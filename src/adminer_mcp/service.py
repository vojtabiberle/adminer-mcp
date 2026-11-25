"""Tool logic without the MCP layer (docs/tools.md)."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from adminer_mcp.client import AdminerClient
from adminer_mcp.config import Config, Profile
from adminer_mcp.dialects import Dialect, get_dialect
from adminer_mcp.errors import AdminerMcpError, PolicyError, UnsupportedError
from adminer_mcp.models import (
    Affected,
    Binary,
    Cell,
    Empty,
    ResultSet,
    SqlPage,
    Statement,
    StatementError,
)
from adminer_mcp.safety import (
    Classification,
    ExecuteMode,
    check_execute,
    check_query,
    classify,
)

logger = logging.getLogger(__name__)

SHUTDOWN_TIMEOUT = 10.0
"""Upper bound for logging out all profiles on shutdown (each logout has its own 5 s timeout)."""

MAX_OUTPUT_CHARS = 200_000
"""Approximate upper bound for the rows of one tool result."""


@dataclass(frozen=True)
class Target:
    profile: Profile
    dialect: Dialect
    database: str | None
    schema: str | None


class AdminerService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._clients: dict[str, AdminerClient] = {}

    # -- plumbing -------------------------------------------------------------

    def _client(self, profile: Profile) -> AdminerClient:
        client = self._clients.get(profile.name)
        if client is None:
            client = AdminerClient(profile, get_dialect(profile.driver))
            self._clients[profile.name] = client
        return client

    def _target(self, profile: str | None, database: str | None, schema: str | None) -> Target:
        resolved = self.config.get(profile)
        dialect = get_dialect(resolved.driver)
        database = database or resolved.database
        schema = schema or resolved.schema_name
        if schema and not dialect.supports_schemas:
            raise UnsupportedError(f"The {dialect.adminer_driver} driver does not support schemas.")
        if schema and not database:
            raise PolicyError("A schema applies only together with a database: set database too.")
        return Target(resolved, dialect, database, schema)

    def _max_rows(self, profile: Profile, max_rows: int | None) -> int:
        if max_rows is None:
            return profile.max_rows
        if max_rows < 1:
            raise PolicyError("max_rows must be at least 1.")
        return min(max_rows, profile.max_rows_limit)

    async def _run(self, target: Target, sql: str, limit: int) -> tuple[SqlPage, list[str]]:
        client = self._client(target.profile)
        page = await client.run_sql(
            sql, database=target.database, schema=target.schema, limit=limit
        )
        warnings = [client.version_warning] if client.version_warning else []
        return page, warnings

    async def _read(
        self, target: Target, sql: str, limit: int
    ) -> tuple[list[Statement], list[str]]:
        """Run read SQL behind the read-only session guard (ADR-0005)."""
        prelude = target.dialect.read_only_prelude()
        page, warnings = await self._run(target, f"{prelude}\n{sql}", limit)
        if not page.statements:
            raise AdminerMcpError("Adminer returned no statement result.")
        guard = page.statements[0].outcome
        if isinstance(guard, StatementError):
            raise AdminerMcpError(f"The read-only guard failed: {guard.message}")
        statements = page.statements[1:]
        for statement in statements:
            statement.index -= 1
        return statements, warnings

    # -- tools ----------------------------------------------------------------

    def list_profiles(self) -> list[dict[str, Any]]:
        result = []
        for profile in self.config.profiles.values():
            client = self._clients.get(profile.name)
            result.append(
                {
                    "name": profile.name,
                    "description": profile.description,
                    "default": profile.name == self.config.default_profile,
                    "driver": profile.driver,
                    "server": profile.server,
                    "username": profile.username,
                    "database": profile.database,
                    "schema": profile.schema_name,
                    "allow_write": profile.allow_write,
                    "allow_ddl": profile.allow_ddl,
                    "state": client.state.value if client else "not_connected",
                    "adminer_version": client.version if client else None,
                }
            )
        return result

    async def list_databases(self, profile: str | None, database: str | None) -> dict[str, Any]:
        target = self._target(profile, database, None)
        rows = await self._metadata(target, target.dialect.sql_list_databases())
        return {"databases": [row[0] for row in rows]}

    async def list_schemas(self, profile: str | None, database: str | None) -> dict[str, Any]:
        target = self._target(profile, database, None)
        if not target.dialect.supports_schemas:
            raise UnsupportedError(f"The {target.dialect.adminer_driver} driver has no schemas.")
        rows = await self._metadata(target, target.dialect.sql_list_schemas())
        return {"database": target.database, "schemas": [row[0] for row in rows]}

    async def list_tables(
        self, profile: str | None, database: str | None, schema: str | None
    ) -> dict[str, Any]:
        target = self._target(profile, database, schema)
        sql = target.dialect.sql_list_tables(target.schema)
        rows = await self._metadata(
            Target(target.profile, target.dialect, target.database, None), sql
        )
        tables = [
            {
                "schema": row[0],
                "name": row[1],
                "type": row[2],
                "estimated_rows": int(row[3]) if isinstance(row[3], str) else None,
                "comment": row[4],
            }
            for row in rows
        ]
        return {"database": target.database, "schema": target.schema, "tables": tables}

    async def describe_table(
        self, table: str, profile: str | None, database: str | None, schema: str | None
    ) -> dict[str, Any]:
        target = self._target(profile, database, schema)
        table_schema = target.schema
        if table_schema is None and "." in table:
            table_schema, table = table.split(".", 1)
        statements = target.dialect.sql_describe_table(table, table_schema)
        plain = Target(target.profile, target.dialect, target.database, None)
        results, _ = await self._read(plain, ";\n".join(statements), limit=10_000)
        rows = [self._rows(result) for result in results]
        if len(rows) != len(statements):
            raise AdminerMcpError("Adminer did not return all parts of the table description.")
        info, columns, indexes, foreign_keys = rows
        if not info:
            where = f"{table_schema}.{table}" if table_schema else table
            raise AdminerMcpError(
                f"Table {where!r} was not found in database {target.database or '(default)'!r}."
            )
        [(found_schema, name, kind, comment)] = info
        index_list = [
            {
                "name": row[0],
                "primary": row[1] == "t",
                "unique": row[2] == "t",
                "columns": json.loads(str(row[3])),
                "definition": row[4],
            }
            for row in indexes
        ]
        primary_key: list[str] = next(
            (index["columns"] for index in index_list if index["primary"]), []
        )
        return {
            "schema": found_schema,
            "name": name,
            "type": kind,
            "comment": comment,
            "columns": [
                {
                    "name": row[0],
                    "type": row[1],
                    "nullable": row[2] == "t",
                    "default": row[3],
                    "comment": row[4],
                }
                for row in columns
            ],
            "primary_key": primary_key,
            "indexes": [
                {key: value for key, value in index.items() if key != "primary"}
                for index in index_list
            ],
            "foreign_keys": [
                {
                    "name": row[0],
                    "columns": json.loads(str(row[1])),
                    "references": {
                        "schema": row[2],
                        "table": row[3],
                        "columns": json.loads(str(row[4])),
                    },
                }
                for row in foreign_keys
            ],
        }

    async def _metadata(self, target: Target, sql: str) -> list[list[Cell]]:
        [statement], _ = await self._read(target, sql, limit=100_000)
        return self._rows(statement)

    def _rows(self, statement: Statement) -> list[list[Cell]]:
        outcome = statement.outcome
        if isinstance(outcome, StatementError):
            raise AdminerMcpError(f"Metadata query failed: {outcome.message}")
        if isinstance(outcome, ResultSet):
            return outcome.rows
        return []

    async def query(
        self,
        sql: str,
        profile: str | None,
        database: str | None,
        schema: str | None,
        max_rows: int | None,
    ) -> dict[str, Any]:
        target = self._target(profile, database, schema)
        classification = classify(sql, target.dialect)
        check_query(classification, target.dialect)
        limit = self._max_rows(target.profile, max_rows)
        statements, warnings = await self._read(target, sql, limit)
        return self._result(target, statements, len(classification.statements), limit, warnings)

    async def explain(
        self,
        sql: str,
        analyze: bool,
        profile: str | None,
        database: str | None,
        schema: str | None,
    ) -> dict[str, Any]:
        target = self._target(profile, database, schema)
        if len(classify(sql, target.dialect).statements) != 1:
            raise PolicyError("explain takes exactly one statement.")
        explain_sql = target.dialect.sql_explain(sql.strip().rstrip(";"), analyze)
        check_query(classify(explain_sql, target.dialect), target.dialect)
        statements, warnings = await self._read(target, explain_sql, limit=10_000)
        return self._result(target, statements, 1, 10_000, warnings)

    async def execute(
        self,
        sql: str,
        allow_full_table: bool,
        atomic: bool,
        dry_run: bool,
        profile: str | None,
        database: str | None,
        schema: str | None,
        max_rows: int | None,
    ) -> dict[str, Any]:
        target = self._target(profile, database, schema)
        classification = classify(sql, target.dialect)
        plan = check_execute(
            classification,
            target.dialect,
            allow_write=target.profile.allow_write,
            allow_ddl=target.profile.allow_ddl,
            allow_full_table=allow_full_table,
            atomic=atomic,
            dry_run=dry_run,
            profile_name=target.profile.name,
        )
        limit = self._max_rows(target.profile, max_rows)
        dialect = target.dialect
        wrapped = plan.mode in (ExecuteMode.ATOMIC, ExecuteMode.DRY_RUN)
        if wrapped:
            end = dialect.commit_sql if plan.mode == ExecuteMode.ATOMIC else dialect.rollback_sql
            # The newline and ';' end the agent SQL even if it ends with a line comment.
            full_sql = f"{dialect.begin_sql}\n{sql}\n;\n{end}"
        else:
            full_sql = sql
        page, warnings = await self._run(target, full_sql, limit)
        statements = page.statements
        count = len(classification.statements)
        if wrapped:
            if not statements or isinstance(statements[0].outcome, StatementError):
                raise AdminerMcpError("The MCP server could not start the transaction.")
            statements = statements[1 : 1 + count]
            for statement in statements:
                statement.index -= 1
        result = self._result(target, statements, count, limit, warnings + plan.warnings)
        result["transaction"] = self._transaction(
            plan.mode, page, statements, classification, count, result["warnings"]
        )
        return result

    def _transaction(
        self,
        mode: ExecuteMode,
        page: SqlPage,
        statements: list[Statement],
        classification: Classification,
        count: int,
        warnings: list[str],
    ) -> dict[str, Any]:
        failed = next((s.index for s in statements if isinstance(s.outcome, StatementError)), None)
        outcome = "none"
        if mode == ExecuteMode.DRY_RUN:
            outcome = "rolled_back" if failed else "dry_run_rolled_back"
            if failed is None:
                warnings.append(
                    "Dry run: the statements ran and were rolled back; nothing was saved."
                )
        elif mode == ExecuteMode.ATOMIC:
            outcome = "rolled_back" if failed or page.adminer_rolled_back else "committed"
            if outcome == "rolled_back":
                warnings.append("The atomic block was rolled back; nothing was saved.")
        elif mode == ExecuteMode.EXPLICIT:
            ran = {s.index for s in statements if not isinstance(s.outcome, StatementError)}
            for statement in classification.statements:
                if statement.index in ran and statement.keyword in ("COMMIT", "END"):
                    outcome = "committed"
                elif statement.index in ran and statement.keyword in ("ROLLBACK", "ABORT"):
                    outcome = "rolled_back"
        if page.adminer_rolled_back and mode in (ExecuteMode.NONE, ExecuteMode.EXPLICIT):
            outcome = "rolled_back_by_adminer"
            warnings.append(
                "Adminer rolled back an unfinished transaction at the end of the request;"
                " no change from the transaction was saved (ADR-0012)."
            )
        if (
            failed is not None
            and failed > 1
            and mode in (ExecuteMode.NONE, ExecuteMode.EXPLICIT)
            and outcome != "rolled_back_by_adminer"
        ):
            warnings.append(
                f"Statements before statement {failed} ran in autocommit and stay applied."
            )
        return {"mode": mode.value, "outcome": outcome, "failed_statement": failed}

    async def reconnect(self, profile: str | None) -> dict[str, Any]:
        resolved = self.config.get(profile)
        client = self._client(resolved)
        await client.reconnect()
        return {
            "profile": resolved.name,
            "state": client.state.value,
            "adminer_version": client.version,
            "warnings": [client.version_warning] if client.version_warning else [],
        }

    async def shutdown(self) -> None:
        """Log out every active session (ADR-0011), then close the HTTP clients."""
        clients = list(self._clients.values())
        await asyncio.gather(*(client.logout() for client in clients), return_exceptions=True)
        await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)
        self._clients.clear()

    # -- output ---------------------------------------------------------------

    def _result(
        self,
        target: Target,
        statements: list[Statement],
        expected: int,
        limit: int,
        warnings: list[str],
    ) -> dict[str, Any]:
        warnings = list(warnings)
        budget = [MAX_OUTPUT_CHARS]
        output = [self._statement(target, s, limit, budget, warnings) for s in statements]
        failed = next((s for s in statements if isinstance(s.outcome, StatementError)), None)
        if failed is not None and failed.index < expected:
            warnings.append(
                f"Statement {failed.index} failed; statements {failed.index + 1}-{expected}"
                " did not run."
            )
        elif failed is None and len(statements) != expected:
            warnings.append(
                f"The MCP server expected {expected} statements but Adminer ran"
                f" {len(statements)}. The statement split may differ; check the results."
            )
            logger.warning(
                "statement count mismatch: expected %s, got %s", expected, len(statements)
            )
        return {
            "profile": target.profile.name,
            "database": target.database,
            "schema": target.schema,
            "statements": output,
            "warnings": warnings,
        }

    def _statement(
        self,
        target: Target,
        statement: Statement,
        limit: int,
        budget: list[int],
        warnings: list[str],
    ) -> dict[str, Any]:
        item: dict[str, Any] = {"index": statement.index, "sql": statement.sql}
        outcome = statement.outcome
        if isinstance(outcome, ResultSet):
            rows, truncated_cells, cut = self._rows_output(outcome.rows, target.profile, budget)
            item.update(
                kind="result_set",
                columns=[
                    {"name": c.name, "type": c.type, "table": c.table} for c in outcome.columns
                ],
                rows=rows,
                returned_rows=len(rows),
                total_rows=outcome.total_rows,
                truncated=len(rows) < outcome.total_rows,
                truncated_cells=truncated_cells,
            )
            if outcome.total_rows > len(rows) and not cut:
                warnings.append(
                    f"Statement {statement.index}: the result had {outcome.total_rows} rows,"
                    f" {len(rows)} returned. max_rows limits only the output: the database and"
                    " Adminer still processed the whole result. Add LIMIT to the SQL (ADR-0010)."
                )
            if cut:
                warnings.append(
                    f"Statement {statement.index}: the output was cut after {len(rows)} rows"
                    f" because it exceeded {MAX_OUTPUT_CHARS} characters."
                )
        elif isinstance(outcome, Empty):
            item.update(kind="empty", columns=None, rows=[], returned_rows=0, total_rows=0)
        elif isinstance(outcome, Affected):
            item.update(kind="affected", affected_rows=outcome.affected_rows)
        elif isinstance(outcome, StatementError):
            item.update(kind="error", error=outcome.message)
        if statement.warnings:
            item["warnings"] = statement.warnings
        return item

    def _rows_output(
        self, rows: list[list[Cell]], profile: Profile, budget: list[int]
    ) -> tuple[list[list[Any]], int, bool]:
        output: list[list[Any]] = []
        truncated_cells = 0
        for row in rows:
            converted = []
            for cell in row:
                value, cut = self._cell(cell, profile.max_cell_chars)
                truncated_cells += cut
                converted.append(value)
            size = sum(len(v) if isinstance(v, str) else 8 for v in converted) + 4 * len(converted)
            if size > budget[0]:
                return output, truncated_cells, True
            budget[0] -= size
            output.append(converted)
        return output, truncated_cells, False

    @staticmethod
    def _cell(cell: Cell, max_chars: int) -> tuple[Any, int]:
        if isinstance(cell, Binary):
            return {"binary_bytes": cell.size}, 0
        if isinstance(cell, str) and len(cell) > max_chars:
            return f"{cell[:max_chars]}… [truncated, {len(cell)} characters]", 1
        return cell, 0
