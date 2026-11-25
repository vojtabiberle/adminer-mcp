"""SQL classification and policy checks.

See docs/sql-safety.md, ADR-0006, ADR-0007, ADR-0013 and ADR-0014.

The classifier is a guard against agent mistakes, not a security boundary. It splits SQL into
statements with the sqlglot tokenizer, takes the category from the leading keyword, and uses
the sqlglot AST only for details (WHERE, data-modifying CTEs, SELECT INTO, FOR UPDATE,
function calls). sqlglot misparses some statements (e.g. START TRANSACTION as an alias), so a
statement whose leading keyword is not known is `unknown`, the most restricted category.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from enum import StrEnum

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.simplify import simplify
from sqlglot.tokenizer_core import Token, TokenType

from adminer_mcp.dialects.base import Dialect
from adminer_mcp.errors import PolicyError

# sqlglot logs a warning for every statement it parses as a generic Command.
logging.getLogger("sqlglot").setLevel(logging.ERROR)


class Category(StrEnum):
    READ = "read"
    SESSION = "session"
    SESSION_PRIVILEGED = "session_privileged"
    LOCK = "lock"
    TRANSACTION = "transaction"
    DML = "dml"
    DDL = "ddl"
    UNKNOWN = "unknown"


READ_CATEGORIES = frozenset({Category.READ, Category.SESSION})
DDL_CATEGORIES = frozenset({Category.DDL, Category.UNKNOWN})

_READ_KEYWORDS = {"SELECT", "WITH", "VALUES", "TABLE", "SHOW"}
_DML_KEYWORDS = {"INSERT", "UPDATE", "DELETE", "MERGE"}
_TRANSACTION_KEYWORDS = {
    "BEGIN",
    "START",
    "COMMIT",
    "END",
    "ROLLBACK",
    "ABORT",
    "SAVEPOINT",
    "RELEASE",
}
_TRANSACTION_START = {"BEGIN", "START"}
_TRANSACTION_END = {"COMMIT", "END", "ROLLBACK", "ABORT"}
_DDL_KEYWORDS = {
    "CREATE", "ALTER", "DROP", "TRUNCATE", "COMMENT", "GRANT", "REVOKE", "REINDEX", "CLUSTER",
    "VACUUM", "ANALYZE", "ANALYSE", "REFRESH", "CALL", "DO", "SECURITY", "IMPORT", "REASSIGN",
}  # fmt: skip
_EXPLAIN_OPTIONS = {"ANALYZE", "ANALYSE", "VERBOSE"}
# Statements that Adminer handles in its own way; the agent must use the tool parameters.
_REJECTED_ALWAYS = {"DELIMITER": "DELIMITER is not supported."}


@dataclass
class ClassifiedStatement:
    index: int
    """1-based, matches Adminer's `sql-N`."""
    sql: str
    category: Category
    keyword: str
    full_table: list[str] = field(default_factory=list)
    """Tables changed by UPDATE/DELETE/TRUNCATE without a restricting WHERE."""
    functions: set[str] = field(default_factory=set)
    note: str | None = None
    """Why the statement got its category, when it is not obvious."""


@dataclass
class Classification:
    statements: list[ClassifiedStatement]
    parse_error: str | None = None

    def categories(self) -> set[Category]:
        return {statement.category for statement in self.statements}


def _split(sql: str, dialect: Dialect) -> list[tuple[str, list[Token]]]:
    """Split SQL into statements (text and tokens). Comments and empty statements disappear."""
    tokens = _tokenize(sql, dialect)
    statements: list[tuple[str, list[Token]]] = []
    current: list[Token] = []
    for token in [*tokens, None]:
        if token is None or token.token_type == TokenType.SEMICOLON:
            if current:
                statements.append((sql[current[0].start : current[-1].end + 1], current))
            current = []
        else:
            current.append(token)
    return statements


def _words(tokens: list[Token], count: int) -> list[str]:
    return [token.text.upper() for token in tokens[:count]]


def _function_names(tree: exp.Expr) -> set[str]:
    names: set[str] = set()
    for node in tree.find_all(exp.Func):
        if isinstance(node, exp.Anonymous):
            names.add(node.name.lower())
        else:
            names.add(node.sql_name().lower())
    return names


def _is_constant_true(condition: exp.Expr) -> bool:
    simplified = simplify(condition.copy())
    if isinstance(simplified, exp.Boolean):
        return bool(simplified.this)
    if isinstance(simplified, exp.Literal) and simplified.is_number:
        return float(simplified.this) != 0
    return False


def _unrestricted_tables(tree: exp.Expr) -> list[str]:
    tables = []
    for node in tree.find_all(exp.Update, exp.Delete):
        where = node.args.get("where")
        if where is None or _is_constant_true(where.this):
            target = node.this
            tables.append(target.sql() if target is not None else "?")
    return tables


def _has_dml(tree: exp.Expr) -> bool:
    return any(True for _ in tree.find_all(exp.Insert, exp.Update, exp.Delete, exp.Merge))


def _parse(text: str, dialect: Dialect) -> exp.Expr | None:
    try:
        tree = sqlglot.parse_one(text, read=dialect.sqlglot_dialect)
    except SqlglotError:
        return None
    return None if isinstance(tree, exp.Command) else tree


def _classify_set(tokens: list[Token], keyword: str, dialect: Dialect) -> tuple[Category, str]:
    words = _words(tokens, 6)
    rest = [word for word in words[1:] if word not in ("SESSION", "LOCAL")]
    if keyword == "RESET":
        name = rest[0].lower() if rest else ""
        if name == "all" or name in dialect.guarded_settings:
            return Category.SESSION_PRIVILEGED, f"RESET {name}"
        return Category.SESSION, f"RESET {name}"
    if not rest:
        return Category.UNKNOWN, "empty SET"
    first = rest[0]
    if first in ("ROLE", "AUTHORIZATION", "CHARACTERISTICS", "TRANSACTION"):
        return Category.SESSION_PRIVILEGED, f"SET {first}"
    name = first.lower()
    if name in dialect.guarded_settings:
        return Category.SESSION_PRIVILEGED, f"SET {name}"
    return Category.SESSION, f"SET {name}"


def _tokenize(sql: str, dialect: Dialect) -> list[Token]:
    return sqlglot.Dialect.get_or_raise(dialect.sqlglot_dialect).tokenize(sql)


def _classify_explain(index: int, text: str, dialect: Dialect) -> ClassifiedStatement:
    """EXPLAIN reads, but EXPLAIN ANALYZE runs the inner statement, so it gets its category."""
    # The tokenizer returns the rest of a COMMAND statement as one string: tokenize it again.
    rest = text[len("EXPLAIN") :]
    try:
        tokens = _tokenize(rest, dialect)
    except SqlglotError:
        return ClassifiedStatement(
            index, text, Category.UNKNOWN, "EXPLAIN", note="the SQL could not be parsed"
        )
    position = 0
    analyze = False
    if position < len(tokens) and tokens[position].token_type == TokenType.L_PAREN:
        depth = 0
        for offset, token in enumerate(tokens[position:], start=position):
            if token.token_type == TokenType.L_PAREN:
                depth += 1
            elif token.token_type == TokenType.R_PAREN:
                depth -= 1
            elif token.text.upper() in ("ANALYZE", "ANALYSE"):
                analyze = True
            if depth == 0:
                position = offset + 1
                break
    while position < len(tokens) and tokens[position].text.upper() in _EXPLAIN_OPTIONS:
        analyze = analyze or tokens[position].text.upper() in ("ANALYZE", "ANALYSE")
        position += 1
    if position >= len(tokens):
        return ClassifiedStatement(index, text, Category.UNKNOWN, "EXPLAIN", note="empty EXPLAIN")
    inner = _classify_one(index, rest[tokens[position].start :], tokens[position:], dialect)
    if analyze:
        inner.sql = text
        inner.keyword = "EXPLAIN"
        inner.note = f"EXPLAIN ANALYZE runs the statement ({inner.category})"
        return inner
    category = Category.UNKNOWN if inner.category == Category.UNKNOWN else Category.READ
    return ClassifiedStatement(
        index, text, category, "EXPLAIN", functions=inner.functions, note=inner.note
    )


def _classify_one(
    index: int, text: str, tokens: list[Token], dialect: Dialect
) -> ClassifiedStatement:
    keyword = tokens[0].text.upper()

    if tokens[0].token_type == TokenType.BACKSLASH:
        return ClassifiedStatement(
            index,
            text,
            Category.UNKNOWN,
            "\\",
            note="psql meta-command; use the database parameter",
        )
    if keyword in _REJECTED_ALWAYS:
        return ClassifiedStatement(
            index, text, Category.UNKNOWN, keyword, note=_REJECTED_ALWAYS[keyword]
        )
    if keyword == "EXPLAIN":
        return _classify_explain(index, text, dialect)
    if keyword in ("SET", "RESET"):
        category, note = _classify_set(tokens, keyword, dialect)
        return ClassifiedStatement(index, text, category, keyword, note=note)
    if keyword == "LOCK":
        return ClassifiedStatement(index, text, Category.LOCK, keyword)
    if keyword in _TRANSACTION_KEYWORDS:
        if keyword == "START" and _words(tokens, 2) != ["START", "TRANSACTION"]:
            return ClassifiedStatement(index, text, Category.UNKNOWN, keyword)
        return ClassifiedStatement(index, text, Category.TRANSACTION, keyword)
    if keyword in _DDL_KEYWORDS:
        statement = ClassifiedStatement(index, text, Category.DDL, keyword)
        if keyword == "TRUNCATE":
            statement.full_table = [" ".join(token.text for token in tokens[1:]) or "?"]
        return statement
    if keyword == "COPY":
        words = {token.text.upper() for token in tokens}
        if "FROM" in words:
            return ClassifiedStatement(index, text, Category.DML, keyword)
        return ClassifiedStatement(
            index, text, Category.UNKNOWN, keyword, note="COPY TO writes outside the database"
        )

    if keyword in _READ_KEYWORDS or keyword in _DML_KEYWORDS:
        if keyword == "SHOW":
            return ClassifiedStatement(index, text, Category.READ, keyword)
        tree = _parse(text, dialect)
        if tree is None:
            return ClassifiedStatement(
                index, text, Category.UNKNOWN, keyword, note="the SQL could not be parsed"
            )
        statement = ClassifiedStatement(index, text, Category.READ, keyword)
        statement.functions = _function_names(tree)
        statement.full_table = _unrestricted_tables(tree)
        if keyword in _DML_KEYWORDS or _has_dml(tree):
            statement.category = Category.DML
            if keyword not in _DML_KEYWORDS:
                statement.note = "data-modifying CTE"
        elif any(select.args.get("into") for select in tree.find_all(exp.Select)):
            statement.category = Category.DML
            statement.note = "SELECT INTO creates a table"
        elif any(select.args.get("locks") for select in tree.find_all(exp.Select)):
            statement.category = Category.LOCK
            statement.note = "SELECT … FOR UPDATE/SHARE takes row locks"
        return statement

    return ClassifiedStatement(index, text, Category.UNKNOWN, keyword)


def classify(sql: str, dialect: Dialect) -> Classification:
    try:
        parts = _split(sql, dialect)
    except SqlglotError as error:
        return Classification(statements=[], parse_error=str(error))
    statements = [
        _classify_one(index, text, tokens, dialect)
        for index, (text, tokens) in enumerate(parts, start=1)
    ]
    return Classification(statements=statements)


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _describe(statement: ClassifiedStatement) -> str:
    text = " ".join(statement.sql.split())
    if len(text) > 80:
        text = text[:80] + "…"
    note = f" ({statement.note})" if statement.note else ""
    return f"statement {statement.index} [{statement.category}{note}]: {text}"


def check_query(classification: Classification, dialect: Dialect) -> None:
    """Raise PolicyError unless every statement only reads (rules for the `query` tool)."""
    if classification.parse_error:
        raise PolicyError(
            f"The SQL could not be split into statements: {classification.parse_error}"
        )
    if not classification.statements:
        raise PolicyError("The SQL contains no statement.")
    problems = []
    for statement in classification.statements:
        if statement.category not in READ_CATEGORIES:
            problems.append(_describe(statement))
            continue
        forbidden = sorted(
            name for name in statement.functions if _matches(name, dialect.side_effect_functions)
        )
        if forbidden:
            problems.append(
                f"statement {statement.index} calls functions with side effects:"
                f" {', '.join(forbidden)}"
            )
    if problems:
        raise PolicyError(
            "The query tool runs only read statements. Rejected: "
            + "; ".join(problems)
            + ". Use the execute tool for statements that change data or session state."
        )


class ExecuteMode(StrEnum):
    NONE = "none"
    EXPLICIT = "explicit"
    ATOMIC = "atomic"
    DRY_RUN = "dry_run"


@dataclass
class ExecutePlan:
    mode: ExecuteMode
    warnings: list[str]


def check_execute(
    classification: Classification,
    dialect: Dialect,
    *,
    allow_write: bool,
    allow_ddl: bool,
    allow_full_table: bool,
    atomic: bool,
    dry_run: bool,
    profile_name: str,
) -> ExecutePlan:
    """Raise PolicyError if `execute` must not run the SQL; return the mode and warnings."""
    if not allow_write:
        raise PolicyError(f"Profile {profile_name!r} is read only (allow_write = false).")
    if classification.parse_error:
        raise PolicyError(
            f"The SQL could not be split into statements: {classification.parse_error}"
        )
    statements = classification.statements
    if not statements:
        raise PolicyError("The SQL contains no statement.")
    if atomic and dry_run:
        raise PolicyError("Use either atomic or dry_run, not both: dry_run is already atomic.")
    if dry_run and not dialect.supports_dry_run:
        raise PolicyError(f"dry_run is not supported for the {dialect.adminer_driver} driver.")

    problems: list[str] = []
    for statement in statements:
        if statement.keyword in ("\\", *_REJECTED_ALWAYS):
            problems.append(_describe(statement))
        elif statement.category in DDL_CATEGORIES and not allow_ddl:
            problems.append(
                f"{_describe(statement)} needs allow_ddl = true in profile {profile_name!r}"
            )
        if statement.full_table and not allow_full_table:
            tables = ", ".join(statement.full_table)
            problems.append(
                f"statement {statement.index} changes every row of {tables} (no restricting WHERE);"
                " add a WHERE condition, or set allow_full_table = true if this is intended"
            )
        if (atomic or dry_run) and statement.category == Category.TRANSACTION:
            problems.append(
                f"statement {statement.index} controls the transaction ({statement.keyword});"
                " atomic and dry_run manage the transaction themselves"
            )
        if (
            (atomic or dry_run)
            and statement.category in DDL_CATEGORIES
            and not dialect.transactional_ddl
        ):
            problems.append(f"statement {statement.index} is DDL, which commits implicitly here")
        if dry_run:
            lasting = sorted(
                name
                for name in statement.functions
                if _matches(name, dialect.non_rollbackable_functions)
            )
            if lasting:
                problems.append(
                    f"statement {statement.index} calls functions whose effects a rollback does"
                    f" not undo: {', '.join(lasting)}"
                )
    if problems:
        raise PolicyError("Rejected: " + "; ".join(problems) + ".")

    warnings: list[str] = []
    mode = ExecuteMode.DRY_RUN if dry_run else ExecuteMode.ATOMIC if atomic else ExecuteMode.NONE
    if mode == ExecuteMode.NONE and Category.TRANSACTION in classification.categories():
        mode = ExecuteMode.EXPLICIT
        open_transaction = False
        for statement in statements:
            if statement.category != Category.TRANSACTION:
                continue
            if statement.keyword in _TRANSACTION_START:
                open_transaction = True
            elif statement.keyword in _TRANSACTION_END:
                open_transaction = False
        if open_transaction:
            warnings.append(
                "The SQL starts a transaction without COMMIT. Adminer rolls it back at the end of"
                " the request, so its changes will not be saved."
            )
    return ExecutePlan(mode=mode, warnings=warnings)
