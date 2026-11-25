# ADR-0014: Opt-in `dry_run` parameter for `execute`

- **Status:** Accepted
- **Date:** 2026-11-26
- **Related:** RFC-0001, ADR-0004, ADR-0006, ADR-0007, ADR-0013

## Context

Before a write, the agent and the user want to know what it will do: how many rows change and which ones. `EXPLAIN` gives only estimates. Through Adminer, a statement can run inside `BEGIN … ROLLBACK` in one request, which gives real results without saving them (RFC-0001).

## Decision

- `execute` gets an optional parameter `dry_run: bool = false`.
- With `dry_run=true`, the MCP server sends `BEGIN;` + agent SQL + `ROLLBACK;` and returns the same result as a real run (affected rows, `RETURNING` rows) with `outcome: dry_run_rolled_back`.
- Transaction-control statements in the agent SQL are rejected (an inner `COMMIT` would save the changes).
- `dry_run` cannot be combined with `atomic`.
- `allow_write`, `allow_ddl` and the WHERE guard apply as for a real run.
- Statements whose effects a rollback does not undo are rejected per dialect. For PostgreSQL: `nextval`, `setval`, `dblink*`, `pg_terminate_backend`, `pg_cancel_backend`, session-level `pg_advisory_lock*`, `COPY … TO` a file or program, `pg_file_write`, `ALTER SYSTEM`.
- `dry_run` is enabled only for dialects with `supports_dry_run`. In the MVP that is PostgreSQL. Dialects without transactional DDL reject DDL with `dry_run`.

## Alternatives considered

- **Dry run through `EXPLAIN`** – estimates only, no real rows.
- **Dry run as a `SELECT count(*)` with the same WHERE** – works only for simple `UPDATE`/`DELETE`, not for `INSERT … SELECT`, CTEs, triggers or DDL.

## Consequences

- A dry run executes the statements: it costs the same as a real run, holds row locks, and for DDL holds `ACCESS EXCLUSIVE` locks for the duration of the request. On production this can block other sessions.
- Side effects in triggers (e.g. calls through `dblink` or `http` extensions) cannot be detected and are not rolled back. The tool description must say so.
- A dry run result shows the state at the moment of the request. A later real run can affect different rows if the data changes in between.
