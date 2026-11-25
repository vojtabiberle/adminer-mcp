# ADR-0001: Python, official MCP SDK (FastMCP), stdio transport

- **Status:** Accepted
- **Date:** 2026-11-25
- **Related:** PRD-0001

## Context

The MCP server runs locally and the MCP client (Claude Code and others) starts it. We need an HTTP client with cookies, an HTML5 parser, and an SQL parser for the classifier. The project owner prefers Python.

## Decision

- Python 3.12+, project managed with `uv`.
- Official MCP SDK `mcp`, FastMCP API (tool decorators, schema from type annotations).
- stdio transport. Logs go to stderr.
- Libraries: `httpx` (async), `selectolax`, `sqlglot`, `keyring`, `pydantic`.

## Alternatives considered

- **TypeScript (`@modelcontextprotocol/sdk`, `cheerio`)** – technically equivalent; the owner prefers Python.
- **HTTP/SSE transport** – not needed for local use, and it would open a port with access to the database.

## Consequences

- `sqlglot` is the best multi-dialect SQL parser available in Python, which helps the classifier.
- The user needs `uv`. The server starts through `uv --directory … run adminer-mcp`.
