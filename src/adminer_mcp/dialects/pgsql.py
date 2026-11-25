"""PostgreSQL (Adminer driver `pgsql`)."""

from __future__ import annotations

from typing import ClassVar

from adminer_mcp.dialects.base import Dialect

_SYSTEM_SCHEMAS = "n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'"

_RELKIND = (
    "CASE c.relkind WHEN 'r' THEN 'table' WHEN 'v' THEN 'view'"
    " WHEN 'm' THEN 'materialized_view' WHEN 'f' THEN 'foreign_table'"
    " WHEN 'p' THEN 'partitioned_table' END"
)


class PostgresDialect(Dialect):
    adminer_driver = "pgsql"
    sqlglot_dialect = "postgres"
    supports_schemas = True
    transactional_ddl = True
    supports_dry_run = True

    side_effect_functions: ClassVar[tuple[str, ...]] = (
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_advisory_*lock*",
        "pg_try_advisory_*lock*",
        "nextval",
        "setval",
        "set_config",
        "lo_*",
        "dblink*",
        "pg_read_*file",
        "pg_ls_dir",
        "pg_file_write",
        "pg_switch_wal",
        "pg_create_*_replication_slot",
        "pg_drop_replication_slot",
        "pg_sleep*",
    )

    non_rollbackable_functions: ClassVar[tuple[str, ...]] = (
        "nextval",
        "setval",
        "dblink*",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_advisory_lock*",
        "pg_try_advisory_lock*",
        "pg_file_write",
    )

    guarded_settings = frozenset(
        {"default_transaction_read_only", "transaction_read_only", "session_authorization", "role"}
    )

    def read_only_prelude(self) -> str:
        return "SET default_transaction_read_only = on;"

    def sql_list_databases(self) -> str:
        return "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname"

    def sql_list_schemas(self) -> str:
        return f"SELECT n.nspname FROM pg_namespace n WHERE {_SYSTEM_SCHEMAS} ORDER BY n.nspname"

    def sql_list_tables(self, schema: str | None) -> str:
        schema_filter = f" AND n.nspname = {self.quote_literal(schema)}" if schema else ""
        return (
            f"SELECT n.nspname AS schema, c.relname AS name, {_RELKIND} AS type,"
            " CASE WHEN c.relkind IN ('r', 'm', 'p') AND c.reltuples >= 0"
            " THEN c.reltuples::bigint END AS estimated_rows,"
            " obj_description(c.oid, 'pg_class') AS comment"
            " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            f" WHERE c.relkind IN ('r', 'v', 'm', 'f', 'p') AND {_SYSTEM_SCHEMAS}{schema_filter}"
            " ORDER BY n.nspname, c.relname"
        )

    def _regclass(self, table: str, schema: str | None) -> str:
        name = f"quote_ident({self.quote_literal(table)})"
        if schema:
            name = f"quote_ident({self.quote_literal(schema)}) || '.' || {name}"
        return f"to_regclass({name})"

    def sql_describe_table(self, table: str, schema: str | None) -> list[str]:
        oid = self._regclass(table, schema)
        return [
            f"SELECT n.nspname AS schema, c.relname AS name, {_RELKIND} AS type,"
            " obj_description(c.oid, 'pg_class') AS comment"
            f" FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = {oid}",
            "SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,"
            " NOT a.attnotnull AS nullable, pg_get_expr(d.adbin, d.adrelid) AS default,"
            " col_description(a.attrelid, a.attnum) AS comment"
            " FROM pg_attribute a LEFT JOIN pg_attrdef d"
            " ON d.adrelid = a.attrelid AND d.adnum = a.attnum"
            f" WHERE a.attrelid = {oid} AND a.attnum > 0 AND NOT a.attisdropped"
            " ORDER BY a.attnum",
            "SELECT i.relname AS name, ix.indisprimary AS primary, ix.indisunique AS unique,"
            " array_to_json(ARRAY(SELECT a.attname FROM unnest(ix.indkey)"
            " WITH ORDINALITY AS k(attnum, ord) JOIN pg_attribute a"
            " ON a.attrelid = ix.indrelid AND a.attnum = k.attnum ORDER BY k.ord)) AS columns,"
            " pg_get_indexdef(ix.indexrelid) AS definition"
            " FROM pg_index ix JOIN pg_class i ON i.oid = ix.indexrelid"
            f" WHERE ix.indrelid = {oid} ORDER BY i.relname",
            "SELECT con.conname AS name,"
            " array_to_json(ARRAY(SELECT a.attname FROM unnest(con.conkey)"
            " WITH ORDINALITY AS k(attnum, ord) JOIN pg_attribute a"
            " ON a.attrelid = con.conrelid AND a.attnum = k.attnum ORDER BY k.ord)) AS columns,"
            " fn.nspname AS ref_schema, fc.relname AS ref_table,"
            " array_to_json(ARRAY(SELECT a.attname FROM unnest(con.confkey)"
            " WITH ORDINALITY AS k(attnum, ord) JOIN pg_attribute a"
            " ON a.attrelid = con.confrelid AND a.attnum = k.attnum ORDER BY k.ord))"
            " AS ref_columns"
            " FROM pg_constraint con JOIN pg_class fc ON fc.oid = con.confrelid"
            " JOIN pg_namespace fn ON fn.oid = fc.relnamespace"
            f" WHERE con.contype = 'f' AND con.conrelid = {oid} ORDER BY con.conname",
        ]

    def sql_explain(self, sql: str, analyze: bool) -> str:
        return f"EXPLAIN (ANALYZE, BUFFERS) {sql}" if analyze else f"EXPLAIN {sql}"
