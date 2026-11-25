# Adminer 6.3.1 HTTP interface

How the MCP server talks to Adminer. Everything here comes from the source code of tag `v6.3.1` in the [vrana/adminer](https://github.com/vrana/adminer) repository. File references are relative to its root.
Adminer has no API, so this is a non-public interface and it can change in another version. Every markup dependency listed here must have a test against a saved response (see [development.md](development.md)).

## Base URL

Adminer is either a single PHP file (`https://host/adminer.php`) or a directory (`https://host/adminer/`). The profile field `url` is always the full address without a query string.

## URL parameters

```
{url}?{driver}={server}&username={user}[&db={database}[&ns={schema}]]&{page}
```

- **Driver key**: the parameter name is the driver id, the value is the server (`host[:port]` or `:socket`):
  - `server` – MySQL and MariaDB (default driver; with an empty server the parameter is omitted),
  - `pgsql`, `sqlite`, `mssql`, `oracle` – built-in drivers (`adminer/drivers/*.inc.php`),
  - `mongo`, `elastic`, `clickhouse`, `firebird`, `simpledb`, `imap`, `redis`, `igdb` – plugin drivers (`plugins/drivers/`). They work only if the Adminer installation on the server has them.
- **`db`** is optional. Without it, PostgreSQL connects to the `postgres` database (`adminer/drivers/pgsql.inc.php`) and MySQL connects with no database selected. An empty `db=` means the database overview.
- **`ns`** (schema) applies only together with `db` (constant `ME` in `adminer/include/bootstrap.inc.php`).
- **Page**: the SQL command page is `sql=` (empty value). The MVP does not use other pages.

The password is stored in the session under the driver/server/user triple (`set_password`). Changing `db` or `ns` in the URL does not need a new login.

## Cookies and session

| Cookie | Meaning | MCP server |
|---|---|---|
| `adminer_sid` | PHP session (name set in `bootstrap.inc.php`) | Kept in the client cookie jar, separate per profile |
| `adminer_key` | Key that encrypts the permanent login | Kept as the server sets it |
| `adminer_permanent` | Permanent login (encrypted password in a cookie) | Not used; `auth[permanent]` is not sent |
| `adminer_lang` | UI language | Sends `en`, so that message texts are in English and can be parsed (`adminer/include/lang.inc.php`) |

## CSRF token

- The session holds a random string `$_SESSION["token"]`. Each form carries `<input type='hidden' name='token' value='{hex}:{rand}'>` (`get_token`, `adminer/include/functions.inc.php`).
- Each rendered token is masked differently, but all of them are valid for the whole session. The MCP server reads the token from any page after login and reuses it.
- `verify_token` requires that the `Sec-Fetch-Site` header is absent or has the value `same-origin`. The HTTP client must not send any other value.
- A POST without a valid token returns 403 and the message "Invalid CSRF token".

## Login

See `adminer/include/auth.inc.php`.

1. `GET {url}` returns the login form with a token and sets `adminer_sid`.
2. `POST {url}` with the fields:

   | Field | Value |
   |---|---|
   | `auth[driver]` | driver id (`pgsql`, `server`, …) |
   | `auth[server]` | server |
   | `auth[username]` | user |
   | `auth[password]` | password |
   | `auth[db]` | database, can be empty |
   | `token` | token from step 1 |

3. Adminer only stores the password in the session and **always** redirects to `?{driver}={server}&username={user}[&db=…]`. The check happens when the target page loads, so the client must follow the redirect and evaluate the target page:
   - success: a normal page with navigation (`span.version`) and a token,
   - failure: HTTP 403 and the login form again, with the error in `div.error`. The error is "Invalid credentials.", the database connection error text, or "Adminer does not support accessing a database without a password." Only then is the failed attempt recorded (`add_invalid_login`).

Adminer does not support an empty password (`Adminer::login`).

### Detecting the logged-out state

The response contains `input[name="auth[password]"]`. If the URL contains `username`, the status is also HTTP 403. After the session expires, the message is "Session expired. Please log in again."

### Brute-force protection

- Each failed login is recorded under the key `bruteForceKey()`, which by default is `REMOTE_ADDR`, i.e. the client IP.
- After **30 failures** within a 30-minute window, Adminer rejects further attempts without checking the credentials at all: "Too many unsuccessful logins, try again in N minute(s)."
- Behind a reverse proxy, attempts are counted under the proxy IP, so the block affects all users behind that proxy.

Consequence for the MCP server: it never retries automatically after a failed login (see [architecture.md](architecture.md#login-and-session)).

## SQL command (`sql=`)

See `adminer/sql.inc.php`.

### Request

`POST {url}?{driver}={server}&username={user}[&db=…[&ns=…]]&sql=`

| Field | Value | Note |
|---|---|---|
| `query` | SQL, can contain multiple statements | |
| `limit` | max. number of printed rows per statement | An empty value means no limit. The MCP server always sends it |
| `error_stops` | `1` | Stops at the first error |
| `only_errors` | not sent | With it, results are not printed |
| `token` | CSRF token | |

### Processing

- Adminer splits the SQL into statements by the delimiter itself. It respects quotes, comments, `$$` in PostgreSQL, `DELIMITER` and `COPY … FROM stdin`, and in PostgreSQL also the `\connect db` meta-command.
- Each statement runs separately (`multi_query`) in autocommit mode; results are printed in order.
- **At the end of every request** Adminer calls `driver()->rollback()`. An unfinished transaction (`BEGIN` without `COMMIT`) is rolled back and `ROLLBACK -- Adminer` is printed. An open transaction cannot stay open through Adminer.
- For `SELECT`, Adminer automatically runs `EXPLAIN` on a second connection (the "Explain" link in the footer). This cannot be disabled without `only_errors`.
- Queries are stored in a history in the server-side session, including literals.

### Response – structure per statement

```html
<pre id='sql-1'><code class='jush-pgsql'>SELECT …</code></pre>

<!-- a) result with rows -->
<form method='post'>
<div class='scrollable'>
<table class='nowrap odds'>
<thead><tr><th title='orders.id int4'>id<th title='orders.note text'>note
<tbody>
<tr><td class='number'>1<td><i>NULL</i>
</table>
</div>
<p class='sql-footer'>20 / 1234 rows <span class='time'>(0.002 s)</span>, …
</form>

<!-- b) result without rows -->
<p class='message'>No rows.

<!-- c) statement without a result set -->
<p class='message' title='{info}'>Query executed OK, 3 rows affected. <span class='time'>(0.001 s)</span>

<!-- d) error; "(errno)" only if the driver returns it -->
<p class='error'>Error in query (errno): ERROR:  relation "x" does not exist

<!-- optional -->
<div id='warnings-1' class='hidden'>…</div>
<div id='explain-1' class='hidden explain'>…</div>
```

More notes:

- The HTML omits end tags (`<td>`, `<tr>`, `<p>`), so the parser must be HTML5 compliant.
- The `title` attribute on `<th>` has the form `{orgtable}.{orgname} {type}`, or just `{type}`. For expressions without a table, the table can be missing.
- **A result without rows does not return column names.** There is only "No rows."
- The `p.sql-footer` footer contains `{limit} / {total} rows` if the result exceeded the limit, otherwise `{n} rows`.
- With multiple statements and an error, there is a summary `p.error` at the end with links to the statements.
- Outside the results there can be a `div.error` (page error, e.g. invalid CSRF token) and a `div.message`.

### Cell value rendering

See `select_value` in `functions.inc.php` and `Adminer::selectVal` in `adminer.inc.php`.

| Value | HTML |
|---|---|
| NULL | `<i>NULL</i>` |
| empty string | empty cell |
| `char`, `binary`, `boolean` (not `var*`) | `<code>…</code>` |
| `json`, `jsonb` | `<code class='jush-json'>…</code>` |
| e-mail or URL | wrapped in `<a href>` |
| binary data that is not UTF-8 | `<i>N bytes</i>` (the value is not available) |
| nested arrays (document drivers) | nested `<table>` |
| numeric types | `td.number` |

Values are HTML-escaped and are **not shortened** on the SQL command page: `select_value` gets `text_length = null`, unlike the table browse page. A literal `<i>NULL</i>` stored as text is escaped, so it can be told apart from a real NULL: look for an `i` element, not for text.

### Row limit

`limit` limits only the number of printed rows. PHP still loads the whole result (`store_result`) and reports the total count in the footer. A large `SELECT` without `LIMIT` therefore loads both the Adminer server and the database, even if the MCP server returns only a few rows.

## Query result export

The "Export" button below a result on the SQL command page.

`POST …&sql=` with the fields `export=1`, `query` (one statement), `output=text`, `format` (`csv`, `csv;`, `tsv`, `sql`) and `token`. The response is plain text: UTF-8 BOM, header and rows (`dump_csv`).

Limitations that prevent its use for normal queries (see [ADR-0003](adr/0003-results-from-sql-command-html.md)):

- NULL and the empty string are both written as `""` and cannot be told apart.
- An empty result returns only the BOM, without a header.
- A query error returns an empty body and the error message is lost.
- No limit, always the whole result.
- `format=sql` on the SQL command page only returns the query text, not the data.

It is useful only for bulk export to a file (PRD-0001 R15).

## Logout

`POST {url}?{driver}={server}&username={user}` with the fields `logout=1` and `token`. Removes passwords and query history from the session. The MCP server sends it on shutdown (ADR-0011).

## Version

The version is in the navigation as `<span class='version'>6.3.1</span>` inside `h1` (`Adminer::navigation`). The MCP server reads it after login and compares it with the list of tested versions.

## Parser markup dependencies (checklist for version changes)

- `input[name="auth[password]"]`, `input[name=token]`, `div.error`
- `pre[id^=sql-]` as the start of a statement result
- `table.nowrap` → `thead th[title]`, `tbody tr td`, `td > i` for NULL and binary data
- `p.message`, `p.error`, `p.sql-footer`
- English texts: `Query executed OK, %d row(s) affected.`, `No rows.`, `Error in query`, `%d / %d rows`, `Too many unsuccessful logins`, `Session expired`
- `span.version`
