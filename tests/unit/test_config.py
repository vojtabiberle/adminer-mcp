from __future__ import annotations

from pathlib import Path

import pytest

from adminer_mcp import config
from adminer_mcp.config import SecretRef, load_config, resolve_password, resolve_secret
from adminer_mcp.errors import ConfigError

TOML = """
default_profile = "prod"

[defaults]
max_rows = 50

[profiles.prod]
url = "https://admin.example.com/adminer.php"
driver = "PostgreSQL"
server = "db:5432"
username = "reader"
password = { keyring = true }

[profiles.staging]
url = "https://staging.example.com/"
driver = "pgsql"
username = "app"
password = { env = "STAGING_PW" }
database = "app"
schema = "public"
allow_write = true
allow_ddl = true
max_rows = 10

[profiles.staging.basic_auth]
username = "team"
password = { env = "STAGING_BASIC" }
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "profiles.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_profiles_from_file(tmp_path: Path) -> None:
    cfg = load_config(env={}, path=write(tmp_path, TOML))
    assert cfg.default_profile == "prod"
    prod = cfg.get(None)
    assert prod.name == "prod"
    assert prod.driver == "pgsql"  # alias, case-insensitive
    assert prod.database is None and prod.schema_name is None
    assert prod.max_rows == 50  # from [defaults]
    assert not prod.allow_write
    staging = cfg.get("staging")
    assert staging.max_rows == 10  # the profile overrides [defaults]
    assert staging.schema_name == "public"
    assert staging.basic_auth is not None and staging.basic_auth.username == "team"
    assert cfg.any_write


def test_env_profile(tmp_path: Path) -> None:
    env = {
        "ADMINER_URL": "http://localhost:8088/adminer.php",
        "ADMINER_DRIVER": "pgsql",
        "ADMINER_SERVER": "postgres",
        "ADMINER_USERNAME": "reader",
        "ADMINER_PASSWORD": "secret",
        "ADMINER_DATABASE": "",
        "ADMINER_ALLOW_WRITE": "1",
    }
    cfg = load_config(env=env, path=tmp_path / "missing.toml")
    profile = cfg.get(None)
    assert profile.name == "default"
    assert profile.database is None  # empty means none
    assert profile.allow_write
    assert resolve_password(profile, env) == "secret"


def test_env_profile_collides_with_file(tmp_path: Path) -> None:
    text = TOML.replace("[profiles.prod]", "[profiles.default]").replace('"prod"', '"default"')
    with pytest.raises(ConfigError, match="both"):
        load_config(env={"ADMINER_URL": "http://x/"}, path=write(tmp_path, text))


def test_no_profiles(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="No profiles configured"):
        load_config(env={}, path=tmp_path / "missing.toml")


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('driver = "PostgreSQL"', 'driver = "access"', "unknown Adminer driver"),
        ("adminer.php", "adminer.php?pgsql=", "query string"),
        ("{ keyring = true }", '{ keyring = true, env = "X" }', "exactly one"),
        ("allow_write = true\n", "", "allow_ddl needs allow_write"),
    ],
)
def test_invalid_profile(tmp_path: Path, old: str, new: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(env={}, path=write(tmp_path, TOML.replace(old, new)))


def test_max_rows_above_limit(tmp_path: Path) -> None:
    text = TOML.replace("max_rows = 50", "max_rows = 5000")
    with pytest.raises(ConfigError, match="max_rows_limit"):
        load_config(env={}, path=write(tmp_path, text))


def test_default_profile_required_for_several_profiles(tmp_path: Path) -> None:
    text = TOML.replace('default_profile = "prod"', "")
    with pytest.raises(ConfigError, match="default_profile"):
        load_config(env={}, path=write(tmp_path, text))


def test_unknown_profile(tmp_path: Path) -> None:
    cfg = load_config(env={}, path=write(tmp_path, TOML))
    with pytest.raises(ConfigError, match="Unknown profile 'nope'"):
        cfg.get("nope")


def test_missing_env_password() -> None:
    with pytest.raises(ConfigError, match="STAGING_PW is not set"):
        resolve_secret(SecretRef(env="STAGING_PW"), "staging", env={})


def test_keyring_password(monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring

    stored = {("adminer-mcp", "prod"): "kr-secret"}
    monkeypatch.setattr(
        keyring, "get_password", lambda service, account: stored.get((service, account))
    )
    assert resolve_secret(SecretRef(keyring=True), "prod", env={}) == "kr-secret"
    with pytest.raises(ConfigError, match="adminer-mcp set-password other"):
        resolve_secret(SecretRef(keyring=True), "other", env={})


def test_default_config_path() -> None:
    assert config.default_config_path({"ADMINER_MCP_CONFIG": "/x/p.toml"}) == Path("/x/p.toml")
    assert config.default_config_path({"XDG_CONFIG_HOME": "/cfg"}) == Path(
        "/cfg/adminer-mcp/profiles.toml"
    )
