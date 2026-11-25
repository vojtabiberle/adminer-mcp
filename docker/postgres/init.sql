-- Seed for the test environment. Runs once in database "app" as user "postgres".
-- It contains the edge cases the HTML parser must handle (see docs/development.md).

CREATE ROLE reader LOGIN PASSWORD 'reader-test-pw';
CREATE ROLE writer LOGIN PASSWORD 'writer-test-pw';

CREATE SCHEMA audit;

CREATE TABLE customers (
    id serial PRIMARY KEY,
    name text NOT NULL,
    email text
);
COMMENT ON TABLE customers IS 'Customers of the shop';

CREATE TABLE orders (
    id bigserial PRIMARY KEY,
    customer_id integer REFERENCES customers (id),
    status text NOT NULL DEFAULT 'new',
    note text,
    amount numeric(30, 10),
    created_at timestamptz,
    data jsonb,
    tags text[],
    payload bytea,
    code char(3)
);
COMMENT ON COLUMN orders.note IS 'Free text';
CREATE INDEX orders_status_idx ON orders (status);

CREATE TABLE big (
    id integer PRIMARY KEY,
    label text
);

CREATE TABLE audit.log (
    id serial PRIMARY KEY,
    message text
);

CREATE VIEW open_orders AS SELECT * FROM orders WHERE status <> 'closed';

INSERT INTO customers (name, email) VALUES
    ('Alice', 'alice@example.com'),
    ('Bob', NULL),
    ('Příliš žluťoučký kůň 🐎', 'https://example.com/profile');

INSERT INTO orders (customer_id, status, note, amount, created_at, data, tags, payload, code) VALUES
    (1, 'new', NULL, 12345678901234567890.0123456789, '2026-11-25 10:00:00+01', '{"a": 1, "b": [1, 2]}', '{x,y}', '\xdeadbeef', 'ABC'),
    (1, 'paid', '', 0.5, '2026-11-25 11:30:00+00', 'null', '{}', 'abc', NULL),
    (2, 'closed', E'line 1\nline 2\n\ttabbed', -1, NULL, NULL, NULL, NULL, 'X'),
    (3, 'new', '<i>NULL</i> & <b>bold</b> "quoted"', NULL, NULL, '"text"', NULL, NULL, NULL);

INSERT INTO big (id, label) SELECT g, 'row ' || g FROM generate_series(1, 1500) AS g;

INSERT INTO audit.log (message) VALUES ('created');

-- A function that writes: the read-only session guard must stop it (ADR-0005).
CREATE FUNCTION archive_orders() RETURNS integer LANGUAGE sql AS $$
    INSERT INTO audit.log (message) VALUES ('archived') RETURNING id;
$$;

GRANT USAGE ON SCHEMA public, audit TO reader, writer;
GRANT SELECT ON ALL TABLES IN SCHEMA public, audit TO reader;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public, audit TO writer;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public, audit TO writer;
GRANT EXECUTE ON FUNCTION archive_orders() TO reader, writer;

CREATE DATABASE other;
