# RFC-0001: Transactions within one request

- **Status:** Implemented
- **Author:** Vojtěch Biberle
- **Date:** 2026-11-26
- **Follows:** PRD-0001
- **Resulting ADRs:** ADR-0012, ADR-0013, ADR-0014

## Summary

Through Adminer, a transaction can exist only within one HTTP request, so only within one call of `execute`. This RFC proposes three features inside that limit:

1. detect and report when Adminer rolled back an unfinished transaction (ADR-0012),
2. an opt-in `atomic` parameter: the MCP server wraps the SQL in `BEGIN … COMMIT` (ADR-0013),
3. an opt-in `dry_run` parameter: the MCP server wraps the SQL in `BEGIN … ROLLBACK` and returns what would change (ADR-0014).

The default behavior from ADR-0004 does not change: without these parameters, the MCP server does not add transaction statements.

## Motivation

The user may need several changes to be applied all together or not at all, and may want to see the effect of a change before it is applied. ADR-0004 forbids wrapping by default because of the risk of a hanging transaction. That risk does not exist through Adminer (see below), so an explicit opt-in is safe.

## Facts from Adminer 6.3.1

Source: `adminer/sql.inc.php`, `adminer/drivers/pgsql.inc.php`.

- All statements of one request run on one connection. After the request, the connection closes (`pg_connect` with `PGSQL_CONNECT_FORCE_NEW`, no persistent connections).
- After the last statement, Adminer calls `connection()->inTransaction()` and then `driver()->rollback()`. If a transaction was open, it prints `<pre><code class='jush-pgsql'>ROLLBACK -- Adminer</code></pre>` (`ROLLBACK TRANSACTION -- Adminer` for MS SQL). This `pre` has no `id`, unlike the `pre id='sql-N'` statement blocks.
- For PostgreSQL, `inTransaction()` uses `pg_transaction_status` and is true for `INTRANS` and `INERROR`. The marker is therefore reliable for PostgreSQL.
- With `error_stops=1`, the first error ends the loop, and the final rollback still runs. A failed statement inside `BEGIN …` rolls back the whole transaction.
- Consequence: a transaction cannot live longer than one HTTP request. A transaction across several tool calls is not possible.

## Proposal

### 1. Report the Adminer rollback (ADR-0012)

- The parser recognizes the `ROLLBACK -- Adminer` block (a `pre` without `id` whose `code` text matches `^ROLLBACK( TRANSACTION)? -- Adminer$`) and sets `adminer_rolled_back = true`.
- The classifier also checks the SQL statically for a `BEGIN`/`START TRANSACTION` without a following `COMMIT`/`END`. It does not reject such SQL, because it can be intentional; it adds a warning to the result.
- The result of `execute` gets a `transaction` object (see below). If Adminer rolled back, the result contains the warning "Adminer rolled back an unfinished transaction at the end of the request; no change from the transaction was saved" and lists which statements ran inside the transaction.

### 2. `execute(…, atomic=true)` (ADR-0013)

- The MCP server sends `BEGIN;` + agent SQL + `COMMIT;`.
- The agent SQL must not contain transaction-control statements (`BEGIN`, `START TRANSACTION`, `COMMIT`, `END`, `ROLLBACK`, `SAVEPOINT`, `RELEASE`). Otherwise `PolicyError`, because an inner `COMMIT` would end the atomic block early.
- On error, `error_stops` stops the run and Adminer rolls back everything. The result says `outcome: rolled_back` and which statement failed.
- All other rules stay the same (`allow_write`, `allow_ddl`, WHERE guard).
- Dialects whose DDL commits implicitly (MySQL, Oracle) reject DDL with `atomic=true`. PostgreSQL has transactional DDL, so it allows it.

### 3. `execute(…, dry_run=true)` (ADR-0014)

- The MCP server sends `BEGIN;` + agent SQL + `ROLLBACK;`.
- The result is the same as for a real run (affected rows, `RETURNING` rows), with `outcome: dry_run_rolled_back`.
- The same check of transaction-control statements as for `atomic`. This is critical: an inner `COMMIT` would save the "dry run".
- `dry_run` and `atomic` cannot be combined (`dry_run` is already atomic).
- `allow_write` (and `allow_ddl` for DDL) is still required: a dry run really executes the statements, takes locks and needs the privileges.
- The WHERE guard still applies; with `allow_full_table=true` the agent can preview a full-table change.
- Statements with effects that a rollback does not undo are rejected or warned about per dialect. For PostgreSQL:
  - rejected: `nextval`, `setval`, `dblink*`, `pg_terminate_backend`, `pg_cancel_backend`, session-level `pg_advisory_lock*`, `COPY … TO` a file or program, `pg_file_write`, `ALTER SYSTEM`;
  - fail by themselves inside a transaction block (no special handling): `VACUUM`, `CREATE DATABASE`, `CREATE INDEX CONCURRENTLY`, `REINDEX CONCURRENTLY`;
  - cannot be detected: side effects in triggers (e.g. `dblink`, `http` extensions). The tool description says so.
- Dialects without transactional DDL reject DDL with `dry_run=true`. Dialects or storage engines without transactions (e.g. MySQL MyISAM) cannot be detected from SQL; for those dialects, `dry_run` is not enabled until a separate RFC handles it.

### Output change

`execute` gets a `transaction` field:

```json
"transaction": {
  "mode": "none | explicit | atomic | dry_run",
  "outcome": "none | committed | rolled_back | dry_run_rolled_back | rolled_back_by_adminer",
  "failed_statement": 3
}
```

- `none` – no transaction statements; every statement ran in autocommit.
- `explicit` – the agent wrote `BEGIN … COMMIT` itself.
- `rolled_back_by_adminer` – Adminer printed `ROLLBACK -- Adminer` (ADR-0012), in any mode.
- `failed_statement` – index of the failed statement, if any.

Statement indexes in the output refer to the agent SQL. The MCP server removes the results of the statements it added (`BEGIN`, `COMMIT`, `ROLLBACK`).

### Dialect interface

`Dialect` gets:

| Member | PostgreSQL |
|---|---|
| `transactional_ddl: bool` | `True` |
| `supports_dry_run: bool` | `True` |
| `begin_sql`, `commit_sql`, `rollback_sql` | `BEGIN;`, `COMMIT;`, `ROLLBACK;` |
| `non_rollbackable_functions` | list above |

## Alternatives considered

- **Transactions across several tool calls** (the MCP server buffers statements and replays them in each call, with `COMMIT` at the end) – statements run again in every step, `now()` and sequences change between replays, and data can change between steps, so the commit may differ from what the agent saw. Rejected.
- **Only point 1 (explicit `BEGIN … COMMIT` by the agent)** – works, but the agent can forget `COMMIT` or write an inner `COMMIT` by mistake. `atomic` removes this class of errors.
- **`dry_run` through `EXPLAIN`** – shows only estimates, not the real affected rows or `RETURNING` data.

## Impact

- **Compatibility:** new optional parameters and a new output field. Without them, behavior stays as in ADR-0004.
- **Security:** `dry_run` changes nothing in the data, but it holds row locks and, for DDL, `ACCESS EXCLUSIVE` locks for the duration of the request, and it uses the full write privileges. The tool description must say this.
- **Load:** a dry run costs the same as a real run.

## Testing

- Parser fixtures: `ROLLBACK -- Adminer` after `BEGIN` without `COMMIT`; after an error inside a transaction; no marker after `COMMIT`.
- Classifier: inner `COMMIT`/`ROLLBACK`/`SAVEPOINT` with `atomic` and `dry_run` → rejected; `nextval` with `dry_run` → rejected.
- Integration (PostgreSQL):
  - `atomic`: second statement fails → the first change is not saved;
  - `dry_run`: `UPDATE … RETURNING` returns rows, and a follow-up `query` shows unchanged data;
  - `dry_run` with `CREATE TABLE` → the table does not exist afterwards;
  - explicit `BEGIN` without `COMMIT` → `rolled_back_by_adminer` and the data is unchanged.

## Rollout plan

1. ADR-0012, ADR-0013 and ADR-0014 as part of the MVP (accepted before the implementation of `execute`).
3. After implementation, update [tools.md](../tools.md), [sql-safety.md](../sql-safety.md) and [architecture.md](../architecture.md).

## Open questions

None. Resolved: `dry_run` does not downgrade the WHERE guard. A full-table dry run still locks and processes the whole table; `allow_full_table=true` is a conscious choice.
