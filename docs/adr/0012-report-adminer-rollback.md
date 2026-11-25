# ADR-0012: Detect and report the automatic Adminer rollback

- **Status:** Accepted
- **Date:** 2026-11-26
- **Related:** RFC-0001, ADR-0004, [adminer-protocol.md](../adminer-protocol.md#processing)

## Context

If the agent writes `BEGIN` in `execute` and forgets `COMMIT`, or a statement fails inside a transaction, Adminer rolls the transaction back at the end of the request. The only sign is a `pre` block `ROLLBACK -- Adminer` without an `id`. The statements before it are reported as "Query executed OK, N rows affected", so the agent can think that the change is saved when it is not.

## Decision

- The parser recognizes the `ROLLBACK -- Adminer` block (also `ROLLBACK TRANSACTION -- Adminer`) and reports it separately from the statement results.
- The result of `execute` contains `transaction.outcome = "rolled_back_by_adminer"` and a warning that no change from the transaction was saved.
- The classifier marks the statements that ran inside a transaction, so that the warning can name them.
- This is part of the MVP.

## Alternatives considered

- **Reject `BEGIN` without `COMMIT` before execution** – the agent may want exactly that (to test a statement and roll back). With RFC-0001, `dry_run` covers that case better, but rejection would still break valid SQL.
- **Ignore it** – the agent gets misleading results. Rejected.

## Consequences

- The parser must accept a `pre` block without `id` after the statement blocks.
- The marker depends on the `inTransaction()` implementation of the driver. For PostgreSQL it is reliable (`pg_transaction_status`). Each new dialect must verify it.
