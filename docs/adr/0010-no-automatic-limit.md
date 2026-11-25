# ADR-0010: Do not rewrite SQL to add LIMIT; warn instead

- **Status:** Accepted
- **Date:** 2026-11-26
- **Related:** PRD-0001, ADR-0003, [adminer-protocol.md](../adminer-protocol.md#row-limit)

## Context

The Adminer `limit` field limits only the printed rows. PHP still loads the whole result (`store_result`), and the database computes it. A `SELECT` without `LIMIT` on a large table can load the database and the Adminer server heavily, even if the MCP server returns only `max_rows` rows.

The MCP server could add `LIMIT` to the SQL itself (with sqlglot). That changes the SQL the agent wrote: sqlglot regenerates the statement, which can change formatting or dialect details, and the semantics of some queries (e.g. a `LIMIT` that already exists in a subquery, `FETCH FIRST`, `UNION`).

## Decision

- The MCP server does not rewrite SQL to add or change `LIMIT`. The SQL goes to Adminer as the agent wrote it (plus the read-only prelude, ADR-0005).
- Responsibility for `LIMIT` stays with the user and the agent.
- The tool warns:
  - The descriptions of `query` and `execute` tell the agent that `max_rows` limits only the output, not the work on the server, and that the agent should write `LIMIT`.
  - When a result set has more rows than were returned (`total_rows > returned_rows`), the result contains a warning with both numbers and the advice to add `LIMIT`.

## Alternatives considered

- **Add `LIMIT max_rows + 1` automatically** – protects the server, but changes the agent's SQL and can change its meaning. Rejected.
- **Reject a top-level `SELECT` without `LIMIT`** – too strict for small tables and aggregate queries.

## Consequences

- An agent that ignores the warning can still send expensive queries. The warning comes only after the query ran.
- The SQL that runs is always the SQL the agent wrote, which makes errors and results easy to understand.
