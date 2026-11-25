# SQL safety

Decisions: [ADR-0004](adr/0004-no-transaction-wrapping.md), [ADR-0005](adr/0005-read-only-session-guard.md), [ADR-0006](adr/0006-sql-classifier-and-write-gate.md), [ADR-0007](adr/0007-ddl-separate-flag.md).

## Principle

Protection has three layers. None of them is enough alone:

1. **Privileges of the database user.** The only truly reliable layer. For production, a user with read-only privileges is recommended.
2. **Classifier in the MCP server** (`safety.py`). Decides before sending whether the SQL may pass through a given tool. It is a guard against agent mistakes, not a security boundary against an attacker.
3. **Read-only session** for `query` (ADR-0005). The database itself rejects a write, even if the classifier does not detect it (e.g. a write inside a user function).

## Statement categories

The classifier splits the SQL into statements (`sqlglot.parse` with the profile dialect) and assigns each one a category:

| Category | Examples | `query` | `execute` |
|---|---|---|---|
| `read` | `SELECT`, `WITH … SELECT` without data-modifying CTEs, `VALUES`, `TABLE t`, `SHOW`, `EXPLAIN` (without `ANALYZE` of a non-read statement) | yes | yes |
| `session` | `SET`/`RESET` of common parameters (`search_path`, `statement_timeout`, `work_mem`, …) | yes | yes |
| `session_privileged` | `SET ROLE`, `SET SESSION AUTHORIZATION`, `SET [default_]transaction_read_only`, `SET SESSION CHARACTERISTICS` | no | yes |
| `lock` | `SELECT … FOR UPDATE/SHARE`, `LOCK TABLE` | no | yes |
| `transaction` | `BEGIN`, `START TRANSACTION`, `COMMIT`, `ROLLBACK`, `SAVEPOINT` | no | yes |
| `dml` | `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `SELECT … INTO`, `COPY … FROM`, CTEs with `INSERT/UPDATE/DELETE` | no | with `allow_write` |
| `ddl` | `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `COMMENT`, `GRANT`, `REVOKE`, `REINDEX`, `CLUSTER`, `VACUUM`, `ANALYZE`, `REFRESH MATERIALIZED VIEW`, `CALL`, `DO` | no | with `allow_write` and `allow_ddl` |
| `unknown` | anything sqlglot cannot parse (returns `exp.Command`) or does not know, meta-commands (`\connect`), `DELIMITER` | no | same as `ddl` |

`CALL` and `DO` are in `ddl` because they run arbitrary code.

## Rules for the `query` tool

- All statements must be `read` or `session`. Otherwise `PolicyError` with the list of statements and categories and the hint "use `execute`".
- **Functions with side effects** in an otherwise read query are forbidden (best effort, list per dialect). For PostgreSQL: `pg_terminate_backend`, `pg_cancel_backend`, `pg_reload_conf`, `pg_rotate_logfile`, `pg_advisory_*lock*`, `nextval`, `setval`, `set_config`, `lo_*`, `dblink*`, `pg_read_*file`, `pg_ls_dir`, `pg_switch_wal`, `pg_create_*_replication_slot`, `pg_drop_replication_slot`, `pg_sleep*`.
- `EXPLAIN ANALYZE` passes only if the inner statement is `read`.
- The read-only prelude (ADR-0005) is prepended to the SQL. Statements that would turn it off (`SET … transaction_read_only`, `SET SESSION CHARACTERISTICS`, `RESET ALL`) are rejected.

## Rules for the `execute` tool

- The profile must have `allow_write = true`, otherwise `PolicyError` ("profile X is read only").
- `ddl` and `unknown` statements need `allow_ddl = true` in addition to `allow_write` (ADR-0007). Otherwise `PolicyError` ("profile X does not allow DDL").
- **UPDATE/DELETE without a restriction** (for each `UPDATE` and `DELETE`, also inside CTEs):
  - `WHERE` is missing, or
  - after simplification (`sqlglot.optimizer.simplify`) `WHERE` evaluates to a constant true (`1=1`, `TRUE`, `1`, `'a'='a'`, `NOT FALSE`),
  - → `PolicyError`, unless the call has `allow_full_table = true`. The error contains the table name and the advice to add a condition.
  - `UPDATE … FROM` and `DELETE … USING` follow the same rule: WHERE decides.
  - Also applies to MySQL `UPDATE … LIMIT n` without WHERE.
- `TRUNCATE` is `ddl` and also requires `allow_full_table = true`.
- Multiple statements in one call are allowed. Each is checked separately, and the strictest result decides.

Tautology detection is best effort. `WHERE id = id` or `WHERE id IS NULL OR id IS NOT NULL` may not be detected. The goal is to catch the most common mistake (a forgotten WHERE), not to prove that the condition restricts something.

## Statement splitting must match Adminer

The classifier splits SQL with sqlglot; Adminer uses its own parser (see [adminer-protocol.md](adminer-protocol.md#processing)). If the splits differed, the classifier could check different statements than the ones Adminer runs. Therefore:

- `query` rejects SQL that sqlglot cannot parse.
- `DELIMITER`, `\connect` and `COPY … FROM stdin` are always rejected in `query`.
- After execution, the number of statements from the classifier is compared with the number of `pre[id^=sql-]` blocks in the response. A difference is returned as a warning and logged. For `query`, it signals a classifier bug that must be fixed.

## What the MCP server does not do

- **It does not wrap queries in transactions** (ADR-0004). In addition, Adminer itself calls `ROLLBACK` at the end of every request, so no transaction can stay open between calls.
- It does not find out the number of affected rows in advance. A possible "dry run" (`SELECT count(*) … WHERE <same condition>` before UPDATE/DELETE) is a candidate for a separate PRD.
- It does not prevent reading sensitive data. That is a matter of database user privileges.

## Tests

The classifier has table-driven unit tests: input SQL, dialect, expected category, and decision for `query`, `execute`, and `execute` with `allow_full_table`. Each bypass found is added as a regression case.
