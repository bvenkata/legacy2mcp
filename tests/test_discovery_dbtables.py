from __future__ import annotations

import sqlite3

import pytest

from legacy2mcp.discovery.contract import ContractError
from legacy2mcp.discovery.dbtables import discover_db_tables
from tests.sqlite_dialect import SqliteTestDialect


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, total REAL);
        CREATE VIEW big_orders AS SELECT * FROM orders WHERE total > 100;
        """
    )
    conn.close()
    return str(path)


class _ClosingDialect(SqliteTestDialect):
    """Records whether the connection was closed."""

    closed = False

    def connect(self, dsn, timeout_seconds):
        conn = super().connect(dsn, timeout_seconds)
        dialect = self

        class _Conn:
            def __getattr__(self, name):
                return getattr(conn, name)

            def close(self):
                dialect.closed = True
                conn.close()

        return _Conn()


def test_every_base_table_with_its_columns_and_no_views(db_path):
    finding = discover_db_tables("postgres", db_path, "LEGACY_DB_DSN", dialect=SqliteTestDialect())

    assert finding.dialect == "postgres"
    assert finding.dsn_env == "LEGACY_DB_DSN"
    assert [(t.table, t.columns) for t in finding.tables] == [
        ("customers", ["id", "name", "email"]),
        ("orders", ["id", "customer_id", "total"]),
    ]


def test_the_schema_is_carried_to_the_finding(db_path):
    finding = discover_db_tables("mssql", db_path, "DSN", db_schema="sales", dialect=SqliteTestDialect())

    assert finding.db_schema == "sales"


def test_named_tables_only_matched_without_case_and_kept_in_order(db_path):
    finding = discover_db_tables(
        "oracle", db_path, "DSN", tables=["ORDERS", "customers", "orders"], dialect=SqliteTestDialect()
    )

    assert [t.table for t in finding.tables] == ["orders", "customers"]


def test_a_named_table_that_does_not_exist_is_an_error(db_path):
    with pytest.raises(ContractError, match="No base table named 'invoices', 'big_orders'"):
        discover_db_tables("postgres", db_path, "DSN", tables=["invoices", "big_orders"], dialect=SqliteTestDialect())


def test_a_schema_with_more_tables_than_the_cap_needs_a_list(db_path):
    with pytest.raises(ContractError, match=r"has 2 base tables, more than the 1 .*customers, orders.*--db-tables"):
        discover_db_tables("postgres", db_path, "DSN", max_tables=1, dialect=SqliteTestDialect())

    # an explicit list is never capped
    finding = discover_db_tables("postgres", db_path, "DSN", tables=["orders"], max_tables=1, dialect=SqliteTestDialect())
    assert [t.table for t in finding.tables] == ["orders"]


def test_an_empty_schema_is_an_error(tmp_path):
    path = str(tmp_path / "empty.db")
    sqlite3.connect(path).close()

    with pytest.raises(ContractError, match="Found no base tables in the default schema"):
        discover_db_tables("postgres", path, "DSN", dialect=SqliteTestDialect())


def test_a_failed_connection_names_the_variable_never_the_dsn():
    class _Refusing(SqliteTestDialect):
        def connect(self, dsn, timeout_seconds):
            raise RuntimeError(f"could not connect to {dsn}")

    dsn = "postgresql://admin:hunter2@db.internal/legacy"
    with pytest.raises(ContractError) as excinfo:
        discover_db_tables("postgres", dsn, "LEGACY_DB_DSN", dialect=_Refusing())

    assert "$LEGACY_DB_DSN" in str(excinfo.value)
    assert "hunter2" not in str(excinfo.value)


def test_the_connection_is_closed(db_path):
    dialect = _ClosingDialect()
    discover_db_tables("postgres", db_path, "DSN", dialect=dialect)

    assert dialect.closed


def test_an_unknown_dialect_is_a_contract_error():
    with pytest.raises(ContractError, match="Unknown DB dialect: 'db2'"):
        discover_db_tables("db2", "x", "DSN")
