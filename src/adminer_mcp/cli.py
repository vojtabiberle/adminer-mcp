"""Command line: `adminer-mcp` (MCP server on stdio), `set-password`, `check`."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import signal
import sys
from typing import TYPE_CHECKING, Any

import anyio

from adminer_mcp import __version__
from adminer_mcp.config import KEYRING_SERVICE, load_config
from adminer_mcp.errors import AdminerMcpError
from adminer_mcp.service import SHUTDOWN_TIMEOUT, AdminerService

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer


def _setup_logging() -> None:
    level = os.environ.get("ADMINER_MCP_LOG_LEVEL", "INFO").upper()
    # stdout belongs to the MCP protocol; logs go to stderr.
    logging.basicConfig(
        level=level, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def _serve_until_signal(server: MCPServer[Any], service: AdminerService) -> None:
    """Run the stdio server. On SIGTERM or SIGINT log out every profile and exit (ADR-0011).

    The normal end (the client closes stdin) logs out in the server lifespan. A signal cannot
    use that path: the stdio transport blocks in a thread that reads stdin, so cancelling the
    server never finishes. The signal handler logs out itself and then exits the process.
    """
    async with anyio.create_task_group() as group:

        async def watch_signals() -> None:
            with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
                async for signum in signals:
                    logging.getLogger(__name__).info("received signal %s, shutting down", signum)
                    with anyio.move_on_after(SHUTDOWN_TIMEOUT):
                        await service.shutdown()
                    logging.shutdown()
                    os._exit(0)

        group.start_soon(watch_signals)
        await server.run_stdio_async()
        group.cancel_scope.cancel()


def _serve() -> int:
    from adminer_mcp.server import create_server

    config = load_config()
    service = AdminerService(config)
    anyio.run(_serve_until_signal, create_server(config, service), service)
    return 0


def _set_password(profile_name: str) -> int:
    import keyring

    config = load_config()
    profile = config.get(profile_name)
    if profile.password.keyring in (None, False):
        print(
            f"Profile {profile.name!r} does not read its password from the keyring"
            f" (password = {profile.password.model_dump(exclude_none=True)}).",
            file=sys.stderr,
        )
        return 2
    account = (
        profile.password.keyring if isinstance(profile.password.keyring, str) else profile.name
    )
    password = getpass.getpass(f"Password for profile {profile.name!r} ({profile.username}): ")
    if not password:
        print("Empty password: nothing stored.", file=sys.stderr)
        return 2
    keyring.set_password(KEYRING_SERVICE, account, password)
    print(f"Stored in the keyring: service {KEYRING_SERVICE!r}, account {account!r}.")
    return 0


async def _check(profile_name: str | None) -> int:
    config = load_config()
    service = AdminerService(config)
    try:
        result = await service.query(
            "SELECT current_database(), current_user, version()", profile_name, None, None, 1
        )
        reconnect_info = service.list_profiles()
    finally:
        await service.shutdown()
    [statement] = result["statements"]
    database, user, version = statement["rows"][0]
    profile = next(p for p in reconnect_info if p["name"] == result["profile"])
    print(f"Profile:  {result['profile']}")
    print(f"Adminer:  {profile['adminer_version']}")
    print(f"Database: {database} (user {user})")
    print(f"Server:   {version}")
    for warning in result["warnings"]:
        print(f"Warning:  {warning}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="adminer-mcp", description=__doc__)
    parser.add_argument("--version", action="version", version=f"adminer-mcp {__version__}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="run the MCP server on stdio (default)")
    store = commands.add_parser("set-password", help="store a profile password in the keyring")
    store.add_argument("profile")
    check = commands.add_parser("check", help="log in with a profile and print the server info")
    check.add_argument("profile", nargs="?")
    args = parser.parse_args(argv)

    _setup_logging()
    try:
        if args.command == "set-password":
            return _set_password(args.profile)
        if args.command == "check":
            return asyncio.run(_check(args.profile))
        return _serve()
    except AdminerMcpError as error:
        print(f"adminer-mcp: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
