# Configuration

Decision: [ADR-0008](adr/0008-profiles-and-secrets.md).

## Configuration sources

1. **Profile file** `$XDG_CONFIG_HOME/adminer-mcp/profiles.toml` (default `~/.config/adminer-mcp/profiles.toml`). The path can be changed with the `ADMINER_MCP_CONFIG` variable.
2. **Environment variables** for the simple case of one profile without a file. They create the profile `default`.

If both sources exist, the profile from the environment is added to the profiles from the file. A name collision on `default` is a configuration error.

`default_profile` is required when there is more than one profile; with one profile, that profile is the default. Without any profile, the server does not start and prints what to configure.

## Profile file

```toml
default_profile = "prod"

[defaults]                 # shared values for all profiles; a profile can override them
max_rows = 100
max_rows_limit = 1000
max_cell_chars = 2000
timeout = 60               # seconds per HTTP request
verify_tls = true

[profiles.prod]
url = "https://admin.example.com/adminer.php"
driver = "pgsql"
server = "db-prod.internal:5432"
username = "app_reader"
password = { keyring = true }          # service "adminer-mcp", account = profile name
# database = "app"                     # optional
# schema = "public"                    # optional, only for drivers with schemas
allow_write = false

[profiles.staging]
url = "https://admin-staging.example.com/"
driver = "pgsql"
server = "db-staging.internal"
username = "app"
password = { env = "ADMINER_STAGING_PASSWORD" }
database = "app"
allow_write = true

[profiles.staging.basic_auth]           # optional, HTTP Basic auth in front of Adminer
username = "team"
password = { env = "ADMINER_STAGING_BASIC_PASSWORD" }
```

### Profile fields

| Field | Required | Default | Description |
|---|---|---|---|
| `url` | yes | – | Adminer address without a query string |
| `driver` | yes | – | Adminer driver id: `pgsql`, `server` (MySQL/MariaDB), `sqlite`, `mssql`, `oracle`, or a plugin driver. Case-insensitive aliases: `mysql`/`mariadb` → `server`, `postgres`/`postgresql` → `pgsql`. The MVP supports only `pgsql`; others pass validation but the tools return "driver … is not supported yet" |
| `server` | yes* | `""` | `host[:port]`. Empty only for drivers where Adminer allows it (SQLite) |
| `username` | yes | – | Database user |
| `password` | yes | – | `{ env = "NAME" }`, `{ keyring = true }` (account = profile name) or `{ keyring = "other-account" }`. Plain-text passwords are not supported |
| `database` | no | none | Default database. Tools can override it with the `database` parameter |
| `schema` | no | none | Default schema (`ns`). Applies only together with a database |
| `allow_write` | no | `false` | Enables the `execute` tool for DML ([ADR-0006](adr/0006-sql-classifier-and-write-gate.md)) |
| `allow_ddl` | no | `false` | Enables DDL in `execute`; needs `allow_write` too ([ADR-0007](adr/0007-ddl-separate-flag.md)) |
| `basic_auth` | no | – | `{ username, password = {env|keyring} }`. With `{ keyring = true }` the keyring account is `<profile>/basic_auth` |
| `verify_tls` | no | `true` | Certificate verification |
| `timeout` | no | `60` | HTTP request timeout in seconds |
| `max_rows` | no | `100` | Default number of returned rows per statement |
| `max_rows_limit` | no | `1000` | Upper bound for the `max_rows` parameter |
| `max_cell_chars` | no | `2000` | Longer values are truncated and marked |
| `description` | no | – | Free text the agent sees in `list_profiles` (e.g. "production, read only") |

## Environment variables (one profile `default`)

| Variable | Field |
|---|---|
| `ADMINER_URL` | `url` |
| `ADMINER_DRIVER` | `driver` |
| `ADMINER_SERVER` | `server` |
| `ADMINER_USERNAME` | `username` |
| `ADMINER_PASSWORD` | the password itself |
| `ADMINER_DATABASE` | `database` |
| `ADMINER_SCHEMA` | `schema` |
| `ADMINER_ALLOW_WRITE` | `allow_write` (`1`/`true`) |
| `ADMINER_ALLOW_DDL` | `allow_ddl` |

The environment profile is created only if `ADMINER_URL` is set.

Other variables:

| Variable | Meaning |
|---|---|
| `ADMINER_MCP_CONFIG` | Path to the profile file |
| `ADMINER_MCP_LOG_LEVEL` | Log level on stderr (`DEBUG`, `INFO`, `WARNING`, …), default `INFO` |

## Passwords

- **Keyring:** `adminer-mcp set-password prod` (with `uvx --from git+https://github.com/vojtabiberle/adminer-mcp` or `uv run` in front) asks for the password and stores it under the service `adminer-mcp`, account `prod`. On Linux this uses Secret Service (GNOME Keyring, KWallet), which must be unlocked in the session where the MCP client runs.
- **Env:** set the variable in the shell that starts the MCP client, or in the `env` section of the MCP client configuration. Never commit it to a repository.
- The password is loaded only at the first login of the profile. An error therefore shows up when the profile is used, not at startup. `adminer-mcp check <profile>` verifies it in advance: it logs in and prints the Adminer version, the database, the user and the server version.
- `allow_ddl = true` without `allow_write = true` is a configuration error.

## Registration in an MCP client

Without a clone, `uvx` installs and runs the server from GitHub. Claude Code, user scope:

```bash
claude mcp add adminer --scope user -- uvx --from git+https://github.com/vojtabiberle/adminer-mcp adminer-mcp
```

From a local clone:

```bash
claude mcp add adminer --scope user -- uv --directory /path/to/adminer-mcp run adminer-mcp
```

Or `.mcp.json` in the project where it is used, here with a password from the environment of the MCP client:

```json
{
  "mcpServers": {
    "adminer": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/vojtabiberle/adminer-mcp", "adminer-mcp"],
      "env": {
        "ADMINER_STAGING_PASSWORD": "${ADMINER_STAGING_PASSWORD}"
      }
    }
  }
}
```

Profiles are loaded only at process start. After editing `profiles.toml`, restart the MCP server.
