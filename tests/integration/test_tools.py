from __future__ import annotations

from typing import Any

import httpx
import pytest
from mcp.client import Client

from adminer_mcp.client import State
from adminer_mcp.config import Config
from adminer_mcp.errors import AdminerMcpError, AuthError, PolicyError
from adminer_mcp.server import create_server
from adminer_mcp.service import AdminerService

from .conftest import ADMINER_URL


def only(result: dict[str, Any]) -> dict[str, Any]:
    [statement] = result["statements"]
    assert isinstance(statement, dict)
    return statement


async def count_log(service: AdminerService) -> int:
    result = await service.query("SELECT count(*) FROM audit.log", "admin", None, None, None)
    return int(only(result)["rows"][0][0])


class TestMetadata:
    async def test_list_profiles(self, service: AdminerService) -> None:
        profiles = {p["name"]: p for p in service.list_profiles()}
        assert profiles["reader"]["default"]
        assert profiles["admin"]["allow_ddl"]
        assert "password" not in profiles["reader"]

    async def test_list_databases(self, service: AdminerService) -> None:
        result = await service.list_databases(None, None)
        assert result["databases"] == ["app", "other", "postgres"]

    async def test_list_schemas(self, service: AdminerService) -> None:
        assert (await service.list_schemas(None, None))["schemas"] == ["audit", "public"]

    async def test_list_tables(self, service: AdminerService) -> None:
        tables = (await service.list_tables(None, None, None))["tables"]
        by_name = {(t["schema"], t["name"]): t for t in tables}
        assert by_name[("public", "open_orders")]["type"] == "view"
        assert by_name[("public", "customers")]["comment"] == "Customers of the shop"
        assert by_name[("public", "big")]["estimated_rows"] == 1500
        audit = (await service.list_tables(None, None, "audit"))["tables"]
        assert [t["name"] for t in audit] == ["log"]

    async def test_describe_table(self, service: AdminerService) -> None:
        table = await service.describe_table("orders", None, None, None)
        assert table["schema"] == "public" and table["type"] == "table"
        columns = {c["name"]: c for c in table["columns"]}
        assert columns["amount"]["type"] == "numeric(30,10)"
        assert columns["note"]["comment"] == "Free text"
        assert not columns["status"]["nullable"]
        assert columns["status"]["default"] == "'new'::text"
        assert table["primary_key"] == ["id"]
        assert {i["name"] for i in table["indexes"]} == {"orders_pkey", "orders_status_idx"}
        [fk] = table["foreign_keys"]
        assert fk["columns"] == ["customer_id"]
        assert fk["references"] == {"schema": "public", "table": "customers", "columns": ["id"]}

    async def test_describe_table_with_schema_prefix(self, service: AdminerService) -> None:
        table = await service.describe_table("audit.log", None, None, None)
        assert table["schema"] == "audit" and table["primary_key"] == ["id"]

    async def test_describe_unknown_table(self, service: AdminerService) -> None:
        with pytest.raises(AdminerMcpError, match="'nope' was not found"):
            await service.describe_table("nope", None, None, None)


class TestQuery:
    async def test_values(self, service: AdminerService) -> None:
        result = await service.query(
            "SELECT id, note, payload, code FROM orders ORDER BY id", None, None, None, None
        )
        statement = only(result)
        assert statement["kind"] == "result_set"
        assert [c["name"] for c in statement["columns"]] == ["id", "note", "payload", "code"]
        rows = statement["rows"]
        assert rows[0][1] is None and rows[1][1] == ""
        assert rows[2][1] == "line 1\nline 2\n\ttabbed"
        assert rows[0][2] == {"binary_bytes": 4}
        assert rows[0][3] == "ABC"
        assert result["warnings"] == []

    async def test_more_rows_than_returned_warns(self, service: AdminerService) -> None:
        result = await service.query("SELECT * FROM big", None, None, None, 5)
        statement = only(result)
        assert statement["returned_rows"] == 5
        assert statement["total_rows"] == 1500
        assert statement["truncated"]
        [warning] = result["warnings"]
        assert "Add LIMIT" in warning

    async def test_limit_in_sql_does_not_warn(self, service: AdminerService) -> None:
        result = await service.query("SELECT * FROM big LIMIT 5", None, None, None, 5)
        assert result["warnings"] == []

    async def test_schema_and_no_database(self, service: AdminerService) -> None:
        result = await service.query(
            "SELECT message FROM log WHERE id = 1", None, "app", "audit", None
        )
        assert only(result)["rows"] == [["created"]]
        result = await service.query("SELECT current_database()", "nodb", None, None, None)
        assert only(result)["rows"] == [["postgres"]]

    async def test_other_database(self, service: AdminerService) -> None:
        result = await service.query("SELECT current_database()", None, "other", None, None)
        assert only(result)["rows"] == [["other"]]

    async def test_several_statements_and_error(self, service: AdminerService) -> None:
        result = await service.query(
            "SELECT 1 AS a WHERE false; SELECT * FROM nope; SELECT 3", None, None, None, None
        )
        kinds = [s["kind"] for s in result["statements"]]
        assert kinds == ["empty", "error"]
        assert 'relation "nope" does not exist' in result["statements"][1]["error"]
        assert any("statements 3-3 did not run" in w for w in result["warnings"])

    async def test_write_is_rejected_before_sending(self, service: AdminerService) -> None:
        with pytest.raises(PolicyError, match="Use the execute tool"):
            await service.query("DELETE FROM audit.log WHERE id = 1", None, None, None, None)

    async def test_read_only_guard_stops_write_in_function(self, service: AdminerService) -> None:
        before = await count_log(service)
        result = await service.query("SELECT archive_orders()", "writer", None, None, None)
        statement = only(result)
        assert statement["kind"] == "error"
        assert "read-only transaction" in statement["error"]
        assert await count_log(service) == before

    async def test_explain(self, service: AdminerService) -> None:
        plan = only(await service.explain("SELECT * FROM orders", False, None, None, None))
        assert plan["columns"][0]["name"] == "QUERY PLAN"
        analyzed = only(await service.explain("SELECT * FROM orders", True, None, None, None))
        assert any("actual time" in row[0] for row in analyzed["rows"])
        with pytest.raises(PolicyError):
            await service.explain("DELETE FROM orders WHERE id = 1", True, None, None, None)


class TestExecute:
    async def test_read_only_profile(self, service: AdminerService) -> None:
        with pytest.raises(PolicyError, match="'reader' is read only"):
            await service.execute(
                "INSERT INTO audit.log (message) VALUES ('x')",
                False, False, False, "reader", None, None, None,
            )  # fmt: skip

    async def test_insert_returning(self, service: AdminerService) -> None:
        before = await count_log(service)
        result = await service.execute(
            "INSERT INTO audit.log (message) VALUES ('it') RETURNING message",
            False, False, False, "writer", None, None, None,
        )  # fmt: skip
        assert only(result)["rows"] == [["it"]]
        assert result["transaction"] == {
            "mode": "none",
            "outcome": "none",
            "failed_statement": None,
        }
        assert await count_log(service) == before + 1

    async def test_delete_without_where_is_rejected(self, service: AdminerService) -> None:
        with pytest.raises(PolicyError, match=r"changes every row of audit\.log"):
            await service.execute(
                "DELETE FROM audit.log", False, False, False, "writer", None, None, None
            )

    async def test_dry_run(self, service: AdminerService) -> None:
        result = await service.execute(
            "UPDATE orders SET status = 'dry' WHERE id = 1 RETURNING status",
            False, False, True, "writer", None, None, None,
        )  # fmt: skip
        assert [s["index"] for s in result["statements"]] == [1]
        assert only(result)["rows"] == [["dry"]]
        assert result["transaction"]["outcome"] == "dry_run_rolled_back"
        check = await service.query(
            "SELECT status FROM orders WHERE id = 1", None, None, None, None
        )
        assert only(check)["rows"] == [["new"]]

    async def test_dry_run_ddl(self, service: AdminerService) -> None:
        result = await service.execute(
            "CREATE TABLE dry_table (a int); INSERT INTO dry_table VALUES (1)",
            False, False, True, "admin", None, None, None,
        )  # fmt: skip
        assert result["transaction"]["outcome"] == "dry_run_rolled_back"
        tables = (await service.list_tables("admin", None, "public"))["tables"]
        assert "dry_table" not in {t["name"] for t in tables}

    async def test_ddl_needs_allow_ddl(self, service: AdminerService) -> None:
        with pytest.raises(PolicyError, match="needs allow_ddl"):
            await service.execute(
                "CREATE TABLE x (a int)", False, False, True, "writer", None, None, None
            )

    async def test_atomic_failure_saves_nothing(self, service: AdminerService) -> None:
        before = await count_log(service)
        result = await service.execute(
            "INSERT INTO audit.log (message) VALUES ('atomic'); INSERT INTO nope VALUES (1)",
            False, True, False, "writer", None, None, None,
        )  # fmt: skip
        assert result["transaction"] == {
            "mode": "atomic",
            "outcome": "rolled_back",
            "failed_statement": 2,
        }
        assert await count_log(service) == before

    async def test_atomic_success(self, service: AdminerService) -> None:
        before = await count_log(service)
        result = await service.execute(
            "INSERT INTO audit.log (message) VALUES ('a1'); INSERT INTO audit.log (message)"
            " VALUES ('a2') -- trailing comment",
            False, True, False, "writer", None, None, None,
        )  # fmt: skip
        assert [s["index"] for s in result["statements"]] == [1, 2]
        assert result["transaction"]["outcome"] == "committed"
        assert result["warnings"] == []
        assert await count_log(service) == before + 2

    async def test_begin_without_commit_is_rolled_back_by_adminer(
        self, service: AdminerService
    ) -> None:
        before = await count_log(service)
        result = await service.execute(
            "BEGIN; INSERT INTO audit.log (message) VALUES ('lost')",
            False, False, False, "writer", None, None, None,
        )  # fmt: skip
        assert result["transaction"]["mode"] == "explicit"
        assert result["transaction"]["outcome"] == "rolled_back_by_adminer"
        assert any("without COMMIT" in w for w in result["warnings"])
        assert any("rolled back an unfinished transaction" in w for w in result["warnings"])
        assert await count_log(service) == before

    async def test_autocommit_partial_failure(self, service: AdminerService) -> None:
        before = await count_log(service)
        result = await service.execute(
            "INSERT INTO audit.log (message) VALUES ('kept'); INSERT INTO nope VALUES (1);"
            " INSERT INTO audit.log (message) VALUES ('not run')",
            False, False, False, "writer", None, None, None,
        )  # fmt: skip
        assert result["transaction"]["failed_statement"] == 2
        assert any("stay applied" in w for w in result["warnings"])
        assert await count_log(service) == before + 1


class TestSession:
    async def test_wrong_password(self, service: AdminerService) -> None:
        for _ in range(2):
            with pytest.raises(AuthError, match="password authentication failed"):
                await service.query("SELECT 1", "wrong", None, None, None)
        [profile] = [p for p in service.list_profiles() if p["name"] == "wrong"]
        assert profile["state"] == State.AUTH_FAILED

    async def test_shutdown_logs_out(self, config: Config) -> None:
        service = AdminerService(config)
        await service.query("SELECT 1", None, None, None, None)
        client = service._clients["reader"]
        cookies = {
            c.name: c.value or "" for c in client._http.cookies.jar if c.name != "adminer_lang"
        }
        token = client._token
        assert token is not None
        await service.shutdown()
        async with httpx.AsyncClient(cookies=cookies) as http:
            response = await http.post(
                ADMINER_URL,
                params={
                    "pgsql": "postgres",
                    "username": "reader",
                    "db": "app",
                    "ns": "",
                    "sql": "",
                },
                data={"query": "SELECT 1", "limit": "1", "token": token},
            )
        assert "auth[password]" in response.text  # the old session no longer works


class TestMcpLayer:
    async def test_tools_and_calls(self, config: Config) -> None:
        async with Client(create_server(config)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(tools) == {
                "list_profiles", "list_databases", "list_schemas", "list_tables",
                "describe_table", "query", "explain", "reconnect", "execute",
            }  # fmt: skip
            assert tools["query"].annotations is not None
            assert tools["query"].annotations.read_only_hint
            result = await client.call_tool("query", {"sql": "SELECT 1 AS one"})
            assert not result.is_error
            assert result.structured_content is not None
            assert result.structured_content["statements"][0]["rows"] == [["1"]]
            error = await client.call_tool("query", {"sql": "DROP TABLE orders"})
            assert error.is_error
            assert "Use the execute tool" in error.content[0].text  # type: ignore[union-attr]

    async def test_execute_is_hidden_without_write_profiles(self, config: Config) -> None:
        read_only = Config(default_profile="reader", profiles={"reader": config.profiles["reader"]})
        async with Client(create_server(read_only)) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert "execute" not in names
