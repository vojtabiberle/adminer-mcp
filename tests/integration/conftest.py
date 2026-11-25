"""Integration tests against the Docker environment (docker/compose.yml)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from adminer_mcp.config import Config, load_config
from adminer_mcp.service import AdminerService

ADMINER_URL = "http://127.0.0.1:8088/adminer.php"

PROFILES = f"""
default_profile = "reader"

[profiles.reader]
url = "{ADMINER_URL}"
driver = "pgsql"
server = "postgres"
username = "reader"
password = {{ env = "IT_READER_PW" }}
database = "app"

[profiles.writer]
url = "{ADMINER_URL}"
driver = "pgsql"
server = "postgres"
username = "writer"
password = {{ env = "IT_WRITER_PW" }}
database = "app"
allow_write = true

[profiles.admin]
url = "{ADMINER_URL}"
driver = "pgsql"
server = "postgres"
username = "postgres"
password = {{ env = "IT_ADMIN_PW" }}
database = "app"
allow_write = true
allow_ddl = true

[profiles.nodb]
url = "{ADMINER_URL}"
driver = "pgsql"
server = "postgres"
username = "reader"
password = {{ env = "IT_READER_PW" }}

[profiles.wrong]
url = "{ADMINER_URL}"
driver = "pgsql"
server = "postgres"
username = "reader"
password = {{ env = "IT_WRONG_PW" }}
"""


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/integration" in str(item.path):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session", autouse=True)
def _adminer_available() -> None:
    try:
        httpx.get(ADMINER_URL, timeout=3)
    except httpx.HTTPError:
        pytest.skip(
            "Adminer test environment is not running (docker compose -f docker/compose.yml up)"
        )


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("IT_READER_PW", "reader-test-pw")
    monkeypatch.setenv("IT_WRITER_PW", "writer-test-pw")
    monkeypatch.setenv("IT_ADMIN_PW", "postgres-test-pw")
    monkeypatch.setenv("IT_WRONG_PW", "wrong-password")
    path = tmp_path / "profiles.toml"
    path.write_text(PROFILES, encoding="utf-8")
    return load_config(env={}, path=path)


@pytest.fixture
async def service(config: Config) -> AsyncIterator[AdminerService]:
    service = AdminerService(config)
    try:
        yield service
    finally:
        await service.shutdown()
