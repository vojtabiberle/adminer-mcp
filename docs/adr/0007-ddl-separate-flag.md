# ADR-0007: DDL behind a separate `allow_ddl` flag

- **Status:** Accepted
- **Date:** 2026-11-26
- **Related:** ADR-0006

## Context

The requirement is to let the agent run INSERT/UPDATE/DELETE (ADR-0006). DDL (`DROP`, `ALTER`, `TRUNCATE`, `CREATE`, `GRANT`) and code execution (`CALL`, `DO`) have a much larger impact and often cannot be undone. A profile for data fixes usually does not need DDL.

## Decision

- A new profile flag `allow_ddl` (default `false`).
- `execute` lets statements of the `ddl` and `unknown` categories through only with `allow_write = true` and also `allow_ddl = true`.
- `TRUNCATE` also requires `allow_full_table = true` (ADR-0006).

## Alternatives considered

- **One `allow_write` flag for DML and DDL** – simpler configuration, but a profile for data fixes would also allow `DROP TABLE`.
- **Levels (`access = "read" | "write" | "admin"`)** – equivalent and more readable, but less flexible if more categories are added.

## Consequences

- One more field in the configuration and in `list_profiles`.
