"""HTTP session with one Adminer instance for one profile (docs/architecture.md)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from enum import StrEnum
from typing import NoReturn

import httpx

from adminer_mcp import parser
from adminer_mcp.config import Profile, resolve_password, resolve_secret
from adminer_mcp.dialects.base import Dialect
from adminer_mcp.errors import (
    AdminerConnectionError,
    AdminerPageError,
    AuthError,
    LockedOutError,
    ProtocolError,
    SessionError,
)
from adminer_mcp.models import SqlPage

logger = logging.getLogger(__name__)

TESTED_VERSIONS = frozenset({"6.3.1"})
LOGOUT_TIMEOUT = 5.0
_CSRF_ERROR = "Invalid CSRF token"


class State(StrEnum):
    NOT_CONNECTED = "not_connected"
    CONNECTED = "connected"
    AUTH_FAILED = "auth_failed"
    LOCKED_OUT = "locked_out"


class AdminerClient:
    """Login, CSRF token, relogin and logout for one profile. Requests are serialized."""

    def __init__(
        self,
        profile: Profile,
        dialect: Dialect,
        *,
        password_resolver: Callable[[Profile], str] = resolve_password,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.profile = profile
        self.dialect = dialect
        self.state = State.NOT_CONNECTED
        self.version: str | None = None
        self.version_warning: str | None = None
        self._password_resolver = password_resolver
        self._token: str | None = None
        self._auth_error: AuthError | None = None
        self._lock = asyncio.Lock()
        auth = None
        if profile.basic_auth is not None:
            basic_password = resolve_secret(
                profile.basic_auth.password, f"{profile.name}/basic_auth"
            )
            auth = httpx.BasicAuth(profile.basic_auth.username, basic_password)
        self._http = httpx.AsyncClient(
            auth=auth,
            timeout=profile.timeout,
            verify=profile.verify_tls,
            follow_redirects=False,
            headers={"User-Agent": "adminer-mcp"},
            transport=transport,
        )
        self._reset_cookies()

    # -- URLs -----------------------------------------------------------------

    def _params(self, database: str | None, schema: str | None) -> dict[str, str]:
        profile = self.profile
        params: dict[str, str] = {}
        if self.dialect.adminer_driver != "server" or profile.server:
            params[self.dialect.adminer_driver] = profile.server
        params["username"] = profile.username
        if database:
            params["db"] = database
            if self.dialect.supports_schemas:
                # Without ns, Adminer redirects to the default schema and the POST data is lost.
                # An empty ns keeps the default search_path of the user.
                params["ns"] = schema or ""
        return params

    # -- session ----------------------------------------------------------------

    def _reset_cookies(self) -> None:
        self._http.cookies.clear()
        self._http.cookies.set("adminer_lang", "en")
        self._token = None

    async def _request(
        self, method: str, params: dict[str, str] | None = None, data: dict[str, str] | None = None,
        *, follow_redirects: bool = False, timeout: float | None = None,
    ) -> httpx.Response:  # fmt: skip
        started = time.monotonic()
        try:
            response = await self._http.request(
                method,
                self.profile.url,
                params=params,
                data=data,
                follow_redirects=follow_redirects,
                timeout=timeout if timeout is not None else self.profile.timeout,
            )
        except httpx.TimeoutException:
            raise AdminerConnectionError(
                f"Adminer did not answer within {self.profile.timeout:g} s"
                f" (profile {self.profile.name!r}). Is the VPN connected?"
            ) from None
        except httpx.HTTPError as error:
            raise AdminerConnectionError(
                f"Cannot reach Adminer for profile {self.profile.name!r}: {error}."
                " Is the VPN connected?"
            ) from None
        logger.info(
            "profile=%s %s status=%s %.2fs",
            self.profile.name,
            method,
            response.status_code,
            time.monotonic() - started,
        )
        if response.status_code == 401:
            raise AuthError(
                f"HTTP 401 from the server in front of Adminer (profile {self.profile.name!r}):"
                " check basic_auth."
            )
        if response.status_code >= 500:
            raise ProtocolError(
                f"HTTP {response.status_code} from Adminer: {_excerpt(response.text)}"
            )
        return response

    async def login(self) -> None:
        """Log in once. A failed login is never retried automatically (brute-force protection)."""
        if self._auth_error is not None:
            raise self._auth_error
        password = self._password_resolver(self.profile)
        self._reset_cookies()
        self.state = State.NOT_CONNECTED

        page = await self._request("GET")
        token = parser.extract_token(page.text)
        if token is None:
            raise ProtocolError(f"No login form at {self.profile.url}: {_excerpt(page.text)}")

        response = await self._request(
            "POST",
            data={
                "auth[driver]": self.dialect.adminer_driver,
                "auth[server]": self.profile.server,
                "auth[username]": self.profile.username,
                "auth[password]": password,
                "auth[db]": self.profile.database or "",
                "token": token,
            },
        )
        if response.is_redirect:
            location = response.headers.get("location", "")
            response = await self._http_get_absolute(location)

        html = response.text
        if parser.is_login_page(html):
            self._fail_login(parser.page_errors(html))
        token = parser.extract_token(html)
        if token is None:
            raise ProtocolError(f"No CSRF token after login: {_excerpt(html)}")
        self._token = token
        self.version = parser.extract_version(html)
        self.version_warning = None
        if self.version not in TESTED_VERSIONS:
            self.version_warning = (
                f"Adminer version {self.version or 'unknown'} is not tested with this MCP server"
                f" (tested: {', '.join(sorted(TESTED_VERSIONS))}). Results may be parsed wrongly."
            )
            logger.warning(self.version_warning)
        self.state = State.CONNECTED

    async def _http_get_absolute(self, location: str) -> httpx.Response:
        url = httpx.URL(self.profile.url).join(location)
        try:
            return await self._http.get(url, follow_redirects=True)
        except httpx.HTTPError as error:
            raise AdminerConnectionError(f"Cannot reach Adminer: {error}") from None

    def _fail_login(self, errors: list[str]) -> NoReturn:
        detail = "; ".join(errors) or "Adminer returned the login form without an error message."
        minutes = parser.locked_out_minutes(errors)
        if parser.is_locked_out(errors):
            self.state = State.LOCKED_OUT
            error: AuthError = LockedOutError(
                f"Adminer blocks logins from this IP address: {detail}", minutes
            )
        else:
            self.state = State.AUTH_FAILED
            error = AuthError(
                f"Adminer rejected the login for profile {self.profile.name!r}: {detail}"
                " The MCP server will not try again (Adminer blocks an IP address after 30 failed"
                " logins). Fix the password or the profile, then call reconnect."
            )
        self._auth_error = error
        self._reset_cookies()
        raise error

    async def run_sql(
        self, sql: str, *, database: str | None, schema: str | None, limit: int
    ) -> SqlPage:
        """Send SQL to the SQL command page and parse the response."""
        async with self._lock:
            if self.state != State.CONNECTED:
                await self.login()
            params = {**self._params(database, schema), "sql": ""}
            relogged = False
            csrf_retried = False
            while True:
                assert self._token is not None
                response = await self._request(
                    "POST",
                    params=params,
                    data={
                        "query": sql,
                        "limit": str(limit),
                        "error_stops": "1",
                        "token": self._token,
                    },
                )
                if response.is_redirect:
                    raise ProtocolError(
                        "Adminer redirected the SQL request to"
                        f" {response.headers.get('location', '?')}; the request was not run."
                    )
                html = response.text
                if parser.is_login_page(html):
                    if relogged:
                        self.state = State.NOT_CONNECTED
                        raise SessionError(
                            "The Adminer session expired again right after a new login."
                        )
                    logger.info("profile=%s session expired, logging in again", self.profile.name)
                    relogged = True
                    await self.login()
                    continue
                errors = parser.page_errors(html)
                if any(error.startswith(_CSRF_ERROR) for error in errors) and not csrf_retried:
                    csrf_retried = True
                    self._token = parser.extract_token(html) or self._token
                    continue
                if errors:
                    raise AdminerPageError(self._page_error_message(errors, database, schema))
                return parser.parse_sql_page(html)

    def _page_error_message(
        self, errors: list[str], database: str | None, schema: str | None
    ) -> str:
        message = "Adminer reported: " + "; ".join(errors)
        if errors == ["Not found."] and schema:
            message += f" Schema {schema!r} does not exist in database {database!r}."
        return message

    async def logout(self) -> None:
        """Best effort: log out so that the password does not stay in the server session."""
        if self.state != State.CONNECTED or self._token is None:
            return
        try:
            await self._request(
                "POST",
                params=self._params(None, None),
                data={"logout": "1", "token": self._token},
                timeout=LOGOUT_TIMEOUT,
            )
        except Exception as error:  # logout must never block the shutdown
            logger.warning("profile=%s logout failed: %s", self.profile.name, error)
        else:
            logger.info("profile=%s logged out", self.profile.name)
        finally:
            self.state = State.NOT_CONNECTED
            self._reset_cookies()

    async def reconnect(self) -> None:
        """Log out, forget a failed login and try one new login (the `reconnect` tool)."""
        async with self._lock:
            await self.logout()
            self._auth_error = None
            self.state = State.NOT_CONNECTED
            await self.login()

    async def aclose(self) -> None:
        await self._http.aclose()


def _excerpt(text: str, length: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= length else text[:length] + "…"
