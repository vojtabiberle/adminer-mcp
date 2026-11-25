# adminer-mcp – documentation

A local MCP server that lets an agent work with a database that is reachable only through the [Adminer](https://www.adminer.org/) web interface.
The MCP server logs in to Adminer over HTTP the same way a browser does, sends SQL to the "SQL command" page, and converts the returned HTML into structured data.

## Contents

Reference documents describe the target state of the system and are kept up to date:

| Document | Content |
|---|---|
| [architecture.md](architecture.md) | Components, request flow, code layout, error states |
| [adminer-protocol.md](adminer-protocol.md) | The Adminer 6.3.1 HTTP interface as the MCP server uses it (derived from the source code) |
| [configuration.md](configuration.md) | Profiles, passwords (env/keyring), registration in an MCP client |
| [tools.md](tools.md) | MCP tool contract (inputs, outputs, errors) |
| [sql-safety.md](sql-safety.md) | SQL classification, write protection, UPDATE/DELETE without WHERE |
| [development.md](development.md) | Local development, tests, test environment with Adminer in Docker |

Requirements and decisions:

- [prd/](prd/) – product requirements (PRD)
- [rfc/](rfc/) – technical change proposals (RFC)
- [adr/](adr/) – architecture decision records (ADR)

## Process: PRD → RFC → ADR

| Type | Answers | When to write | Statuses |
|---|---|---|---|
| **PRD** | *What* and *why* | A new capability or a behavior change visible to the user or the agent (new tool, new driver, changed output) | Draft → Accepted → Implemented, or Superseded |
| **RFC** | *How* | A technical proposal for a change that affects the architecture, the Adminer protocol or the tool contract. Usually follows a PRD | Draft → In review → Accepted / Rejected → Implemented |
| **ADR** | *Why this way* | Each individual decision with its alternatives and consequences. Comes out of an RFC, or stands alone for smaller decisions | Proposed → Accepted / Rejected, later Deprecated or Superseded by ADR-XXXX |

Rules:

- Files are numbered in ascending order with four digits, per type: `adr/0007-ddl-flag.md`, `rfc/0001-export-tool.md`.
- The template is always `0000-template.md` in the given directory.
- The content of an accepted ADR does not change. To change a decision, write a new ADR that supersedes the old one (`Superseded by`) and update only the status of the old one.
- After implementation, update the reference documents so that they describe the actual state. PRDs and RFCs stay as a historical record.
- Status `Proposed` means the proposal waits for a decision by the project owner.

## ADR overview

| ADR | Decision | Status |
|---|---|---|
| [0001](adr/0001-python-mcp-sdk.md) | Python, official MCP SDK (FastMCP), stdio transport | Accepted |
| [0002](adr/0002-http-client-html-parsing.md) | Talk to Adminer through an HTTP client and HTML parsing | Accepted |
| [0003](adr/0003-results-from-sql-command-html.md) | Read query results from the SQL command HTML page, not from the CSV export | Accepted |
| [0004](adr/0004-no-transaction-wrapping.md) | The MCP server does not wrap queries in transactions | Accepted |
| [0005](adr/0005-read-only-session-guard.md) | Read-only guard at the database session level for the `query` tool | Accepted |
| [0006](adr/0006-sql-classifier-and-write-gate.md) | SQL classifier, writes behind the `allow_write` flag, block UPDATE/DELETE without WHERE | Accepted |
| [0007](adr/0007-ddl-separate-flag.md) | DDL behind a separate `allow_ddl` flag | Accepted |
| [0008](adr/0008-profiles-and-secrets.md) | Multiple profiles, passwords from env or keyring, database optional and selectable per call | Accepted |
| [0009](adr/0009-dialect-modules.md) | One dialect module per Adminer driver, PostgreSQL first | Accepted |
| [0010](adr/0010-no-automatic-limit.md) | Do not rewrite SQL to add LIMIT; warn instead | Accepted |
| [0011](adr/0011-logout-on-shutdown.md) | Log out of Adminer on shutdown | Accepted |
| [0012](adr/0012-report-adminer-rollback.md) | Detect and report the automatic Adminer rollback | Accepted |
| [0013](adr/0013-atomic-execute.md) | Opt-in `atomic` parameter for `execute` | Accepted |
| [0014](adr/0014-dry-run-execute.md) | Opt-in `dry_run` parameter for `execute` | Accepted |

## RFC overview

| RFC | Title | Status |
|---|---|---|
| [0001](rfc/0001-transactions-within-one-request.md) | Transactions within one request (ADR-0012 to ADR-0014) | Implemented |
