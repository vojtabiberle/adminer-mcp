# adminer-mcp

[![CI](https://github.com/vojtabiberle/adminer-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/vojtabiberle/adminer-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A local [MCP](https://modelcontextprotocol.io/) server that lets an AI agent work with a database that is reachable **only through the [Adminer](https://www.adminer.org/) web interface** – for example a database behind a VPN where you have no direct connection, no SSH tunnel, just Adminer in the browser.

The server logs in to Adminer over HTTP like a browser, sends SQL to the "SQL command" page and returns the results as structured data. It never connects to the database directly.

- **Read safely:** `query` accepts only read SQL and runs it in a read-only database session.
- **Write on purpose:** `execute` exists only for profiles with `allow_write`; DDL needs `allow_ddl`; `UPDATE`/`DELETE` without a restricting `WHERE` needs an explicit confirmation.
- **Transactions within one call:** `atomic` (all or nothing) and `dry_run` (run, show the result, roll back).
- **Several profiles:** each profile is one Adminer login; the agent chooses the profile and the database per call.
- **No passwords in the agent's hands:** passwords come from the system keyring or environment variables, never from tool parameters.

> Status: alpha. Tested with **Adminer 6.3.1** and **PostgreSQL** (driver `pgsql`). Other Adminer drivers (MySQL/MariaDB, SQLite, MS SQL, Oracle, …) are planned. Adminer has no API, so a different Adminer version can change the HTML the server parses; the server warns about untested versions.

This project is not affiliated with Adminer.

## Requirements

- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12+ when needed)
- Network access to the Adminer instance (connect to the VPN yourself; the server does not handle it)

## Quick start

1. Create `~/.config/adminer-mcp/profiles.toml`:

   ```toml
   [profiles.dev]
   url = "https://adminer.example.com/adminer.php"   # without ?…
   driver = "pgsql"
   server = "db.internal:5432"                        # the "Server" field of the Adminer login form
   username = "reader"
   password = { keyring = true }
   # database = "app"                                 # optional default database
   # allow_write = true                               # only if the agent may change data
   ```

2. Store the password in the system keyring and check the login:

   ```bash
   uvx --from git+https://github.com/vojtabiberle/adminer-mcp adminer-mcp set-password dev
   uvx --from git+https://github.com/vojtabiberle/adminer-mcp adminer-mcp check dev
   ```

3. Register the server in your MCP client. Claude Code:

   ```bash
   claude mcp add adminer --scope user -- uvx --from git+https://github.com/vojtabiberle/adminer-mcp adminer-mcp
   ```

   Other clients (`mcpServers` JSON):

   ```json
   {
     "mcpServers": {
       "adminer": {
         "command": "uvx",
         "args": ["--from", "git+https://github.com/vojtabiberle/adminer-mcp", "adminer-mcp"]
       }
     }
   }
   ```

   From a local clone, use `uv --directory /path/to/adminer-mcp run adminer-mcp` instead.

More options – several profiles, passwords from environment variables, HTTP Basic auth, limits – are in [docs/configuration.md](docs/configuration.md).

## Tools

| Tool | Description |
|---|---|
| `list_profiles` | Configured profiles, their permissions and connection state |
| `list_databases`, `list_schemas`, `list_tables` | Database structure |
| `describe_table` | Columns, primary key, indexes, foreign keys |
| `query` | Read SQL; returns columns, rows and warnings |
| `explain` | Query plan, optionally `EXPLAIN ANALYZE` for reads |
| `execute` | Data changes; only if a profile has `allow_write` |
| `reconnect` | New login after a fixed password or profile |

Every tool takes optional `profile`, `database` and `schema` parameters. Details and output formats: [docs/tools.md](docs/tools.md).

## Safety model

Protection has three layers, and only the first one is a real security boundary:

1. **The database user's privileges.** Use a read-only user wherever writes are not needed.
2. **An SQL classifier** decides before anything is sent whether the SQL may run through a tool. It guards against agent mistakes; it is not a defense against an attacker.
3. **A read-only session** (`SET default_transaction_read_only = on`) for `query`, so the database rejects writes hidden in functions.

Good to know:

- `max_rows` limits only what the server returns. Adminer and the database still process the whole result, so the agent should write `LIMIT`. The server never rewrites SQL; it warns when a result was larger than the output.
- A transaction never outlives one tool call: Adminer rolls back an unfinished transaction at the end of every request.
- Adminer blocks an IP address after 30 failed logins in 30 minutes (behind a proxy: everyone behind it). The server therefore never retries a failed login on its own.
- On shutdown the server logs out of Adminer, so the password does not stay in the server-side session.

Full rules: [docs/sql-safety.md](docs/sql-safety.md).

## Documentation

- [docs/](docs/README.md) – architecture, the Adminer HTTP protocol as the server uses it, configuration, tools, safety
- [docs/development.md](docs/development.md) – development setup and tests (Adminer + PostgreSQL in Docker)
- Decisions and changes are recorded as PRDs, RFCs and ADRs in [docs/](docs/README.md#process-prd--rfc--adr)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
