# ADR-0006: SQL classifier, writes behind the `allow_write` flag, block UPDATE/DELETE without WHERE

- **Status:** Accepted
- **Date:** 2026-11-25
- **Related:** ADR-0004, ADR-0007, [sql-safety.md](../sql-safety.md)

## Context

The agent needs to read and also change data (INSERT/UPDATE/DELETE). Writes must not be available by accident, and the most common destructive mistake, UPDATE or DELETE without WHERE, must be caught. Adminer itself has no such protection.

## Decision

- `safety.py` splits SQL into statements with `sqlglot` and assigns each one a category (`read`, `session`, `dml`, `ddl`, …, see [sql-safety.md](../sql-safety.md)).
- **`query`** lets only reads through. It rejects everything else before sending.
- **`execute`** is available only for profiles with `allow_write = true`. The tool is registered only if such a profile exists. Writes are not forbidden; they are behind an explicit configuration.
- `execute` rejects **UPDATE/DELETE without WHERE**, or with a WHERE that is a constant true. The agent must set `allow_full_table = true`. The same applies to `TRUNCATE`.
- SQL that sqlglot cannot parse is treated as the most risky category.

## Alternatives considered

- **Regular expressions instead of a parser** – unreliable (comments, CTEs, strings).
- **Forbid writes completely** – rejected; writes are needed.
- **User confirmation of writes through MCP elicitation** – depends on client support. Candidate for a future PRD.

## Consequences

- The classifier is a guard against mistakes, not a security boundary. The real boundary is the privileges of the database user.
- Statement splitting in sqlglot and in Adminer can differ. This is checked after execution (block count), and SQL the classifier does not understand is rejected.
- Tautology detection in WHERE is best effort.
