from __future__ import annotations

import pytest

from adminer_mcp.dialects import get_dialect
from adminer_mcp.errors import UnsupportedError


def test_pgsql_is_registered() -> None:
    dialect = get_dialect("pgsql")
    assert dialect.adminer_driver == "pgsql"
    assert dialect.read_only_prelude() == "SET default_transaction_read_only = on;"


def test_unsupported_driver() -> None:
    with pytest.raises(UnsupportedError, match=r"'server' is not supported yet\. Supported: pgsql"):
        get_dialect("server")


def test_literals_are_quoted() -> None:
    dialect = get_dialect("pgsql")
    assert dialect.quote_literal("o'brien") == "'o''brien'"
    sql = dialect.sql_list_tables("x' OR '1'='1")
    assert "n.nspname = 'x'' OR ''1''=''1'" in sql
    [table_info, *_] = dialect.sql_describe_table("t'", "s")
    assert "quote_ident('s') || '.' || quote_ident('t''')" in table_info
