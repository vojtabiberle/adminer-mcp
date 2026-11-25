# MCP tools

The tool contract for the agent. All tools except `list_profiles` accept these shared optional parameters:

| Parameter | Type | Description |
|---|---|---|
| `profile` | string | Profile name. Default is `default_profile` |
| `database` | string | Database for this call. Default is `database` from the profile, else none (PostgreSQL then uses `postgres`) |
| `schema` | string | Schema (`ns`) for this call. Only for drivers with schemas and only together with a database |

The tools are stateless: the database selection is not remembered between calls. The agent passes it every time or relies on the profile default.

## Overview

| Tool | Reads/writes | Description |
|---|---|---|
| `list_profiles` | – | List of profiles |
| `list_databases` | reads | Databases on the server |
| `list_schemas` | reads | Schemas in a database |
| `list_tables` | reads | Tables and views |
| `describe_table` | reads | Columns, keys, indexes, foreign keys |
| `query` | reads | Any read SQL |
| `explain` | reads | Query plan |
| `execute` | writes | DML with `allow_write`, DDL with `allow_write` and `allow_ddl`. Registered only if some profile has `allow_write` |
| `reconnect` | – | Drops the profile session and clears the `auth_failed` state |
| `export_query` | reads | Export a result to a local file (PRD-0001 R15, Could, **not implemented**) |

## `list_profiles`

Input: none.

Output:

```json
[
  {
    "name": "prod",
    "description": "production, read only",
    "default": true,
    "driver": "pgsql",
    "server": "db-prod.internal:5432",
    "username": "app_reader",
    "database": "app",
    "schema": null,
    "allow_write": false,
    "allow_ddl": false,
    "state": "connected | not_connected | auth_failed | locked_out",
    "adminer_version": "6.3.1"
  }
]
```

`adminer_version` is `null` until the profile logs in. Passwords are not returned. The Adminer URL is not returned either; the agent does not need it.

## `list_databases`

Output: `{"databases": ["app", "postgres", …]}`. For PostgreSQL without templates (`NOT datistemplate`).

## `list_schemas`

Output: `{"schemas": ["public", "audit", …]}` without system schemas (`pg_*`, `information_schema`). For drivers without schemas: error "driver does not support schemas".

## `list_tables`

Output:

```json
{
  "database": "app",
  "schema": "public",
  "tables": [
    {"schema": "public", "name": "orders", "type": "table", "estimated_rows": 120000, "comment": null}
  ]
}
```

`type`: `table`, `view`, `materialized_view`, `foreign_table`, `partitioned_table`. `estimated_rows` is an estimate from statistics (`reltuples`), not `count(*)`. Without `schema`, tables from all non-system schemas are returned.

## `describe_table`

Input: `table` (required, optionally `schema.table`) and the shared parameters.

Output:

```json
{
  "schema": "public",
  "name": "orders",
  "type": "table",
  "comment": null,
  "columns": [
    {"name": "id", "type": "bigint", "nullable": false, "default": "nextval('orders_id_seq'::regclass)", "comment": null}
  ],
  "primary_key": ["id"],
  "indexes": [{"name": "orders_status_idx", "columns": ["status"], "unique": false, "definition": "CREATE INDEX …"}],
  "foreign_keys": [{"name": "orders_customer_fk", "columns": ["customer_id"], "references": {"schema": "public", "table": "customers", "columns": ["id"]}}]
}
```

The data comes from SQL queries on `pg_catalog` through the SQL command page (multiple statements in one request), not from parsing the Adminer table page.

## `query`

Input:

| Parameter | Type | Description |
|---|---|---|
| `sql` | string, required | One or more read statements |
| `max_rows` | int | Number of returned rows per statement. Default from the profile, max. `max_rows_limit` |

Rules: see [sql-safety.md](sql-safety.md). If the SQL contains anything other than reads, the tool rejects it and sends nothing. The SQL runs in a read-only session ([ADR-0005](adr/0005-read-only-session-guard.md)), so a write hidden in a function fails with a database error.

Output:

```json
{
  "profile": "prod",
  "database": "app",
  "schema": null,
  "statements": [
    {
      "index": 1,
      "sql": "SELECT id, note FROM orders WHERE …",
      "kind": "result_set",
      "columns": [{"name": "id", "type": "int8", "table": "orders"}, {"name": "note", "type": "text", "table": "orders"}],
      "rows": [["1", null], ["2", ""]],
      "returned_rows": 2,
      "total_rows": 2,
      "truncated": false,
      "truncated_cells": 0
    }
  ],
  "warnings": []
}
```

- Values are strings in the text form the database returns. NULL is `null`. Types are not converted, so nothing is lost (`numeric` precision, time zones).
- A value longer than `max_cell_chars` is cut and ends with `… [truncated, N characters]`; `truncated_cells` counts them. The rows of one result stop at about 200,000 characters, with a warning.
- Database notices (e.g. `RAISE NOTICE`) are in the `warnings` field of the statement.
- A binary value that is not UTF-8 is `{"binary_bytes": N}`, because Adminer does not return the content.
- Result without rows: `"kind": "empty"`, `columns` is `null` (Adminer does not return column names for an empty result).
- Error: `"kind": "error"`, `"error": "…"`. Further statements do not run (`error_stops`).
- `warnings` contains e.g. "statement 1: the result had 1,234,567 rows, 100 returned; the server loaded the whole result; add LIMIT". The warning appears for every result set where `total_rows > returned_rows`. The MCP server never adds `LIMIT` itself; that is the responsibility of the user and the agent (ADR-0010).

## `explain`

Input: `sql` (one statement), `analyze` (bool, default `false`).

- `analyze = true` actually runs the query (`EXPLAIN (ANALYZE, BUFFERS)`), so it is allowed only for statements that pass as reads. It runs in the read-only session like `query`.
- The output has the same shape as `query` (one `result_set` with the plan rows, up to 10,000 rows).

## `execute`

Registered only if at least one profile has `allow_write = true`. For a profile without permission it returns `PolicyError`.

Input:

| Parameter | Type | Description |
|---|---|---|
| `sql` | string, required | One or more statements |
| `allow_full_table` | bool | Confirms that UPDATE/DELETE without a restricting WHERE (or `TRUNCATE`) is intended. Default `false` |
| `atomic` | bool | Wrap the SQL in `BEGIN … COMMIT`: all or nothing ([ADR-0013](adr/0013-atomic-execute.md)). Default `false` |
| `dry_run` | bool | Wrap the SQL in `BEGIN … ROLLBACK`: run it, return the results, save nothing ([ADR-0014](adr/0014-dry-run-execute.md)). Default `false` |
| `max_rows` | int | For `RETURNING` and read statements inside |

Rules: see [sql-safety.md](sql-safety.md#rules-for-the-execute-tool). `atomic` and `dry_run` cannot be combined, and with either of them the SQL must not contain `BEGIN`, `COMMIT`, `ROLLBACK`, `SAVEPOINT` or similar.

The output has the same shape as `query`, plus a `transaction` object ([RFC-0001](rfc/0001-transactions-within-one-request.md)). Statements without a result set have `"kind": "affected", "affected_rows": N`. Statement indexes refer to the agent SQL; the statements that the MCP server adds for `atomic` and `dry_run` are not in the output.

```json
"transaction": {
  "mode": "none | explicit | atomic | dry_run",
  "outcome": "none | committed | rolled_back | dry_run_rolled_back | rolled_back_by_adminer",
  "failed_statement": 2
}
```

| `mode` | Meaning |
|---|---|
| `none` | No transaction statements; every statement ran in autocommit |
| `explicit` | The agent wrote `BEGIN … COMMIT` (or `ROLLBACK`) itself |
| `atomic`, `dry_run` | The parameter of the same name |

| `outcome` | Meaning |
|---|---|
| `none` | Autocommit, no transaction |
| `committed` | The transaction was committed |
| `rolled_back` | The atomic block or the dry run failed, or the agent's own `ROLLBACK` ran; nothing from the transaction was saved |
| `dry_run_rolled_back` | The dry run succeeded and was rolled back |
| `rolled_back_by_adminer` | A transaction was still open at the end of the request and Adminer rolled it back ([ADR-0012](adr/0012-report-adminer-rollback.md)) |

`failed_statement` is the index of the failed statement or `null`. Warnings in `warnings` explain the outcome, e.g. that earlier statements stay applied in autocommit, or that nothing was saved.

Warnings in the tool description for the agent:

- Without `atomic` or `dry_run`, each statement runs immediately in autocommit. If an error occurs in the middle, the earlier statements stay applied and the later ones do not run.
- An explicit `BEGIN … COMMIT` works within one call. Adminer rolls back an unfinished transaction at the end of the request; a transaction does not persist between calls.
- `dry_run` really runs the statements: it takes locks and costs the same as a real run; side effects in triggers are not undone.

## `reconnect`

Input: `profile`. Logs out the old session if it is still valid (ADR-0011), drops cookies and the token, reloads the password (env/keyring), clears the `auth_failed` state and makes one login attempt. Output: `{"profile", "state", "adminer_version", "warnings"}`.

## Tool descriptions for the agent

The tool docstrings are part of the contract. They must contain:

- what the tool does, and when to use `query` and when `execute`,
- that `max_rows` limits only the output, not the work on the server; the agent is responsible for writing `LIMIT`, the MCP server does not add it (ADR-0010),
- that the database is selected by a parameter and the tools do not remember it,
- for `execute`, the warning about autocommit and about `allow_full_table`.
