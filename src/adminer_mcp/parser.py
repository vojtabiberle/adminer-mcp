"""Stateless parsing of Adminer 6.3.1 HTML responses.

See docs/adminer-protocol.md for the markup this module depends on. Every unknown shape
raises ProtocolError; the parser never silently returns an empty result.
"""

from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from adminer_mcp.errors import ProtocolError
from adminer_mcp.models import (
    Affected,
    Binary,
    Cell,
    Column,
    Empty,
    ResultSet,
    SqlPage,
    Statement,
    StatementError,
)

_ROLLBACK_MARKER = re.compile(r"^ROLLBACK(?: TRANSACTION)? -- Adminer$")
# Adminer formats every number in a message with a thousands separator ("5 / 1,500 rows").
_AFFECTED = re.compile(r"^Query executed OK, ([\d,]+) rows? affected\.")
_NO_ROWS = "No rows."
_FOOTER_ROWS = re.compile(r"^(?:([\d,]+) / )?([\d,]+) rows?\b")
_BINARY = re.compile(r"^([\d,]+) bytes?$")
_LOCKED_OUT = re.compile(r"Too many unsuccessful logins, try again in (\d+) minute")
_ERROR_PREFIX = re.compile(r"^Error in query(?: \([^)]*\))?: ?")

_SQL_PAGE_SELECTOR = 'pre, table.nowrap, p.message, p.error, p.sql-footer, div[id^="warnings-"]'


def _tree(html: str) -> LexborHTMLParser:
    return LexborHTMLParser(html)


def _content(tree: LexborHTMLParser) -> LexborNode:
    content = tree.css_first("#content")
    if content is None:
        raise ProtocolError(f"The response is not an Adminer page: {_excerpt(tree.text())}")
    return content


def _number(text: str) -> int:
    return int(text.replace(",", ""))


def _excerpt(text: str, length: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= length else text[:length] + "…"


def _text_with_breaks(node: LexborNode) -> str:
    """Text of a node where `<br>` becomes a newline."""
    clone = node.clone()
    for br in clone.css("br"):
        br.replace_with("\n")
    return clone.text()


def is_login_page(html: str) -> bool:
    """True if the page contains the Adminer login form."""
    return _tree(html).css_first('input[name="auth[password]"]') is not None


def page_errors(html: str) -> list[str]:
    """Page-level errors (`div.error` in the content), e.g. a login error or an invalid token."""
    content = _content(_tree(html))
    return [_text_with_breaks(node).strip() for node in content.css("div.error")]


def locked_out_minutes(errors: list[str]) -> int | None:
    """Minutes to wait if one of the errors is the Adminer brute-force block."""
    for error in errors:
        match = _LOCKED_OUT.search(error)
        if match:
            return int(match.group(1))
    return None


def is_locked_out(errors: list[str]) -> bool:
    return any(_LOCKED_OUT.search(error) for error in errors)


def extract_token(html: str) -> str | None:
    node = _tree(html).css_first('input[name="token"]')
    if node is None:
        return None
    return node.attributes.get("value")


def extract_version(html: str) -> str | None:
    node = _tree(html).css_first("span.version")
    if node is None:
        return None
    text = node.text(deep=False).strip()
    return text.split()[0] if text else None


def _is_result_table(node: LexborNode, content: LexborNode) -> bool:
    """A result table, not an EXPLAIN table or a nested value table."""
    parent = node.parent
    while parent is not None and parent.mem_id != content.mem_id:
        classes = (parent.attributes.get("class") or "").split()
        if "explain" in classes or parent.tag in ("td", "th"):
            return False
        parent = parent.parent
    return True


def _inside_sql_form(node: LexborNode, content: LexborNode) -> bool:
    parent = node.parent
    while parent is not None and parent.mem_id != content.mem_id:
        if parent.attributes.get("id") == "form":
            return True
        parent = parent.parent
    return False


def _parse_column(th: LexborNode) -> Column:
    name = th.text()
    title = (th.attributes.get("title") or "").strip()
    if not title:
        return Column(name=name, type=None, table=None)
    prefix, _, type_name = title.rpartition(" ")
    table: str | None = None
    if "." in prefix:
        table = prefix.split(".", 1)[0]
    return Column(name=name, type=type_name or None, table=table)


def _parse_cell(td: LexborNode) -> Cell:
    first = td.child
    # A special value is a single <i> element: NULL or "N bytes" of non-UTF-8 binary data.
    # Real text is HTML-escaped, so it never produces an <i> element.
    if first is not None and first.tag == "i" and first.next is None:
        marker = first.text()
        if marker == "NULL":
            return None
        match = _BINARY.match(marker)
        if match:
            return Binary(size=_number(match.group(1)))
    return td.text()


def _parse_table(table: LexborNode) -> ResultSet:
    columns = [_parse_column(th) for th in table.css("thead th")]
    rows: list[list[Cell]] = []
    for tr in table.css("tbody tr"):
        cells = [_parse_cell(td) for td in tr.css("td") if _cell_belongs_to(td, tr)]
        if len(cells) != len(columns):
            raise ProtocolError(
                f"A result row has {len(cells)} cells but the header has {len(columns)} columns."
            )
        rows.append(cells)
    return ResultSet(columns=columns, rows=rows, total_rows=len(rows))


def _cell_belongs_to(td: LexborNode, tr: LexborNode) -> bool:
    """Skip cells of nested tables (document drivers print nested values as tables)."""
    return td.parent is not None and td.parent.mem_id == tr.mem_id


def parse_sql_page(html: str) -> SqlPage:
    """Parse the response of the SQL command page into statements and their outcomes."""
    tree = _tree(html)
    content = _content(tree)
    page = SqlPage()
    current: Statement | None = None

    for node in content.css(_SQL_PAGE_SELECTOR):
        if _inside_sql_form(node, content):
            continue
        tag = node.tag
        classes = (node.attributes.get("class") or "").split()
        node_id = node.attributes.get("id") or ""

        if tag == "pre":
            code = node.text()
            if node_id.startswith("sql-"):
                current = Statement(index=int(node_id[4:]), sql=code)
                page.statements.append(current)
            elif _ROLLBACK_MARKER.match(code.strip()):
                page.adminer_rolled_back = True
            # Any other <pre> without an id is a non-executable comment: ignore it.
            continue

        if tag == "table":
            if not _is_result_table(node, content):
                continue
            _require(current, "a result table").outcome = _parse_table(node)
            continue

        if tag == "div":  # warnings-N
            _require(current, "warnings").warnings = node.text().strip()
            continue

        text = _text_with_breaks(node).strip()

        if "sql-footer" in classes:
            statement = _require(current, "a result footer")
            match = _FOOTER_ROWS.match(text)
            if isinstance(statement.outcome, ResultSet) and match:
                statement.outcome.total_rows = _number(match.group(2))
            continue

        if "message" in classes:
            statement = _require(current, "a message")
            if text.startswith(_NO_ROWS):
                statement.outcome = Empty()
                continue
            affected = _AFFECTED.match(text)
            if affected:
                statement.outcome = Affected(affected_rows=_number(affected.group(1)))
                continue
            raise ProtocolError(
                f"Unknown message for statement {statement.index}: {_excerpt(text)}"
            )

        if "error" in classes:
            if node.css_first('a[href^="#sql-"]') is not None:
                continue  # summary "Error in query: <links>" after several statements
            if current is None:
                raise ProtocolError(f"Error outside of a statement: {_excerpt(text)}")
            current.outcome = StatementError(message=_ERROR_PREFIX.sub("", text))
            continue

    for statement in page.statements:
        if statement.outcome is None:
            raise ProtocolError(
                f"Statement {statement.index} has no result in the response. "
                "Adminer may have stopped (timeout or PHP error)."
            )
    return page


def _require(statement: Statement | None, what: str) -> Statement:
    if statement is None:
        raise ProtocolError(f"Found {what} before any statement in the response.")
    return statement
