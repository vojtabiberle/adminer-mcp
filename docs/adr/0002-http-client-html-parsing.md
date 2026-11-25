# ADR-0002: Talk to Adminer through an HTTP client and HTML parsing

- **Status:** Accepted
- **Date:** 2026-11-25
- **Related:** PRD-0001, [adminer-protocol.md](../adminer-protocol.md)

## Context

The only path to the database is Adminer 6.3.1 behind a VPN. A direct connection and an SSH tunnel are not available. Adminer has no API, but it works without JavaScript: login and SQL command are plain HTML forms with a CSRF token.

## Decision

The MCP server talks to Adminer like a browser without JavaScript: `httpx` with a cookie session, form submission (login, SQL command), and parsing of the returned HTML. Metadata (tables, columns, indexes) comes from SQL queries on the system catalogs through the same SQL command page, so the parser handles a single page type.

## Alternatives considered

- **Headless browser (Playwright)** – more resilient to UI changes, but slow and with large dependencies (Chromium). Its advantage applies only to pages that depend on JS, which is not the case here. It stays as a fallback if SSO or a captcha appears.
- **Custom Adminer plugin that returns JSON** – the cleanest option, but it needs write access to the Adminer server, which is not available.
- **Parsing other Adminer pages (table structure, table list)** – more HTML dependencies and more places where the markup can change. SQL on the catalogs is more stable and gives more precise data.

## Consequences

- Dependency on non-public markup. Mitigation: a selector checklist in [adminer-protocol.md](../adminer-protocol.md), fixtures for each version, a version check at login, `ProtocolError` instead of silent failure.
- The cookie `adminer_lang=en` is required, because some information (row counts, errors) is read from message texts.
- Metadata needs SQL for each dialect (ADR-0009).
