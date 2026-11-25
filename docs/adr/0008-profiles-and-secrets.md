# ADR-0008: Multiple profiles, passwords from env or keyring, database optional and selectable per call

- **Status:** Accepted
- **Date:** 2026-11-25
- **Related:** [configuration.md](../configuration.md), [tools.md](../tools.md)

## Context

- The user works with several Adminer instances, servers and users (e.g. read-only production, staging with writes).
- Passwords must not be in tool parameters (they would end up in the agent transcript) or in the MCP client configuration, which can be committed by accident.
- Adminer allows login without a database. In Adminer, the database is selected by the `db` URL parameter, without a new login.

## Decision

- Profiles are in `~/.config/adminer-mcp/profiles.toml`. A simple `default` profile can also be set with environment variables.
- The profile password is a reference: `{ env = "…" }` or `{ keyring = … }`. Plain text in the file is not supported. For the single-profile env mode, `ADMINER_PASSWORD` is acceptable, because it is an environment variable.
- `database` and `schema` are optional in the profile. All tools accept `database` and `schema` as call parameters, which take precedence. The tools are stateless and do not remember the database selection.
- The agent selects a profile by name with the `profile` parameter. The default is `default_profile`.
- The session (cookies) is separate per profile and only in memory.

## Alternatives considered

- **A `connect(driver, server, username, password)` tool** – the password would pass through the agent. Rejected.
- **A stateful `use_database` tool** – hidden state between calls causes mistakes when the agent works with several databases or when calls interleave. Rejected in favor of a parameter.
- **Password in `profiles.toml`** – simple, but the file is easily backed up or shared.

## Consequences

- On Linux, Secret Service must be available and unlocked for the keyring.
- A missing password shows up only at the first use of the profile. `adminer-mcp check` verifies it.
- A change in `profiles.toml` needs a restart of the MCP server.
