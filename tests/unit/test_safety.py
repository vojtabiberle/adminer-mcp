from __future__ import annotations

import re
from typing import Any

import pytest

from adminer_mcp.dialects import get_dialect
from adminer_mcp.errors import PolicyError
from adminer_mcp.safety import Category, ExecuteMode, check_execute, check_query, classify

PG = get_dialect("pgsql")


def categories(sql: str) -> list[Category]:
    classification = classify(sql, PG)
    assert classification.parse_error is None
    return [statement.category for statement in classification.statements]


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1", Category.READ),
        ("select * from orders where id = 1", Category.READ),
        ("WITH x AS (SELECT 1) SELECT * FROM x", Category.READ),
        ("SELECT 1 UNION SELECT 2", Category.READ),
        ("VALUES (1), (2)", Category.READ),
        ("TABLE orders", Category.READ),
        ("SHOW search_path", Category.READ),
        ("EXPLAIN SELECT 1", Category.READ),
        ("EXPLAIN DELETE FROM orders", Category.READ),  # without ANALYZE it does not run
        ("EXPLAIN ANALYZE SELECT 1", Category.READ),
        ("EXPLAIN ANALYZE DELETE FROM orders WHERE id = 1", Category.DML),
        ("EXPLAIN (ANALYZE, BUFFERS) UPDATE t SET a = 1 WHERE id = 1", Category.DML),
        ("EXPLAIN (COSTS false) DELETE FROM t", Category.READ),
        ("SET search_path = public", Category.SESSION),
        ("SET LOCAL statement_timeout = '5s'", Category.SESSION),
        ("RESET search_path", Category.SESSION),
        ("SET default_transaction_read_only = off", Category.SESSION_PRIVILEGED),
        ("SET SESSION transaction_read_only TO off", Category.SESSION_PRIVILEGED),
        ("SET ROLE admin", Category.SESSION_PRIVILEGED),
        ("SET SESSION AUTHORIZATION bob", Category.SESSION_PRIVILEGED),
        ("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE", Category.SESSION_PRIVILEGED),
        ("SET TRANSACTION READ WRITE", Category.SESSION_PRIVILEGED),
        ("RESET ALL", Category.SESSION_PRIVILEGED),
        ("SELECT * FROM orders FOR UPDATE", Category.LOCK),
        ("LOCK TABLE orders", Category.LOCK),
        ("BEGIN", Category.TRANSACTION),
        ("START TRANSACTION", Category.TRANSACTION),
        ("COMMIT", Category.TRANSACTION),
        ("END", Category.TRANSACTION),
        ("ROLLBACK", Category.TRANSACTION),
        ("SAVEPOINT a", Category.TRANSACTION),
        ("RELEASE SAVEPOINT a", Category.TRANSACTION),
        ("INSERT INTO t VALUES (1)", Category.DML),
        ("UPDATE t SET a = 1 WHERE id = 1", Category.DML),
        ("DELETE FROM t WHERE id = 1", Category.DML),
        ("MERGE INTO t USING u ON t.id = u.id WHEN MATCHED THEN DELETE", Category.DML),
        ("WITH d AS (DELETE FROM t WHERE id = 1 RETURNING *) SELECT * FROM d", Category.DML),
        ("SELECT * INTO new_t FROM t", Category.DML),
        ("COPY t FROM stdin", Category.DML),
        ("COPY t TO '/tmp/x'", Category.UNKNOWN),
        ("CREATE TABLE x (a int)", Category.DDL),
        ("ALTER TABLE t ADD COLUMN b int", Category.DDL),
        ("DROP TABLE t", Category.DDL),
        ("TRUNCATE t", Category.DDL),
        ("COMMENT ON TABLE t IS 'x'", Category.DDL),
        ("GRANT SELECT ON t TO u", Category.DDL),
        ("VACUUM t", Category.DDL),
        ("ANALYZE t", Category.DDL),
        ("CLUSTER t", Category.DDL),
        ("REFRESH MATERIALIZED VIEW mv", Category.DDL),
        ("CALL p()", Category.DDL),
        ("DO $$ BEGIN END $$", Category.DDL),
        ("PREPARE p AS SELECT 1", Category.UNKNOWN),
        ("EXECUTE p", Category.UNKNOWN),
        ("NOTIFY c", Category.UNKNOWN),
        ("\\connect other", Category.UNKNOWN),
        ("SELEC 1", Category.UNKNOWN),
        ("SELECT FROM WHERE", Category.UNKNOWN),
    ],
)
def test_category(sql: str, expected: Category) -> None:
    assert categories(sql) == [expected]


def test_split_respects_quotes_comments_and_dollar_quoting() -> None:
    sql = "SELECT 'a;b'; -- c; d\nSELECT $$x;y$$;; /* e; */ SELECT 3"
    classification = classify(sql, PG)
    assert [s.sql for s in classification.statements] == [
        "SELECT 'a;b'",
        "SELECT $$x;y$$",
        "SELECT 3",
    ]
    assert [s.index for s in classification.statements] == [1, 2, 3]


def test_command_statement_does_not_swallow_the_next_one() -> None:
    # sqlglot tokenizes the rest of EXPLAIN/RESET/… as one string; ';' must still split.
    assert categories("EXPLAIN SELECT ';'; DROP TABLE t") == [Category.READ, Category.DDL]
    assert categories("RESET search_path; RESET ALL") == [
        Category.SESSION,
        Category.SESSION_PRIVILEGED,
    ]


@pytest.mark.parametrize(
    ("sql", "tables"),
    [
        ("UPDATE orders SET a = 1", ["orders"]),
        ("DELETE FROM orders", ["orders"]),
        ("DELETE FROM orders WHERE 1=1", ["orders"]),
        ("DELETE FROM orders WHERE TRUE", ["orders"]),
        ("DELETE FROM orders WHERE 1", ["orders"]),
        ("UPDATE orders SET a = 1 WHERE 'a' = 'a'", ["orders"]),
        ("UPDATE orders SET a = 1 WHERE NOT FALSE", ["orders"]),
        ("UPDATE orders SET a = 1 WHERE 1=1 OR id = 5", ["orders"]),
        ("WITH d AS (DELETE FROM audit.log) SELECT 1", ["audit.log"]),
        ("TRUNCATE orders", ["orders"]),
        ("UPDATE orders SET a = 1 WHERE id = 5", []),
        ("UPDATE orders SET a = 1 WHERE 1=1 AND id = 5", []),
        ("DELETE FROM t USING u WHERE t.id = u.id", []),
    ],
)
def test_full_table_detection(sql: str, tables: list[str]) -> None:
    [statement] = classify(sql, PG).statements
    assert statement.full_table == tables


class TestQueryPolicy:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1",
            "SELECT 1; SET search_path = audit; SELECT * FROM log",
            "EXPLAIN ANALYZE SELECT * FROM orders",
            "WITH x AS (SELECT 1) SELECT * FROM x",
        ],
    )
    def test_allowed(self, sql: str) -> None:
        check_query(classify(sql, PG), PG)

    @pytest.mark.parametrize(
        ("sql", "message"),
        [
            ("DELETE FROM t WHERE id = 1", "statement 1 [dml]"),
            ("SELECT 1; DROP TABLE t", "statement 2 [ddl]"),
            ("SET default_transaction_read_only = off; SELECT 1", "session_privileged"),
            ("BEGIN; SELECT 1", "transaction"),
            ("SELECT * FROM t FOR UPDATE", "lock"),
            ("SELECT nextval('s')", "side effects: nextval"),
            ("SELECT pg_catalog.pg_sleep(10)", "side effects: pg_sleep"),
            ("SELECT pg_advisory_lock(1)", "pg_advisory_lock"),
            ("SELECT set_config('default_transaction_read_only', 'off', false)", "set_config"),
            ("EXPLAIN ANALYZE DELETE FROM t WHERE id = 1", "EXPLAIN ANALYZE runs the statement"),
            ("SELEC 1", "unknown"),
            ("-- only a comment", "no statement"),
        ],
    )
    def test_rejected(self, sql: str, message: str) -> None:
        with pytest.raises(PolicyError, match=re.escape(message)):
            check_query(classify(sql, PG), PG)

    def test_error_points_to_execute(self) -> None:
        with pytest.raises(PolicyError, match="Use the execute tool"):
            check_query(classify("INSERT INTO t VALUES (1)", PG), PG)


def execute(sql: str, **overrides: Any) -> Any:
    options: dict[str, Any] = {
        "allow_write": True,
        "allow_ddl": False,
        "allow_full_table": False,
        "atomic": False,
        "dry_run": False,
        "profile_name": "staging",
    }
    options.update(overrides)
    return check_execute(classify(sql, PG), PG, **options)


class TestExecutePolicy:
    def test_dml_allowed(self) -> None:
        plan = execute("INSERT INTO t VALUES (1); UPDATE t SET a = 2 WHERE id = 1")
        assert plan.mode == ExecuteMode.NONE
        assert plan.warnings == []

    def test_read_only_profile(self) -> None:
        with pytest.raises(PolicyError, match="'prod' is read only"):
            execute("INSERT INTO t VALUES (1)", allow_write=False, profile_name="prod")

    @pytest.mark.parametrize(
        "sql",
        ["DROP TABLE t", "ALTER TABLE t ADD b int", "DO $$ BEGIN END $$", "PREPARE p AS SELECT 1"],
    )
    def test_ddl_needs_allow_ddl(self, sql: str) -> None:
        with pytest.raises(PolicyError, match="needs allow_ddl"):
            execute(sql)
        execute(sql, allow_ddl=True)

    def test_full_table_needs_confirmation(self) -> None:
        with pytest.raises(PolicyError, match="changes every row of orders"):
            execute("DELETE FROM orders")
        execute("DELETE FROM orders", allow_full_table=True)

    def test_truncate_needs_ddl_and_full_table(self) -> None:
        with pytest.raises(PolicyError, match="allow_full_table"):
            execute("TRUNCATE orders", allow_ddl=True)
        execute("TRUNCATE orders", allow_ddl=True, allow_full_table=True)

    def test_full_table_is_checked_in_dry_run(self) -> None:
        with pytest.raises(PolicyError, match="changes every row"):
            execute("UPDATE orders SET a = 1", dry_run=True)

    def test_connect_is_always_rejected(self) -> None:
        with pytest.raises(PolicyError, match="psql meta-command"):
            execute("\\connect other", allow_ddl=True)

    def test_explicit_transaction(self) -> None:
        plan = execute("BEGIN; UPDATE t SET a = 1 WHERE id = 1; COMMIT")
        assert plan.mode == ExecuteMode.EXPLICIT
        assert plan.warnings == []

    def test_begin_without_commit_warns(self) -> None:
        plan = execute("BEGIN; UPDATE t SET a = 1 WHERE id = 1")
        assert plan.mode == ExecuteMode.EXPLICIT
        [warning] = plan.warnings
        assert "without COMMIT" in warning

    @pytest.mark.parametrize("mode", ["atomic", "dry_run"])
    @pytest.mark.parametrize("sql", ["COMMIT", "BEGIN", "SAVEPOINT a", "ROLLBACK", "END"])
    def test_atomic_and_dry_run_reject_transaction_control(self, mode: str, sql: str) -> None:
        with pytest.raises(PolicyError, match="controls the transaction"):
            execute(f"UPDATE t SET a = 1 WHERE id = 1; {sql}", **{mode: True})

    def test_atomic_and_dry_run_together(self) -> None:
        with pytest.raises(PolicyError, match="not both"):
            execute("SELECT 1", atomic=True, dry_run=True)

    def test_modes(self) -> None:
        assert execute("UPDATE t SET a = 1 WHERE id = 1", atomic=True).mode == ExecuteMode.ATOMIC
        assert execute("UPDATE t SET a = 1 WHERE id = 1", dry_run=True).mode == ExecuteMode.DRY_RUN

    def test_dry_run_rejects_non_rollbackable_functions(self) -> None:
        with pytest.raises(PolicyError, match="rollback does not undo: nextval"):
            execute("INSERT INTO t (id) VALUES (nextval('s'))", dry_run=True)
        execute("INSERT INTO t (id) VALUES (nextval('s'))", atomic=True)

    def test_dry_run_allows_ddl_in_postgres(self) -> None:
        plan = execute("CREATE TABLE x (a int)", dry_run=True, allow_ddl=True)
        assert plan.mode == ExecuteMode.DRY_RUN
