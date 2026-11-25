"""Everything that differs between Adminer drivers (ADR-0009)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar


class Dialect(ABC):
    adminer_driver: ClassVar[str]
    """Driver id in the URL and in auth[driver]."""

    sqlglot_dialect: ClassVar[str]
    """Dialect name for sqlglot (classifier)."""

    supports_schemas: ClassVar[bool]
    """Whether the `schema` (Adminer `ns`) parameter applies."""

    transactional_ddl: ClassVar[bool]
    """DDL can run inside a transaction and is rolled back with it."""

    supports_dry_run: ClassVar[bool]
    """`execute(dry_run=True)` is enabled for this dialect (ADR-0014)."""

    side_effect_functions: ClassVar[tuple[str, ...]] = ()
    """fnmatch patterns of functions that `query` rejects (docs/sql-safety.md)."""

    non_rollbackable_functions: ClassVar[tuple[str, ...]] = ()
    """fnmatch patterns of functions whose effects a rollback does not undo (ADR-0014)."""

    guarded_settings: ClassVar[frozenset[str]] = frozenset()
    """Settings that `query` must not change, because they would turn off the read-only guard."""

    begin_sql: ClassVar[str] = "BEGIN;"
    commit_sql: ClassVar[str] = "COMMIT;"
    rollback_sql: ClassVar[str] = "ROLLBACK;"

    @abstractmethod
    def read_only_prelude(self) -> str:
        """SQL that makes the rest of the request read-only without a transaction (ADR-0005)."""

    @abstractmethod
    def sql_list_databases(self) -> str: ...

    @abstractmethod
    def sql_list_schemas(self) -> str: ...

    @abstractmethod
    def sql_list_tables(self, schema: str | None) -> str: ...

    @abstractmethod
    def sql_describe_table(self, table: str, schema: str | None) -> list[str]:
        """Statements for: table info, columns, indexes, foreign keys (in this order)."""

    @abstractmethod
    def sql_explain(self, sql: str, analyze: bool) -> str: ...

    @staticmethod
    def quote_literal(value: str) -> str:
        """Standard SQL string literal."""
        return "'" + value.replace("'", "''") + "'"
