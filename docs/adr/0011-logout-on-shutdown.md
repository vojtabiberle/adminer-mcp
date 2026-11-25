# ADR-0011: Log out of Adminer on shutdown

- **Status:** Accepted
- **Date:** 2026-11-26
- **Related:** PRD-0001, [adminer-protocol.md](../adminer-protocol.md#logout), [architecture.md](../architecture.md#login-and-session)

## Context

After login, Adminer keeps the database password in the server-side PHP session (`set_password`), together with the query history including literals. If the MCP server only stops, this session stays on the server until PHP removes it (`session.gc_maxlifetime`).

## Decision

- When the MCP server shuts down (the stdio input closes, or the process gets SIGTERM/SIGINT), it logs out every profile that has an active session: `POST …&username=…` with `logout=1` and the token.
- Logout is best effort: a short timeout (5 s per profile), profiles in parallel, errors are logged at `WARNING` and do not block the shutdown.
- `reconnect` also logs out the old session before it drops it, if the session is still valid.

## Alternatives considered

- **No logout** – simpler, but the password and the query history stay in the server session.

## Consequences

- If the MCP client kills the process (SIGKILL), no logout happens. The session then expires on the server as before.
- Shutdown can take up to the logout timeout when the server does not respond (e.g. the VPN is disconnected).
