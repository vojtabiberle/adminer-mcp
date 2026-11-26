# Changelog

All notable changes to this project are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] – 2026-11-26

First version (PRD-0001, RFC-0001).

### Added

- MCP server on stdio with the tools `list_profiles`, `list_databases`, `list_schemas`, `list_tables`, `describe_table`, `query`, `explain`, `reconnect` and `execute`.
- Adminer 6.3.1 support with the PostgreSQL driver (`pgsql`).
- Multiple profiles in `~/.config/adminer-mcp/profiles.toml` or from `ADMINER_*` environment variables; passwords from the keyring or environment variables.
- SQL classifier: `query` accepts only reads; `execute` needs `allow_write`, DDL needs `allow_ddl`, and `UPDATE`/`DELETE` without a restricting `WHERE` needs `allow_full_table`.
- Read-only database session for `query`.
- `execute` modes `atomic` and `dry_run`, and a report of the automatic Adminer rollback.
- Automatic new login after an expired session; no retry after a failed login.
- Logout from Adminer on shutdown (stdin closed, SIGTERM, SIGINT).
- CLI commands `set-password` and `check`.
