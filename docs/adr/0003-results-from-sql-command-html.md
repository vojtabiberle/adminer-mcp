# ADR-0003: Read query results from the SQL command HTML page, not from the CSV export

- **Status:** Accepted
- **Date:** 2026-11-26
- **Related:** ADR-0002, [adminer-protocol.md](../adminer-protocol.md#query-result-export)

## Context

Adminer 6.3.1 offers two ways to get a query result:

1. the HTML table on the SQL command page,
2. the result export (`export=1`, `format=csv|tsv`, `output=text`), which returns plain text without HTML.

At first sight the export looks more robust, but according to the source code (`dump_csv`, `Adminer::dumpData`):

- NULL and the empty string are both `""`,
- an empty result returns no header,
- on error it returns an empty body and drops the message,
- it handles only one statement, with no limit and no column types.

The HTML page, in contrast, distinguishes NULL (`<i>NULL</i>`), carries the column type and table (`th[title]`), the number of affected rows, errors with text, multiple statements in one request, and respects `limit`. Adminer does not shorten values on this page.

## Decision

The `query`, `explain` and `execute` tools and the metadata tools read results from the SQL command HTML page. The export is used only for a possible `export_query` tool that saves a large result to a file (PRD-0001 R15).

## Alternatives considered

- **Export only** – losing NULL and errors is unacceptable for the agent.
- **Combination (HTML for metadata and errors, export for data)** – two requests per query, the SQL runs twice. Not usable for DML.

## Consequences

- The HTML parser is a critical component and needs thorough fixtures.
- A result without rows does not return column names. The tool says so (`columns: null`) and does not try to find them.
- Binary data that is not UTF-8 is not available (Adminer prints only the size). Use `encode(col, 'hex')` in SQL when needed.
- PHP keeps the whole result in memory even with a small `limit` (see the open question in PRD-0001).
