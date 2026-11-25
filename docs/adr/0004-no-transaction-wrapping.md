# ADR-0004: The MCP server does not wrap queries in transactions

- **Status:** Accepted
- **Date:** 2026-11-25
- **Related:** ADR-0005, ADR-0006, [sql-safety.md](../sql-safety.md)

## Context

The original proposal was to wrap read queries in `START TRANSACTION READ ONLY; …; ROLLBACK;`, so that the database itself rejects writes. The project owner does not want the MCP server to work with transactions: an open transaction that stays open is dangerous, especially in PostgreSQL (it holds locks and blocks VACUUM).

The Adminer 6.3.1 source code (`adminer/sql.inc.php`) also shows that:

- each statement runs separately in autocommit,
- at the end of every request Adminer calls `driver()->rollback()`, so it rolls back an unfinished transaction itself and prints `ROLLBACK -- Adminer`.

## Decision

The MCP server does not add `BEGIN`, `START TRANSACTION`, `COMMIT` or `ROLLBACK` to the agent's SQL. Read protection comes from the classifier (ADR-0006) and optionally from a session setting (ADR-0005) that does not open a transaction.

The agent may write `BEGIN … COMMIT` itself in `execute` if it needs several statements to be atomic. This works only within one call.

## Alternatives considered

- **Read-only transaction around `query`** – rejected by the owner. The risk of a hanging transaction is small thanks to the Adminer rollback, but the MCP server would depend on behavior that Adminer can change.
- **Transaction around `execute` with rollback on error** – it would change the semantics (autocommit) and add the same risk.

## Consequences

- `execute` with several statements can stop halfway (earlier statements stay applied). The tool description tells the agent this.
- The classifier is the main protection of `query`. It is therefore strict and rejects SQL it does not understand.
