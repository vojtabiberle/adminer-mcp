"""Registry: Adminer driver id → Dialect."""

from __future__ import annotations

from adminer_mcp.dialects.base import Dialect
from adminer_mcp.dialects.pgsql import PostgresDialect
from adminer_mcp.errors import UnsupportedError

_DIALECTS: dict[str, Dialect] = {
    dialect.adminer_driver: dialect for dialect in (PostgresDialect(),)
}


def get_dialect(driver: str) -> Dialect:
    try:
        return _DIALECTS[driver]
    except KeyError:
        supported = ", ".join(sorted(_DIALECTS))
        raise UnsupportedError(
            f"Adminer driver {driver!r} is not supported yet. Supported: {supported}."
        ) from None


__all__ = ["Dialect", "get_dialect"]
