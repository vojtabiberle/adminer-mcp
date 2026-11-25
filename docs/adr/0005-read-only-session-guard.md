# ADR-0005: Read-only guard at the database session level for the `query` tool

- **Status:** Accepted
- **Date:** 2026-11-26
- **Related:** ADR-0004, ADR-0006

## Context

The classifier (ADR-0006) checks the SQL text. It cannot detect a write hidden in a user function (`SELECT archive_orders()`) or a function with side effects that is missing from the list. Read-only transactions are excluded (ADR-0004).

The database can enforce read-only mode without opening a transaction, through a session setting:

- PostgreSQL: `SET default_transaction_read_only = on;` – each following statement on this connection runs as read-only. A write, `nextval()`, and a write inside a function all fail with `cannot execute … in a read-only transaction`.
- MySQL/MariaDB: `SET SESSION TRANSACTION READ ONLY;` (since MySQL 5.6.5).

Adminer runs all statements of one request on the same connection and closes the connection after the request. The setting therefore affects nothing else.

## Decision

The `query` tool (and `explain` with `analyze`) prepends `dialect.read_only_prelude()` to the agent's SQL; for PostgreSQL it is `SET default_transaction_read_only = on;`. The result of the prelude is removed from the response. The classifier forbids statements in `query` that would turn the guard off (`SET … transaction_read_only`, `SET SESSION CHARACTERISTICS`, `RESET ALL`).

No transaction is opened, so nothing can stay open.

## Alternatives considered

- **Rely on the classifier only** – simpler, but no protection against writes inside functions.
- **Read-only transaction** – rejected in ADR-0004.
- **Read-only database user** – the best solution, but outside the control of the MCP server. Recommended in the documentation, not enforced.

## Consequences

- One extra statement in each `query` request (negligible).
- The prelude changes the number of blocks in the response; the parser must account for it (index shift).
- Temporary tables (`CREATE TEMP TABLE`) will not work in `query`. That is fine; they belong in `execute`.
