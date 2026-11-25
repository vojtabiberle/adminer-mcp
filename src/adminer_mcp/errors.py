"""Exceptions. Each one carries a message that is safe to show to the agent."""

from __future__ import annotations


class AdminerMcpError(Exception):
    """Base class for all errors of this package."""


class ConfigError(AdminerMcpError):
    """Invalid or missing configuration, or a password that cannot be resolved."""


class AuthError(AdminerMcpError):
    """Adminer rejected the login (invalid credentials or a database connection error)."""


class LockedOutError(AuthError):
    """Adminer brute-force protection blocks logins from this IP address."""

    def __init__(self, message: str, minutes: int | None) -> None:
        super().__init__(message)
        self.minutes = minutes


class SessionError(AdminerMcpError):
    """The session expired again right after a new login."""


class PolicyError(AdminerMcpError):
    """The SQL classifier or the profile configuration rejected the request."""


class ProtocolError(AdminerMcpError):
    """Adminer returned a response that the parser does not understand."""


class AdminerConnectionError(AdminerMcpError):
    """Network error, timeout or TLS error while talking to Adminer."""


class UnsupportedError(AdminerMcpError):
    """The driver or the feature is not supported (yet)."""
