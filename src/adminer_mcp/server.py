"""MCP tool registration (docs/tools.md). The logic is in service.py."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Annotated, Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from adminer_mcp import __version__
from adminer_mcp.config import Config
from adminer_mcp.errors import AdminerMcpError
from adminer_mcp.service import SHUTDOWN_TIMEOUT, AdminerService

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Access to databases through the Adminer web interface. Each profile is one Adminer login.
Start with list_profiles. Pass `database` (and `schema`) on every call: the tools do not
remember them. Use `query` to read and `execute` to change data. max_rows limits only the
returned rows, not the work on the server: write LIMIT in your SQL for large tables.
"""

Profile = Annotated[
    str | None,
    Field(description="Profile name from list_profiles. Default: the default profile."),
]
Database = Annotated[
    str | None,
    Field(description="Database for this call. Default: the database of the profile, if any."),
]
Schema = Annotated[
    str | None,
    Field(
        description="Schema for this call (PostgreSQL). Needs a database. Default: the schema of"
        " the profile, else the default search_path of the user."
    ),
]
MaxRows = Annotated[
    int | None,
    Field(
        description="Rows returned per statement. Default and maximum come from the profile."
        " This limits only the output; write LIMIT in the SQL to limit the work on the server.",
        ge=1,
    ),
]

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


async def _call[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except AdminerMcpError as error:
        raise ToolError(str(error)) from error


def create_server(config: Config, service: AdminerService | None = None) -> MCPServer[Any]:
    if service is None:
        service = AdminerService(config)
    svc: AdminerService = service

    @asynccontextmanager
    async def lifespan(_server: MCPServer[Any]) -> AsyncIterator[None]:
        try:
            yield
        finally:
            # Shielded: on SIGTERM the server is cancelled, but the logout must still run.
            with anyio.CancelScope(shield=True), anyio.move_on_after(SHUTDOWN_TIMEOUT):
                await svc.shutdown()

    mcp: MCPServer[Any] = MCPServer(
        name="adminer",
        version=__version__,
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
    )

    @mcp.tool(annotations=READ_ONLY)
    def list_profiles() -> list[dict[str, Any]]:
        """List the configured Adminer profiles (no passwords).

        Shows the driver, server, user, default database and schema, whether the profile allows
        writes (allow_write) and DDL (allow_ddl), and the connection state.
        """
        return svc.list_profiles()

    @mcp.tool(annotations=READ_ONLY)
    async def list_databases(profile: Profile = None, database: Database = None) -> dict[str, Any]:
        """List the databases on the server of the profile."""
        return await _call(svc.list_databases(profile, database))

    @mcp.tool(annotations=READ_ONLY)
    async def list_schemas(profile: Profile = None, database: Database = None) -> dict[str, Any]:
        """List the non-system schemas in a database (PostgreSQL)."""
        return await _call(svc.list_schemas(profile, database))

    @mcp.tool(annotations=READ_ONLY)
    async def list_tables(
        profile: Profile = None, database: Database = None, schema: Schema = None
    ) -> dict[str, Any]:
        """List tables and views with type, estimated row count (from statistics) and comment.

        Without a schema, the tables of all non-system schemas are listed.
        """
        return await _call(svc.list_tables(profile, database, schema))

    @mcp.tool(annotations=READ_ONLY)
    async def describe_table(
        table: Annotated[str, Field(description="Table name, optionally as schema.table.")],
        profile: Profile = None,
        database: Database = None,
        schema: Schema = None,
    ) -> dict[str, Any]:
        """Describe a table: columns (type, nullable, default, comment), primary key, indexes
        and foreign keys."""
        return await _call(svc.describe_table(table, profile, database, schema))

    @mcp.tool(annotations=READ_ONLY)
    async def query(
        sql: Annotated[str, Field(description="One or more read statements.")],
        profile: Profile = None,
        database: Database = None,
        schema: Schema = None,
        max_rows: MaxRows = None,
    ) -> dict[str, Any]:
        """Run read-only SQL (SELECT, WITH, VALUES, TABLE, SHOW, EXPLAIN, SET of session
        parameters) and return the rows.

        Anything that is not a read is rejected before it is sent; use `execute` for changes.
        The SQL runs in a read-only database session, so a write hidden in a function fails.
        Values are strings as the database prints them; NULL is null; a binary value that is
        not UTF-8 is {"binary_bytes": N}. A result without rows has no column names.
        max_rows limits only the returned rows: the database and Adminer still process the
        whole result, so write LIMIT in the SQL. You are responsible for LIMIT; the server does
        not add it, it only warns.
        """
        return await _call(svc.query(sql, profile, database, schema, max_rows))

    @mcp.tool(annotations=READ_ONLY)
    async def explain(
        sql: Annotated[str, Field(description="One statement to explain.")],
        analyze: Annotated[
            bool,
            Field(description="Run the statement and show real times. Only for read statements."),
        ] = False,
        profile: Profile = None,
        database: Database = None,
        schema: Schema = None,
    ) -> dict[str, Any]:
        """Show the query plan of one statement (EXPLAIN, optionally EXPLAIN ANALYZE)."""
        return await _call(svc.explain(sql, analyze, profile, database, schema))

    @mcp.tool()
    async def reconnect(profile: Profile = None) -> dict[str, Any]:
        """Log out, forget a failed login and log in again once.

        Use it after the password or the profile was fixed, or when the state is auth_failed.
        Adminer blocks an IP address after 30 failed logins, so do not call it in a loop.
        """
        return await _call(svc.reconnect(profile))

    if config.any_write:

        @mcp.tool(
            annotations=ToolAnnotations(
                read_only_hint=False,
                destructive_hint=True,
                idempotent_hint=False,
                open_world_hint=False,
            )
        )
        async def execute(
            sql: Annotated[str, Field(description="One or more statements.")],
            allow_full_table: Annotated[
                bool,
                Field(
                    description="Confirm that UPDATE/DELETE without a restricting WHERE (or"
                    " TRUNCATE) is intended."
                ),
            ] = False,
            atomic: Annotated[
                bool,
                Field(description="Run all statements in one transaction: all or nothing."),
            ] = False,
            dry_run: Annotated[
                bool,
                Field(
                    description="Run the statements in a transaction and roll it back: shows"
                    " affected rows and RETURNING data, saves nothing."
                ),
            ] = False,
            profile: Profile = None,
            database: Database = None,
            schema: Schema = None,
            max_rows: MaxRows = None,
        ) -> dict[str, Any]:
            """Run statements that change data: INSERT, UPDATE, DELETE, MERGE (DDL only if the
            profile has allow_ddl).

            Only for profiles with allow_write. Without atomic or dry_run, each statement is
            committed immediately (autocommit): if a statement fails, the earlier ones stay
            applied and the later ones do not run. UPDATE/DELETE without a restricting WHERE
            is rejected unless allow_full_table is true.
            atomic=true wraps the SQL in BEGIN … COMMIT. dry_run=true wraps it in
            BEGIN … ROLLBACK: the statements really run (they take locks and cost the same as a
            real run) but nothing is saved; side effects in triggers are not undone. Do not
            write BEGIN/COMMIT yourself with atomic or dry_run. A transaction never lasts
            longer than one call: Adminer rolls back an unfinished transaction at the end of
            the request, and the result then says rolled_back_by_adminer.
            max_rows limits only the returned rows (RETURNING); write LIMIT where it matters.
            """
            return await _call(
                svc.execute(
                    sql, allow_full_table, atomic, dry_run, profile, database, schema, max_rows
                )
            )

    return mcp
