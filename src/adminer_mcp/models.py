"""Data structures parsed from Adminer responses."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Binary:
    """A binary value that is not UTF-8. Adminer prints only its size, not the content."""

    size: int


Cell = str | Binary | None


@dataclass(frozen=True)
class Column:
    name: str
    type: str | None
    table: str | None


@dataclass
class ResultSet:
    """A statement that returned rows."""

    columns: list[Column]
    rows: list[list[Cell]]
    total_rows: int


@dataclass
class Affected:
    """A statement without a result set."""

    affected_rows: int


@dataclass
class Empty:
    """A result set without rows. Adminer does not print the column names in this case."""


@dataclass
class StatementError:
    message: str


Outcome = ResultSet | Affected | Empty | StatementError


@dataclass
class Statement:
    """One statement as Adminer split and ran it (`pre id='sql-N'`)."""

    index: int
    sql: str
    outcome: Outcome | None = None
    warnings: str | None = None


@dataclass
class SqlPage:
    """The parsed response of the SQL command page."""

    statements: list[Statement] = field(default_factory=list)
    adminer_rolled_back: bool = False
    """Adminer printed `ROLLBACK -- Adminer`: an unfinished transaction was rolled back."""
