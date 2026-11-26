# Contributing

Thank you for your interest. Issues and pull requests are welcome.

## Before you write code

This project documents first and implements second (see [docs/README.md](docs/README.md#process-prd--rfc--adr)):

- A new capability or a visible behavior change (new tool, new driver, changed output) starts with a **PRD** in `docs/prd/`.
- A change to the architecture, the Adminer protocol or the tool contract needs an **RFC** in `docs/rfc/`.
- Each decision is recorded as an **ADR** in `docs/adr/`. Accepted ADRs are not edited; a new ADR supersedes them.

For a bug fix or a small improvement, an issue or a PR with a clear description is enough.

## Development

Setup, the Docker test environment and the test layers are described in [docs/development.md](docs/development.md). In short:

```bash
uv sync
docker compose -f docker/compose.yml up -d --build --wait
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src tests
uv run pytest
```

All checks must pass. Add a regression test for each bug fix. Parser changes need a fixture recorded from the Docker environment (`uv run python tests/fixtures/record.py`); never use HTML from a real server, it would contain real data.

## Pull requests

- Keep a PR focused on one change, with commits that each make sense on their own.
- Update the reference documents in `docs/` when behavior changes.
- Write documentation and code comments in English.
