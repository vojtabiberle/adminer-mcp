"""Profiles and password resolution (docs/configuration.md, ADR-0008)."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from adminer_mcp.errors import ConfigError

KEYRING_SERVICE = "adminer-mcp"
ENV_PROFILE_NAME = "default"

# Adminer driver ids: built-in drivers and drivers from plugins/drivers/.
ADMINER_DRIVERS = frozenset(
    {
        "server",
        "pgsql",
        "sqlite",
        "mssql",
        "oracle",
        "mongo",
        "elastic",
        "clickhouse",
        "firebird",
        "simpledb",
        "imap",
        "redis",
        "igdb",
    }
)
DRIVER_ALIASES = {
    "mysql": "server",
    "mariadb": "server",
    "postgres": "pgsql",
    "postgresql": "pgsql",
}


class SecretRef(BaseModel):
    """Where to find a password: an environment variable or the system keyring."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    env: str | None = None
    keyring: bool | str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.env is None) == (self.keyring in (None, False)):
            raise ValueError('set exactly one of "env" or "keyring"')
        return self


class BasicAuth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    username: str
    password: SecretRef


class Limits(BaseModel):
    """Values that `[defaults]` can set for all profiles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_rows: int = Field(default=100, ge=1)
    max_rows_limit: int = Field(default=1000, ge=1)
    max_cell_chars: int = Field(default=2000, ge=10)
    timeout: float = Field(default=60, gt=0)
    verify_tls: bool = True


class Profile(Limits):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    name: str
    url: str
    driver: str
    server: str = ""
    username: str
    password: SecretRef
    database: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    allow_write: bool = False
    allow_ddl: bool = False
    basic_auth: BasicAuth | None = None
    description: str | None = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("must be an http:// or https:// URL")
        if parts.query or parts.fragment:
            raise ValueError("must not contain a query string or a fragment")
        return value

    @field_validator("driver")
    @classmethod
    def _check_driver(cls, value: str) -> str:
        driver = DRIVER_ALIASES.get(value.lower(), value.lower())
        if driver not in ADMINER_DRIVERS:
            known = ", ".join(sorted(ADMINER_DRIVERS | DRIVER_ALIASES.keys()))
            raise ValueError(f"unknown Adminer driver {value!r}; known: {known}")
        return driver

    @field_validator("database", "schema_name")
    @classmethod
    def _empty_is_none(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def _check_limits(self) -> Self:
        if self.max_rows > self.max_rows_limit:
            raise ValueError("max_rows must not be greater than max_rows_limit")
        if self.allow_ddl and not self.allow_write:
            raise ValueError("allow_ddl needs allow_write")
        return self


class Config(BaseModel):
    model_config = ConfigDict(frozen=True)

    default_profile: str
    profiles: dict[str, Profile]

    def get(self, name: str | None) -> Profile:
        key = name or self.default_profile
        try:
            return self.profiles[key]
        except KeyError:
            known = ", ".join(sorted(self.profiles))
            raise ConfigError(f"Unknown profile {key!r}. Configured profiles: {known}.") from None

    @property
    def any_write(self) -> bool:
        return any(profile.allow_write for profile in self.profiles.values())


def default_config_path(env: Mapping[str, str]) -> Path:
    if env.get("ADMINER_MCP_CONFIG"):
        return Path(env["ADMINER_MCP_CONFIG"]).expanduser()
    base = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    return base / "adminer-mcp" / "profiles.toml"


def _bool_env(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _env_profile(env: Mapping[str, str]) -> dict[str, Any] | None:
    if not env.get("ADMINER_URL"):
        return None
    data: dict[str, Any] = {
        "url": env["ADMINER_URL"],
        "driver": env.get("ADMINER_DRIVER", ""),
        "server": env.get("ADMINER_SERVER", ""),
        "username": env.get("ADMINER_USERNAME", ""),
        "password": {"env": "ADMINER_PASSWORD"},
        "database": env.get("ADMINER_DATABASE"),
        "schema": env.get("ADMINER_SCHEMA"),
        "allow_write": _bool_env(env.get("ADMINER_ALLOW_WRITE")),
        "allow_ddl": _bool_env(env.get("ADMINER_ALLOW_DDL")),
    }
    return data


def _format_validation_error(profile: str, error: ValidationError) -> str:
    problems = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "profile"
        problems.append(f"{location}: {item['msg']}")
    return f"Invalid profile {profile!r}: " + "; ".join(problems)


def load_config(env: Mapping[str, str] | None = None, path: Path | None = None) -> Config:
    """Load profiles from the TOML file and the ADMINER_* environment variables."""
    env = os.environ if env is None else env
    path = path or default_config_path(env)

    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            raise ConfigError(f"Cannot parse {path}: {error}") from None

    unknown_keys = set(raw) - {"default_profile", "defaults", "profiles"}
    if unknown_keys:
        raise ConfigError(f"Unknown keys in {path}: {', '.join(sorted(unknown_keys))}")

    defaults = raw.get("defaults", {})
    try:
        Limits.model_validate(defaults)
    except ValidationError as error:
        raise ConfigError(_format_validation_error("[defaults]", error)) from None

    sources: dict[str, dict[str, Any]] = dict(raw.get("profiles", {}))
    env_profile = _env_profile(env)
    if env_profile is not None:
        if ENV_PROFILE_NAME in sources:
            raise ConfigError(
                f"Profile {ENV_PROFILE_NAME!r} is defined both in {path} and by ADMINER_URL."
            )
        sources[ENV_PROFILE_NAME] = env_profile

    if not sources:
        raise ConfigError(
            f"No profiles configured. Create {path} or set ADMINER_URL and the other "
            "ADMINER_* variables (see docs/configuration.md)."
        )

    profiles: dict[str, Profile] = {}
    for name, data in sources.items():
        try:
            profiles[name] = Profile.model_validate({**defaults, **data, "name": name})
        except ValidationError as error:
            raise ConfigError(_format_validation_error(name, error)) from None

    default_profile = raw.get("default_profile")
    if default_profile is None:
        if len(profiles) > 1:
            raise ConfigError(f"Set default_profile in {path}: there is more than one profile.")
        default_profile = next(iter(profiles))
    if default_profile not in profiles:
        raise ConfigError(f"default_profile {default_profile!r} is not a configured profile.")

    return Config(default_profile=default_profile, profiles=profiles)


def resolve_secret(
    ref: SecretRef, keyring_account: str, env: Mapping[str, str] | None = None
) -> str:
    """Return the password. Raise ConfigError with a hint if it is missing or empty."""
    env = os.environ if env is None else env
    if ref.env is not None:
        value = env.get(ref.env)
        if not value:
            raise ConfigError(f"Environment variable {ref.env} is not set or is empty.")
        return value

    import keyring  # imported lazily: the keyring backend can be slow to initialize

    account = ref.keyring if isinstance(ref.keyring, str) else keyring_account
    try:
        value = keyring.get_password(KEYRING_SERVICE, account)
    except Exception as error:  # the keyring backend raises various error types
        raise ConfigError(f"Cannot read the password from the keyring: {error}") from None
    if not value:
        raise ConfigError(
            f"No password in the keyring for service {KEYRING_SERVICE!r}, account {account!r}. "
            f"Run: adminer-mcp set-password {keyring_account}"
        )
    return value


def resolve_password(profile: Profile, env: Mapping[str, str] | None = None) -> str:
    return resolve_secret(profile.password, profile.name, env)
