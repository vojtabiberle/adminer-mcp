# Architecture

## Overview

```
MCP client (Claude Code, …)
   │  stdio (JSON-RPC)
   ▼
adminer-mcp ──────────────────────────────────────────────┐
   server.py      MCP tools, input validation, output format
   safety.py      SQL classification (read / DML / DDL / other)
   dialects/      SQL and specifics for each Adminer driver
   client.py      HTTP session per profile: login, token, relogin
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

- Loads profiles from a TOML file and from environment variables (see [configuration.md](configuration.md)) and validates them with pydantic.
- Resolves the password only at the first login of the profile (env or keyring) and keeps it only in process memory.
- Exposes `Profile` without the password. The password comes from `resolve_password(profile)`, which only `client.py` calls.

### `client.py` – `AdminerClient`

One instance per profile, created lazily at first use.

- `httpx.AsyncClient` with its own cookie jar, optional Basic auth, cookie `adminer_lang=en`, and timeout and TLS verification from the profile.
- An `asyncio.Lock` serializes the requests of a profile. The PHP session is locked on the server anyway.
- `run_sql(sql, database, schema, limit) -> list[StatementResult]` makes sure a session exists, builds the URL, sends the POST and passes the HTML to the parser.
- `logout()` on MCP server shutdown and in `reconnect` (ADR-0011).

### `parser.py`

Stateless functions over HTML (selectolax / lexbor, HTML5 parser):

- `is_login_page(html)`, `page_errors(html)`, `extract_token(html)`, `extract_version(html)`,
- `parse_sql_results(html) -> list[StatementResult]`. The response is split by `pre[id^=sql-]`, and each block gets the following result, message or error.

`StatementResult` is one of these types:

| Type | Content |
|---|---|
| `ResultSet` | `columns: [{name, type, table}]`, `rows: [[str \| None]]`, `shown_rows`, `total_rows` |
| `Affected` | `affected_rows`, `message` |
| `StatementError` | `message`, `errno` |
| `Empty` | "No rows." – result without rows and without column names |

An unknown response shape raises `ProtocolError` with an HTML excerpt for diagnostics. The parser must never silently return an empty result.

### `safety.py`

SQL classification with `sqlglot` according to the profile dialect, see [sql-safety.md](sql-safety.md). Returns the category of each statement and the list of violated rules. Does no I/O.

### `dialects/`

`Dialect` (abstract class in `base.py`) describes everything that differs between drivers:

| Member | Purpose |
|---|---|
| `adminer_driver` | id in the URL and in `auth[driver]` (`pgsql`, `server`, …) |
| `sqlglot_dialect` | dialect for the classifier (`postgres`, `mysql`, …), or `None` if sqlglot does not know the dialect |
| `supports_schemas` | whether the `schema` (`ns`) parameter makes sense |
| `sql_list_databases()`, `sql_list_schemas()`, `sql_list_tables(schema)`, `sql_describe_table(table, schema)` | SQL for metadata |
| `sql_explain(sql, analyze)` | EXPLAIN form |
| `read_only_prelude()` | read-only session guard for `query` (ADR-0005), e.g. `SET default_transaction_read_only = on;` |

The MVP implements `pgsql.py`. More drivers come through PRD/RFC, see [ADR-0009](adr/0009-dialect-modules.md). Drivers without SQL (e.g. `mongo`, `elastic`) need their own RFC: the classifier and the metadata do not apply to them.

### `server.py`

- FastMCP instance, tool registration (see [tools.md](tools.md)).
- `execute` is registered only if at least one profile has `allow_write = true`. For a profile without permission it returns an error.
- Converts exceptions to tool error results with a clear message for the agent.
- Truncates the output (rows, cell length, total size) according to the configuration.

### `cli.py`

Entry point `adminer-mcp`:

- `adminer-mcp` (no arguments) starts the MCP server on stdio,
- `adminer-mcp set-password <profile>` stores the password in the keyring (reads it with `getpass`),
- `adminer-mcp check <profile>` tries to log in and prints the Adminer version and the database.

## Request flow – `query`

1. `server.py` resolves the profile (parameter, else `default_profile`) and the database and schema (parameter, else the profile value, else none).
2. `safety.classify(sql, dialect)` – if any statement is not a read, return `PolicyError` with a pointer to `execute`.
3. Prepend `dialect.read_only_prelude()` (ADR-0005).
4. `client.run_sql(…, limit=max_rows)`:
   1. Without a session, log in (see below).
   2. `POST …&sql=`.
   3. If the response is the login page, log in again once and repeat the request.
5. `parser.parse_sql_results` → results. Remove the prelude result.
6. Truncate and serialize the result to JSON for the agent. If `total_rows > returned_rows`, add a warning that recommends `LIMIT` (ADR-0010). The SQL itself is never rewritten.

`execute` differs in step 2 (different rules, see [sql-safety.md](sql-safety.md)) and in step 3 (no prelude).

## Login and session

```
            ┌──────── GET url → token, adminer_sid
 no session ┤
            └──────── POST auth[...] + token → 302 (always) → GET target page
                         ├─ normal page → OK: read token and version
                         └─ 403 + login page → AuthError (no retry)

 during a query: response = login page
     → first occurrence: drop the session, log in again, repeat the query
     → second occurrence right after: SessionError
```

Rules:

- **No automatic retry after a failed login.** The profile goes to the state `auth_failed`, and further calls return the same error without contacting the server, until the password changes (reloaded from env/keyring) or the agent calls `reconnect`. Reason: 30 failures block the IP for 30 minutes, and behind a proxy also other users (see [adminer-protocol.md](adminer-protocol.md#brute-force-protection)).
- The message "Too many unsuccessful logins" maps to `LockedOutError` with the number of minutes.
- A response with `Invalid CSRF token` causes a token reload and one retry.
- The session lives for the whole process lifetime. Cookies are not stored on disk.
- **Shutdown** (stdio input closes, SIGTERM, SIGINT): every profile with an active session sends a logout, in parallel, with a 5 s timeout per profile. Errors are logged and do not block the shutdown (ADR-0011). SIGKILL skips this; the session then expires on the server.

## Error states

| Exception | When | Shown to the agent |
|---|---|---|
| `ConfigError` | Invalid or missing profile, missing password | Yes, with the profile name and the missing key |
| `AuthError` | Invalid credentials or database connection error | Yes, the text from Adminer |
| `LockedOutError` | Adminer brute-force protection | Yes, with the wait time |
| `PolicyError` | The classifier rejected the SQL | Yes, with the violated rule and a suggested fix |
| `QueryError` | Database error in a specific statement | Yes, as part of the result (statement index, message) |
| `ProtocolError` | Unexpected HTML (other version, proxy error page, VPN not connected) | Yes, with the HTTP status and a short excerpt |
| `httpx` errors | Network, timeout, TLS | Yes, converted to `ConnectionError` with the hint "is the VPN connected?" |

## Logging

To stderr (stdout belongs to the MCP protocol). Default level `INFO`: profile, page URL, duration, number of statements. SQL only at `DEBUG`. The password, the token and cookies are never logged.

## Repository layout

```
adminer-mcp/
  pyproject.toml              # uv, entry point adminer-mcp = adminer_mcp.cli:main
  src/adminer_mcp/
    __init__.py
    cli.py
    server.py
    config.py
    client.py
    parser.py
    safety.py
    errors.py
    models.py                 # StatementResult, Profile, …
    dialects/
      __init__.py             # registry driver id → Dialect
      base.py
      pgsql.py
  tests/
    unit/                     # parser (fixtures), safety, config
    integration/              # against Adminer in Docker
    fixtures/adminer-6.3.1/   # saved HTML responses
  docker/
    compose.yml               # adminer 6.3.1 + postgres
  docs/
```

## Dependencies

| Package | Purpose |
|---|---|
| `mcp` | Official MCP SDK (FastMCP) |
| `httpx` | Async HTTP client |
| `selectolax` | Fast HTML5 parser (lexbor) |
| `sqlglot` | SQL parsing for the classifier |
| `keyring` | System keyring (Secret Service, KWallet, …) |
| `pydantic` | Configuration validation (comes with `mcp`) |
| `tomllib` | Configuration (stdlib) |
