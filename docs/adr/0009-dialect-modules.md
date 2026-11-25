# ADR-0009: One dialect module per Adminer driver, PostgreSQL first

- **Status:** Accepted
- **Date:** 2026-11-25
- **Related:** ADR-0002, [architecture.md](../architecture.md#dialects)

## Context

PostgreSQL is needed now; later, all databases that Adminer supports: built-in `server` (MySQL/MariaDB), `pgsql`, `sqlite`, `mssql`, `oracle`, and plugin drivers (`mongo`, `elastic`, `clickhouse`, `firebird`, …). The Adminer protocol (login, SQL command, HTML) is the same for all of them. What differs: metadata SQL, the classifier dialect, schema support, the EXPLAIN form, and the list of dangerous functions.

## Decision

- A `Dialect` interface in `dialects/base.py` and one module per Adminer driver id, registered in `dialects/__init__.py`.
- The client, the parser and the tools do not depend on the dialect.
- The MVP implements only `pgsql`. The configuration accepts other drivers, but the tools return "driver not supported yet".
- Each further driver is added through a PRD (and an RFC if it has no SQL or needs tool changes).

## Alternatives considered

- **Generic `information_schema` for all** – does not cover Oracle, SQLite or non-relational drivers, and for PostgreSQL it does not give indexes and comments.
- **Parsing Adminer structure pages (driver-independent)** – rejected in ADR-0002.

## Consequences

- New drivers are an isolated change: module, Docker service, fixtures.
- Non-relational drivers (`mongo`, `elastic`, …) do not fit the SQL classifier and need their own RFC.
