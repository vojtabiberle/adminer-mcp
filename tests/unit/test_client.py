from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from adminer_mcp.client import AdminerClient, State
from adminer_mcp.config import Profile
from adminer_mcp.dialects import get_dialect
from adminer_mcp.errors import (
    AdminerConnectionError,
    AdminerPageError,
    AuthError,
    LockedOutError,
    ProtocolError,
    SessionError,
)
from adminer_mcp.models import ResultSet

FIXTURES = Path(__file__).parent.parent / "fixtures" / "adminer-6.3.1"
URL = "https://adminer.test/adminer.php"


def load(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


class FakeAdminer:
    """Answers like Adminer, from the recorded fixtures."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.after_login: tuple[int, str] = (200, load("logged_in"))
        self.sql_responses: list[httpx.Response] = []
        self.logins = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        params = dict(request.url.params)
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        if request.method == "GET" and not params:
            return httpx.Response(200, text=load("login_page"))
        if request.method == "POST" and "auth[username]" in form:
            self.logins += 1
            return httpx.Response(303, headers={"location": "adminer.php?pgsql=db&username=u"})
        if request.method == "GET":
            status, html = self.after_login
            return httpx.Response(status, text=html)
        if request.method == "POST" and "sql" in params:
            return self.sql_responses.pop(0)
        if request.method == "POST" and form.get("logout") == "1":
            return httpx.Response(302, headers={"location": "adminer.php"})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    def sql_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST" and "sql" in r.url.params]


def make_client(
    fake: Callable[[httpx.Request], httpx.Response], **profile: object
) -> AdminerClient:
    data: dict[str, object] = {
        "name": "test",
        "url": URL,
        "driver": "pgsql",
        "server": "db",
        "username": "u",
        "password": {"env": "UNUSED"},
    }
    data.update(profile)
    return AdminerClient(
        Profile.model_validate(data),
        get_dialect("pgsql"),
        password_resolver=lambda _profile: "secret",
        transport=httpx.MockTransport(fake),
    )


async def test_login_and_query() -> None:
    fake = FakeAdminer()
    fake.sql_responses = [httpx.Response(200, text=load("sql_values"))]
    client = make_client(fake)
    page = await client.run_sql("SELECT 1", database="app", schema=None, limit=7)
    assert client.state == State.CONNECTED
    assert client.version == "6.3.1" and client.version_warning is None
    assert isinstance(page.statements[0].outcome, ResultSet)
    [sql_request] = fake.sql_requests()
    assert dict(sql_request.url.params) == {
        "pgsql": "db",
        "username": "u",
        "db": "app",
        "ns": "",  # always sent with a database, otherwise Adminer redirects and drops the POST
        "sql": "",
    }
    form = parse_qs(sql_request.content.decode())
    assert form["query"] == ["SELECT 1"]
    assert form["limit"] == ["7"]
    assert form["error_stops"] == ["1"]
    assert sql_request.headers.get("sec-fetch-site") is None
    assert "adminer_lang=en" in sql_request.headers["cookie"]


async def test_no_database_means_no_db_and_ns() -> None:
    fake = FakeAdminer()
    fake.sql_responses = [httpx.Response(200, text=load("sql_no_db"))]
    await make_client(fake).run_sql("SELECT 1", database=None, schema="ignored", limit=1)
    assert dict(fake.sql_requests()[0].url.params) == {"pgsql": "db", "username": "u", "sql": ""}


async def test_failed_login_is_not_retried() -> None:
    fake = FakeAdminer()
    fake.after_login = (403, load("login_failed"))
    client = make_client(fake)
    for _ in range(3):
        with pytest.raises(AuthError, match="will not try again"):
            await client.run_sql("SELECT 1", database=None, schema=None, limit=1)
    assert fake.logins == 1
    assert client.state == State.AUTH_FAILED


async def test_reconnect_clears_the_failed_state() -> None:
    fake = FakeAdminer()
    fake.after_login = (403, load("login_failed"))
    client = make_client(fake)
    with pytest.raises(AuthError):
        await client.run_sql("SELECT 1", database=None, schema=None, limit=1)
    fake.after_login = (200, load("logged_in"))
    await client.reconnect()
    assert client.state == State.CONNECTED
    assert fake.logins == 2


async def test_locked_out() -> None:
    fake = FakeAdminer()
    html = load("login_failed").replace(
        "Unable to connect to PostgreSQL server",
        "Too many unsuccessful logins, try again in 12 minute(s).",
    )
    fake.after_login = (403, html)
    client = make_client(fake)
    with pytest.raises(LockedOutError) as info:
        await client.run_sql("SELECT 1", database=None, schema=None, limit=1)
    assert info.value.minutes == 12
    assert client.state == State.LOCKED_OUT


async def test_expired_session_logs_in_once_again() -> None:
    fake = FakeAdminer()
    fake.sql_responses = [
        httpx.Response(403, text=load("session_expired")),
        httpx.Response(200, text=load("sql_values")),
    ]
    page = await make_client(fake).run_sql("SELECT 1", database="app", schema=None, limit=1)
    assert page.statements
    assert fake.logins == 2


async def test_session_expired_twice() -> None:
    fake = FakeAdminer()
    fake.sql_responses = [httpx.Response(403, text=load("session_expired")) for _ in range(2)]
    with pytest.raises(SessionError):
        await make_client(fake).run_sql("SELECT 1", database="app", schema=None, limit=1)


async def test_invalid_csrf_token_is_retried_with_a_new_token() -> None:
    fake = FakeAdminer()
    fake.sql_responses = [
        httpx.Response(403, text=load("csrf_invalid")),
        httpx.Response(200, text=load("sql_values")),
    ]
    await make_client(fake).run_sql("SELECT 1", database="app", schema=None, limit=1)
    first, second = (parse_qs(r.content.decode())["token"][0] for r in fake.sql_requests())
    assert first != second


async def test_unknown_schema() -> None:
    fake = FakeAdminer()
    fake.sql_responses = [httpx.Response(200, text=load("sql_bad_schema"))]
    with pytest.raises(AdminerPageError, match="Schema 'x' does not exist in database 'app'"):
        await make_client(fake).run_sql("SELECT 1", database="app", schema="x", limit=1)


async def test_redirect_of_sql_request_is_an_error() -> None:
    fake = FakeAdminer()
    fake.sql_responses = [httpx.Response(303, headers={"location": "adminer.php?x"})]
    with pytest.raises(ProtocolError, match="redirected"):
        await make_client(fake).run_sql("SELECT 1", database="app", schema=None, limit=1)


async def test_network_error_mentions_vpn() -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(AdminerConnectionError, match="VPN"):
        await make_client(unreachable).run_sql("SELECT 1", database=None, schema=None, limit=1)


async def test_untested_version_warns() -> None:
    fake = FakeAdminer()
    fake.after_login = (
        200,
        load("logged_in").replace("<span class='version'>6.3.1", "<span class='version'>7.0.0"),
    )
    fake.sql_responses = [httpx.Response(200, text=load("sql_values"))]
    client = make_client(fake)
    await client.run_sql("SELECT 1", database=None, schema=None, limit=1)
    assert client.version == "7.0.0"
    assert client.version_warning is not None and "not tested" in client.version_warning


async def test_logout() -> None:
    fake = FakeAdminer()
    fake.sql_responses = [httpx.Response(200, text=load("sql_values"))]
    client = make_client(fake)
    await client.run_sql("SELECT 1", database=None, schema=None, limit=1)
    await client.logout()
    assert client.state == State.NOT_CONNECTED
    logout = fake.requests[-1]
    assert parse_qs(logout.content.decode())["logout"] == ["1"]
    assert dict(logout.url.params) == {"pgsql": "db", "username": "u"}


async def test_logout_errors_are_ignored() -> None:
    fake = FakeAdminer()
    fake.sql_responses = [httpx.Response(200, text=load("sql_values"))]

    def failing_logout(request: httpx.Request) -> httpx.Response:
        if b"logout=1" in request.content:
            raise httpx.ConnectError("gone", request=request)
        return fake(request)

    client = make_client(failing_logout)
    await client.run_sql("SELECT 1", database=None, schema=None, limit=1)
    await client.logout()
    assert client.state == State.NOT_CONNECTED


async def test_logout_without_session_sends_nothing() -> None:
    fake = FakeAdminer()
    await make_client(fake).logout()
    assert fake.requests == []
