"""Record Adminer HTML responses from the Docker test environment as parser fixtures.

Usage:
    docker compose -f docker/compose.yml up -d --build --wait
    uv run python tests/fixtures/record.py

The script talks to Adminer with plain httpx on purpose, so the fixtures do not depend
on the client code that the tests check. Never record fixtures from a real server:
they would contain real data.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

ADMINER_URL = "http://127.0.0.1:8088/adminer.php"
SERVER = "postgres"
VERSION = "6.3.1"
OUT_DIR = Path(__file__).parent / f"adminer-{VERSION}"

USERS = {
    "reader": "reader-test-pw",
    "writer": "writer-test-pw",
    "postgres": "postgres-test-pw",
}

TOKEN_RE = re.compile(r"name=['\"]token['\"] value=['\"]([^'\"]+)")


def token_of(html: str) -> str:
    match = TOKEN_RE.search(html)
    if match is None:
        raise RuntimeError("no token in the page")
    return match.group(1)


def new_client() -> httpx.Client:
    return httpx.Client(cookies={"adminer_lang": "en"}, follow_redirects=False, timeout=30)


def login(client: httpx.Client, user: str, password: str, db: str = "app") -> httpx.Response:
    page = client.get(ADMINER_URL)
    response = client.post(
        ADMINER_URL,
        data={
            "auth[driver]": "pgsql",
            "auth[server]": SERVER,
            "auth[username]": user,
            "auth[password]": password,
            "auth[db]": db,
            "token": token_of(page.text),
        },
    )
    location = response.headers["location"]
    return client.get(httpx.URL(ADMINER_URL).join(location), follow_redirects=True)


def sql_url(user: str, db: str | None = "app", ns: str | None = "") -> str:
    url = f"{ADMINER_URL}?pgsql={SERVER}&username={user}"
    if db is not None:
        url += f"&db={db}"
        if ns is not None:
            url += f"&ns={ns}"
    return url + "&sql="


def run_sql(
    user: str, query: str, limit: int = 20, db: str | None = "app", ns: str | None = ""
) -> str:
    with new_client() as client:
        page = login(client, user, USERS[user], db or "")
        response = client.post(
            sql_url(user, db, ns),
            data={
                "query": query,
                "limit": str(limit),
                "error_stops": "1",
                "token": token_of(page.text),
            },
        )
        return response.text


def save(name: str, html: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{name}.html").write_text(html, encoding="utf-8")
    print(f"recorded {name}.html ({len(html)} bytes)")


def main() -> int:
    with new_client() as client:
        save("login_page", client.get(ADMINER_URL).text)

    with new_client() as client:
        save("login_failed", login(client, "reader", "wrong-password").text)

    with new_client() as client:
        save("logged_in", login(client, "reader", USERS["reader"]).text)

    save(
        "sql_mixed",
        run_sql(
            "reader",
            "SELECT id, note, amount, data, payload, code, tags FROM orders ORDER BY id;"
            " SELECT 1 AS x WHERE false; BEGIN; SELECT archive_orders(); SELECT * FROM nope;",
            limit=2,
        ),
    )
    save(
        "sql_values",
        run_sql(
            "reader",
            "SELECT o.id, o.note, o.created_at, o.data, o.payload, c.name, c.email,"
            " NULL AS nothing, '' AS empty, 1 + 1 AS expr"
            " FROM orders o LEFT JOIN customers c ON c.id = o.customer_id ORDER BY o.id",
        ),
    )
    save("sql_big", run_sql("reader", "SELECT * FROM big ORDER BY id", limit=5))
    save(
        "sql_affected",
        run_sql(
            "writer",
            "BEGIN; INSERT INTO audit.log (message) VALUES ('a'), ('b') RETURNING id, message;"
            " UPDATE orders SET status = status WHERE id IN (1, 2);"
            " DELETE FROM audit.log WHERE false; ROLLBACK;",
        ),
    )
    save(
        "sql_adminer_rollback",
        run_sql("writer", "BEGIN; UPDATE orders SET status = 'x' WHERE id = 1;"),
    )
    save(
        "sql_read_only_violation",
        run_sql("writer", "SET default_transaction_read_only = on; SELECT archive_orders();"),
    )
    save("sql_syntax_error", run_sql("reader", "SELEC 1"))
    save("sql_comment_only", run_sql("reader", "SELECT 1 AS one;\n-- just a comment\n"))
    save(
        "sql_notice",
        run_sql(
            "postgres", "DO $$ BEGIN RAISE NOTICE 'hello from notice'; END $$; SELECT 2 AS two"
        ),
    )
    save("sql_schema", run_sql("reader", "SELECT message FROM log", ns="audit"))
    save("sql_bad_schema", run_sql("reader", "SELECT 1", ns="no_such_schema"))
    save("sql_no_db", run_sql("reader", "SELECT current_database()", db=None))

    with new_client() as client:
        login(client, "reader", USERS["reader"])
        response = client.post(
            sql_url("reader"), data={"query": "SELECT 1", "limit": "1", "token": "bad:token"}
        )
        save("csrf_invalid", response.text)

    with new_client() as client:
        client.get(ADMINER_URL)  # a session cookie without a login
        response = client.post(
            sql_url("reader"), data={"query": "SELECT 1", "limit": "1", "token": "x:y"}
        )
        save("session_expired", response.text)

    with new_client() as client:
        page = login(client, "reader", USERS["reader"])
        response = client.post(
            f"{ADMINER_URL}?pgsql={SERVER}&username=reader",
            data={"logout": "1", "token": token_of(page.text)},
        )
        save("logout", client.get(httpx.URL(ADMINER_URL).join(response.headers["location"])).text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
