# ADR-0013: Opt-in `atomic` parameter for `execute`

- **Status:** Accepted
- **Date:** 2026-11-26
- **Related:** RFC-0001, ADR-0004, ADR-0006, ADR-0007

## Context

ADR-0004 says that the MCP server does not wrap SQL in transactions, because a hanging transaction is dangerous. RFC-0001 shows that through Adminer a transaction cannot outlive one HTTP request: Adminer rolls back an unfinished transaction and closes the connection at the end of every request. The user may need several changes to be all-or-nothing.

## Decision

- `execute` gets an optional parameter `atomic: bool = false`.
- With `atomic=true`, the MCP server sends `BEGIN;` + agent SQL + `COMMIT;` in one request.
- The agent SQL must not contain transaction-control statements (`BEGIN`, `START TRANSACTION`, `COMMIT`, `END`, `ROLLBACK`, `SAVEPOINT`, `RELEASE`). Otherwise `PolicyError`.
- On error, `error_stops` stops the run and Adminer rolls everything back. The result reports `outcome: rolled_back` and the failed statement.
- In dialects without transactional DDL, DDL is rejected with `atomic=true`. PostgreSQL allows it.
- The default (`atomic=false`) keeps ADR-0004 unchanged. This ADR is an explicit opt-in exception, not a replacement of ADR-0004.

## Alternatives considered

- **Leave atomicity to explicit `BEGIN … COMMIT` written by the agent** – works, but the agent can forget `COMMIT` or add an inner `COMMIT`.
- **`atomic=true` as the default** – changes the semantics for all calls and contradicts ADR-0004.

## Consequences

- The transaction holds locks for the duration of the request. Long batches can block other sessions until the request ends.
- The MCP server removes the results of its own `BEGIN`/`COMMIT` from the output, so statement indexes match the agent SQL.
