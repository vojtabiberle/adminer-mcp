# adminer-mcp

A local MCP server that lets an agent work with a database that is reachable only through the [Adminer](https://www.adminer.org/) web interface. It logs in to Adminer over HTTP like a browser, sends SQL to the "SQL command" page and returns the results as structured data.

- Tested with Adminer 6.3.1 and PostgreSQL (driver `pgsql`). Other drivers are planned.
- Multiple profiles (Adminer logins); passwords from environment variables or the system keyring.
- `query` runs only read SQL, in a read-only database session.
- `execute` changes data only for profiles with `allow_write`; DDL needs `allow_ddl`; UPDATE/DELETE without a restricting WHERE needs an explicit confirmation; `atomic` and `dry_run` modes.

## Quick start

1. Install [uv](https://docs.astral.sh/uv/) and the dependencies: `uv sync`.
2. Create `~/.config/adminer-mcp/profiles.toml`:

   ```toml
   [profiles.prod]
   url = "https://admin.example.com/adminer.php"
   driver = "pgsql"
   server = "db.internal:5432"
   username = "reader"
   password = { keyring = true }
   database = "app"          # optional
   ```

3. Store the password and check the login (connect to the VPN first):

   ```bash
   uv run adminer-mcp set-password prod
   uv run adminer-mcp check prod
   ```

4. Register the server in Claude Code:

   ```bash
   claude mcp add adminer --scope user -- uv --directory /path/to/adminer-mcp run adminer-mcp
   ```

## Tools

`list_profiles`, `list_databases`, `list_schemas`, `list_tables`, `describe_table`, `query`, `explain`, `reconnect`, and `execute` (only if a profile allows writes). See [docs/tools.md](docs/tools.md).

## Documentation

Design, protocol notes, configuration, safety rules, and the PRD/RFC/ADR records are in [docs/](docs/README.md). Development and tests: [docs/development.md](docs/development.md).
