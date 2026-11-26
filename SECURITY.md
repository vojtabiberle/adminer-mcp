# Security

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use [GitHub private vulnerability reporting](https://github.com/vojtabiberle/adminer-mcp/security/advisories/new) instead. You will get an answer as soon as possible.

Relevant examples:

- a way to make `query` change data, or to make `execute` bypass `allow_write`, `allow_ddl` or the `WHERE` guard,
- a password, token or cookie that ends up in a tool result or in the log,
- a way to make the server send credentials to a host other than the configured Adminer.

## Scope and limits

The SQL classifier is a guard against agent mistakes, not a security boundary (see [docs/sql-safety.md](docs/sql-safety.md)). The real boundary is the privileges of the database user: give the agent a read-only database user wherever it does not need to write.

The server runs locally with the rights of the user who starts it, and it can read every password that its profiles reference.
