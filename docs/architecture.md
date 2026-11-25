# Architecture

## Overview

```
MCP client (Claude Code, …)
   │  stdio (JSON-RPC)
   ▼
adminer-mcp ──────────────────────────────────────────────┐
   cli.py         entry point, stdio server, signals, set-password, check
   server.py      MCP tool registration (MCPServer), errors → ToolError
   service.py     tool logic: targets, read-only prelude, wrapping, output
   safety.py      SQL classification and policy (read / DML / DDL / other)
   dialects/      SQL and specifics for each Adminer driver
   client.py      HTTP session per profile: login, token, relogin, logout
   parser.py      HTML → structured results
   config.py      profiles, passwords (env / keyring)
└──────────────────────────────────────────────────────────┘
   │  HTTPS (cookie session, CSRF token)
   ▼
Adminer 6.3.1 (PHP, behind VPN) ──► database (PostgreSQL, …)
```

The MCP server runs locally as a process started by the MCP client. It never talks to the database directly, only through the Adminer "SQL command" page (see [adminer-protocol.md](adminer-protocol.md)).

## Components

### `config.py`

- Loads profiles from a TOML file and from environment variables (see [configuration.md](configuration.md)) and validates them with pydantic. Errors become `ConfigError` with the profile name.
- `resolve_password(profile)` reads the password from env or the keyring only at the first login of the profile. The password stays only in process memory.

### `client.py` – `AdminerClient`

One instance per profile, created at first use.

- `httpx.AsyncClient` with its own cookie jar, optional Basic auth, the cookie `adminer_lang=en`, and timeout and TLS verification from the profile. No `Sec-Fetch-Site` header.
- An `asyncio.Lock` serializes the requests of a profile.
- `run_sql(sql, database, schema, limit) -> SqlPage` makes sure a session exists, builds the URL (with a database it always sends `ns`, see [adminer-protocol.md](adminer-protocol.md#url-parameters)), sends the POST and parses the HTML.
- `logout()` (best effort, 5 s timeout) and `reconnect()`.
- State per profile: `not_connected`, `connected`, `auth_failed`, `locked_out`.

### `parser.py`

Stateless functions over HTML (selectolax / lexbor, HTML5 parser):

- `is_login_page`, `page_errors`, `extract_token`, `extract_version`, `is_locked_out`, `locked_out_minutes`,
- `parse_sql_page(html) -> SqlPage`: walks `#content` in document order and assigns each result, message, error and warning to the last `pre id='sql-N'`. Sets `adminer_rolled_back` on the `ROLLBACK -- Adminer` marker.

Models (`models.py`):

| Type | Content |
|---|---|
| `Statement` | `index`, `sql`, `outcome`, `warnings` (database notices) |
| `ResultSet` | `columns: [Column(name, type, table)]`, `rows: [[str \| Binary \| None]]`, `total_rows` |
| `Affected` | `affected_rows` |
| `Empty` | "No rows." – result without rows and without column names |
| `StatementError` | `message` |
| `Binary` | `size` of a non-UTF-8 value whose content Adminer does not print |

An unknown response shape raises `ProtocolError` with an excerpt. A statement without an outcome is a `ProtocolError` too. The parser never silently returns an empty result.

### `safety.py`

SQL classification and policy, see [sql-safety.md](sql-safety.md). `classify(sql, dialect)` returns the statements with their category, unrestricted tables and called functions. `check_query` and `check_execute` raise `PolicyError`; `check_execute` also returns the execute mode (`none`, `explicit`, `atomic`, `dry_run`) and warnings. No I/O.

### `dialects/`

`Dialect` (abstract class in `base.py`) describes everything that differs between drivers:

| Member | Purpose |
|---|---|
| `adminer_driver` | id in the URL and in `auth[driver]` (`pgsql`, `server`, …) |
| `sqlglot_dialect` | dialect for the classifier (`postgres`, `mysql`, …) |
| `supports_schemas` | whether the `schema` (`ns`) parameter applies |
| `transactional_ddl`, `supports_dry_run` | whether DDL can be rolled back; whether `dry_run` is enabled (ADR-0013, ADR-0014) |
| `side_effect_functions`, `non_rollbackable_functions` | fnmatch patterns that `query` and `dry_run` reject |
| `guarded_settings` | settings that `query` must not change (read-only guard) |
| `begin_sql`, `commit_sql`, `rollback_sql` | transaction statements for `atomic` and `dry_run` |
| `read_only_prelude()` | read-only session guard for `query` (ADR-0005), e.g. `SET default_transaction_read_only = on;` |
| `sql_list_databases()`, `sql_list_schemas()`, `sql_list_tables(schema)`, `sql_describe_table(table, schema)` | SQL for metadata |
| `sql_explain(sql, analyze)` | EXPLAIN form |

`get_dialect(driver)` returns the dialect or raises `UnsupportedError`. The MVP implements `pgsql.py`. More drivers come through PRD/RFC, see [ADR-0009](adr/0009-dialect-modules.md). Drivers without SQL (e.g. `mongo`, `elastic`) need their own RFC: the classifier and the metadata do not apply to them.

### `service.py` – `AdminerService`

The tool logic, testable without the MCP layer.

- Resolves the target of a call: profile (parameter, else `default_profile`), database and schema (parameter, else the profile value, else none).
- Read tools and metadata run behind the read-only prelude; the result of the prelude is removed and the statement indexes are shifted back.
- `execute` wraps the SQL for `atomic` (`BEGIN; … ; COMMIT;`) and `dry_run` (`BEGIN; … ; ROLLBACK;`), removes the results of the added statements and reports the `transaction` outcome (RFC-0001).
- Builds the output: rows limited by `max_rows`, cells by `max_cell_chars`, the rows of one result by `MAX_OUTPUT_CHARS` (200,000 characters); warnings for results larger than the output (ADR-0010), failed statements, a statement count that differs from the classifier, and an untested Adminer version.
- `shutdown()` logs out all clients in parallel and closes them (ADR-0011).

### `server.py`

- `create_server(config)` builds an `MCPServer` (the MCP SDK 2.x name of FastMCP) and registers the tools (see [tools.md](tools.md)). The tool docstrings are part of the contract.
- `execute` is registered only if at least one profile has `allow_write = true`.
- Converts `AdminerMcpError` to `ToolError`, so the agent gets the message; other exceptions are crashes that the SDK reports without details.
- The lifespan calls `service.shutdown()` when the server stops (shielded from cancellation, 10 s limit).

### `cli.py`

Entry point `adminer-mcp`:

- `adminer-mcp` or `adminer-mcp serve` runs the MCP server on stdio,
- `adminer-mcp set-password <profile>` stores the password in the keyring (reads it with `getpass`),
- `adminer-mcp check [<profile>]` logs in and prints the Adminer version, the database, the user and the server version.

## Request flow – `query`

1. `service.query` resolves the target and the row limit.
2. `classify` + `check_query` – if any statement is not a read, `PolicyError` with a pointer to `execute`. Nothing is sent.
3. Prepend `dialect.read_only_prelude()` (ADR-0005).
4. `client.run_sql(…, limit=max_rows)`:
   1. Without a session, log in (see below).
   2. `POST …&sql=` with `query`, `limit`, `error_stops=1`, `token`.
   3. If the response is the login page, log in again once and repeat the request. After `Invalid CSRF token`, take the new token from the page and repeat once. Other page errors become `AdminerPageError`.
5. `parse_sql_page` → statements. Remove the prelude result.
6. Truncate and serialize the result. If `total_rows > returned_rows`, add a warning that recommends `LIMIT` (ADR-0010). The SQL itself is never rewritten.

`execute` differs in step 2 (`check_execute`, see [sql-safety.md](sql-safety.md)), step 3 (no prelude; optional wrapping for `atomic` and `dry_run`) and adds the `transaction` object.

## Login and session

```
            ┌──────── GET url → token, adminer_sid
 no session ┤
            └──────── POST auth[...] + token → 303 (always) → GET target (follows redirects)
                         ├─ normal page → OK: read token and version
                         └─ 403 + login page → AuthError / LockedOutError (no retry)

 during a query: response = login page
     → first occurrence: log in again, repeat the query
     → second occurrence right after: SessionError
```

Rules:

- **No automatic retry after a failed login.** The profile goes to the state `auth_failed` (or `locked_out`), and further calls raise the same error without contacting the server, until the agent calls `reconnect`. Reason: 30 failures block the IP for 30 minutes, and behind a proxy also other users (see [adminer-protocol.md](adminer-protocol.md#brute-force-protection)).
- The message "Too many unsuccessful logins" maps to `LockedOutError` with the number of minutes.
- A POST to the SQL page that gets a redirect is a `ProtocolError`: the SQL did not run.
- The session lives for the whole process lifetime. Cookies are not stored on disk.
- **Shutdown** (ADR-0011):
  - The client closes stdin → the server lifespan logs out every active profile.
  - SIGTERM or SIGINT → a signal watcher logs out every active profile (10 s limit) and exits the process. It cannot wait for the server to stop: the stdio transport blocks in a thread that reads stdin.
  - SIGKILL skips the logout; the session then expires on the server.

## Error states

| Exception | When | Shown to the agent |
|---|---|---|
| `ConfigError` | Invalid or missing profile, missing password | Yes, with the profile name and the missing key |
| `AuthError` | Invalid credentials, database connection error, HTTP 401 from a proxy | Yes, the text from Adminer |
| `LockedOutError` | Adminer brute-force protection | Yes, with the wait time |
| `SessionError` | The session expired again right after a new login | Yes |
| `PolicyError` | The classifier or the profile rejected the request | Yes, with the violated rule and a suggested fix |
| `AdminerPageError` | A page-level error, e.g. an unknown schema | Yes, the text from Adminer |
| `UnsupportedError` | A driver or a feature that is not implemented | Yes |
| `ProtocolError` | Unexpected HTML (other version, proxy error page), HTTP 5xx, a redirected SQL request | Yes, with a short excerpt |
| `AdminerConnectionError` | Network error, timeout, TLS | Yes, with the hint "Is the VPN connected?" |

A failed statement is not an exception: it is a statement with `"kind": "error"` in the result.

## Logging

To stderr (stdout belongs to the MCP protocol). Level from `ADMINER_MCP_LOG_LEVEL` (default `INFO`): profile, HTTP method, status and duration of each request, login, logout, version warnings. The SQL, the password, the token and cookies are not logged.

## Repository layout

```
adminer-mcp/
  pyproject.toml              # uv, entry point adminer-mcp = adminer_mcp.cli:main
  src/adminer_mcp/
    cli.py  server.py  service.py  client.py  parser.py  safety.py
    config.py  errors.py  models.py
    dialects/
      __init__.py             # registry driver id → Dialect
      base.py
      pgsql.py
  tests/
    unit/                     # parser (fixtures), client (MockTransport), safety, config, dialects
    integration/              # against Adminer in Docker, through the service and an MCP client
    fixtures/
      record.py               # records the fixtures from the Docker environment
      adminer-6.3.1/          # saved HTML responses
  docker/
    compose.yml               # adminer 6.3.1 + postgres 17
    adminer/Dockerfile        # php:8.4-apache + pgsql + adminer-6.3.1.php (SHA-256 checked)
    postgres/init.sql         # seed with parser edge cases, roles reader/writer
  docs/
```

## Dependencies

| Package | Purpose |
|---|---|
| `mcp` (2.x) | Official MCP SDK (`MCPServer`) |
| `httpx` | Async HTTP client |
| `selectolax` | Fast HTML5 parser (lexbor) |
| `sqlglot` | SQL tokenizer and parser for the classifier |
| `keyring` | System keyring (Secret Service, KWallet, …) |
| `pydantic` | Configuration validation |
| `anyio` | Signal handling in the CLI (comes with `mcp`) |
| `tomllib` | Configuration (stdlib) |
