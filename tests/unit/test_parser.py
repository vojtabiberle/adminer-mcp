from __future__ import annotations

from pathlib import Path

import pytest

from adminer_mcp import parser
from adminer_mcp.errors import ProtocolError
from adminer_mcp.models import Affected, Binary, Column, Empty, ResultSet, StatementError

FIXTURES = Path(__file__).parent.parent / "fixtures" / "adminer-6.3.1"


def load(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def result_set(page_name: str, index: int = 0) -> ResultSet:
    outcome = parser.parse_sql_page(load(page_name)).statements[index].outcome
    assert isinstance(outcome, ResultSet)
    return outcome


class TestPageState:
    def test_login_page(self) -> None:
        html = load("login_page")
        assert parser.is_login_page(html)
        assert parser.extract_token(html)
        assert parser.page_errors(html) == []

    def test_login_failed_reports_database_error(self) -> None:
        html = load("login_failed")
        assert parser.is_login_page(html)
        [error] = parser.page_errors(html)
        assert "password authentication failed" in error
        assert not parser.is_locked_out([error])

    def test_logged_in(self) -> None:
        html = load("logged_in")
        assert not parser.is_login_page(html)
        assert parser.extract_version(html) == "6.3.1"
        token = parser.extract_token(html)
        assert token is not None and ":" in token

    def test_session_expired_is_login_page(self) -> None:
        assert parser.is_login_page(load("session_expired"))

    def test_logout_returns_login_page(self) -> None:
        assert parser.is_login_page(load("logout"))

    def test_csrf_error(self) -> None:
        html = load("csrf_invalid")
        assert not parser.is_login_page(html)
        [error] = parser.page_errors(html)
        assert error.startswith("Invalid CSRF token")

    def test_bad_schema(self) -> None:
        assert parser.page_errors(load("sql_bad_schema")) == ["Not found."]
        assert parser.parse_sql_page(load("sql_bad_schema")).statements == []

    def test_locked_out_minutes(self) -> None:
        errors = ["Too many unsuccessful logins, try again in 27 minute(s)."]
        assert parser.is_locked_out(errors)
        assert parser.locked_out_minutes(errors) == 27

    def test_not_an_adminer_page(self) -> None:
        with pytest.raises(ProtocolError, match="not an Adminer page"):
            parser.parse_sql_page("<html><body>Proxy error</body></html>")


class TestSqlPage:
    def test_mixed_statements(self) -> None:
        page = parser.parse_sql_page(load("sql_mixed"))
        assert [s.index for s in page.statements] == [1, 2, 3, 4]
        first, second, third, fourth = (s.outcome for s in page.statements)
        assert isinstance(first, ResultSet)
        assert first.total_rows == 4
        assert len(first.rows) == 2  # limit=2
        assert isinstance(second, Empty)
        assert isinstance(third, Affected) and third.affected_rows == 0
        assert isinstance(fourth, StatementError)
        assert fourth.message.startswith("ERROR:  permission denied for table log")
        assert "\nCONTEXT:" in fourth.message
        # error_stops: statement 5 did not run; Adminer rolled back the open transaction
        assert page.adminer_rolled_back

    def test_columns_from_th_title(self) -> None:
        columns = result_set("sql_values").columns
        assert columns[0] == Column(name="id", type="int8", table="orders")
        assert columns[5] == Column(name="name", type="text", table="customers")
        assert columns[7] == Column(name="nothing", type="text", table=None)
        assert columns[9] == Column(name="expr", type="int4", table=None)

    def test_cell_values(self) -> None:
        rows = result_set("sql_values").rows
        # id is a link to the edit page; the text is the value
        assert [row[0] for row in rows] == ["1", "2", "3", "4"]
        assert rows[0][1] is None  # NULL
        assert rows[1][1] == ""  # empty string
        assert rows[2][1] == "line 1\nline 2\n\ttabbed"
        assert rows[3][1] == '<i>NULL</i> & <b>bold</b> "quoted"'  # escaped text, not NULL
        assert rows[0][3] == '{"a": 1, "b": [1, 2]}'
        assert rows[0][4] == Binary(size=4)  # \xdeadbeef
        assert rows[1][4] == "abc"  # bytea that is valid UTF-8
        assert rows[3][5] == "Příliš žluťoučký kůň 🐎"
        assert rows[0][6] == "alice@example.com"  # mailto link
        assert rows[3][6] == "https://example.com/profile"  # URL link
        assert rows[0][8] == ""

    def test_numeric_and_char_values(self) -> None:
        rows = result_set("sql_mixed").rows
        assert rows[0][2] == "12345678901234567890.0123456789"
        assert rows[0][5] == "ABC"  # char(3) is wrapped in <code>
        assert rows[0][6] == "{x,y}"

    def test_explain_table_is_ignored(self) -> None:
        page = parser.parse_sql_page(load("sql_comment_only"))
        [statement] = page.statements
        assert isinstance(statement.outcome, ResultSet)
        assert statement.outcome.columns == [Column(name="one", type="int4", table=None)]
        assert not page.adminer_rolled_back  # the comment <pre> is not the rollback marker

    def test_limit_footer(self) -> None:
        outcome = result_set("sql_big")
        assert len(outcome.rows) == 5
        assert outcome.total_rows == 1500

    def test_affected_and_returning(self) -> None:
        page = parser.parse_sql_page(load("sql_affected"))
        outcomes = [s.outcome for s in page.statements]
        assert isinstance(outcomes[1], ResultSet)
        assert outcomes[1].rows == [["2", "a"], ["3", "b"]]
        assert isinstance(outcomes[2], Affected) and outcomes[2].affected_rows == 2
        assert isinstance(outcomes[3], Affected) and outcomes[3].affected_rows == 0
        assert not page.adminer_rolled_back  # explicit ROLLBACK, nothing left open

    def test_thousands_separator_in_affected_rows(self) -> None:
        html = load("sql_affected").replace(
            "Query executed OK, 2 rows affected.", "Query executed OK, 12,345 rows affected."
        )
        outcome = parser.parse_sql_page(html).statements[2].outcome
        assert isinstance(outcome, Affected) and outcome.affected_rows == 12345

    def test_adminer_rollback_marker(self) -> None:
        page = parser.parse_sql_page(load("sql_adminer_rollback"))
        assert page.adminer_rolled_back
        assert isinstance(page.statements[1].outcome, Affected)
        assert page.statements[1].outcome.affected_rows == 1

    def test_read_only_violation(self) -> None:
        page = parser.parse_sql_page(load("sql_read_only_violation"))
        outcome = page.statements[1].outcome
        assert isinstance(outcome, StatementError)
        assert "read-only transaction" in outcome.message

    def test_syntax_error_without_errno(self) -> None:
        [statement] = parser.parse_sql_page(load("sql_syntax_error")).statements
        assert isinstance(statement.outcome, StatementError)
        assert statement.outcome.message.startswith('ERROR:  syntax error at or near "SELEC"')

    def test_notice_becomes_warning(self) -> None:
        statements = parser.parse_sql_page(load("sql_notice")).statements
        assert statements[0].warnings == "NOTICE:  hello from notice"
        assert statements[1].warnings is None

    def test_schema_and_no_database(self) -> None:
        assert result_set("sql_schema").rows == [["created"]]
        assert result_set("sql_no_db").rows == [["postgres"]]

    def test_statement_without_result_is_protocol_error(self) -> None:
        html = load("sql_syntax_error").replace("<p class='error'>", "<p class='other'>")
        with pytest.raises(ProtocolError, match="has no result"):
            parser.parse_sql_page(html)

    def test_unknown_message_is_protocol_error(self) -> None:
        html = load("sql_affected").replace("Query executed OK", "Příkaz proběhl v pořádku")
        with pytest.raises(ProtocolError, match="Unknown message"):
            parser.parse_sql_page(html)
