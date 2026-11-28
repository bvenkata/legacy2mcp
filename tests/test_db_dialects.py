"""
Unit tests for how each dialect translates our dialect-neutral `:name`
SQL placeholders into whatever its real driver accepts. These don't
need a live database or even the driver package installed -- they
exercise execute_named() against a tiny fake DB-API connection/cursor
that just records what it was called with.
"""

from __future__ import annotations

import pytest

from legacy2mcp.adapters.db_dialects import OracleDialect, PostgresDialect, SqlServerDialect


class FakeProcCursor:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeProcConnection:
    def __init__(self, rows):
        self.cur = FakeProcCursor(rows)

    def cursor(self):
        return self.cur


class FakeCursor:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []
        self.description = [("id",), ("status",)]

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchall(self):
        return [(1, "open")]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self):
        self.cur = FakeCursor()
        self.committed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True


def test_postgres_translates_named_to_pyformat_dict():
    conn = FakeConnection()
    rows = PostgresDialect().execute_named(conn, "SELECT id, status FROM orders WHERE status = :status", {"status": "open"})

    sql, params = conn.cur.calls[0]
    assert sql == "SELECT id, status FROM orders WHERE status = %(status)s"
    assert params == {"status": "open"}
    assert rows == [{"id": 1, "status": "open"}]
    assert conn.committed


def test_oracle_passes_named_placeholders_through_unchanged():
    conn = FakeConnection()
    OracleDialect().execute_named(conn, "SELECT id, status FROM orders WHERE status = :status", {"status": "open"})

    sql, params = conn.cur.calls[0]
    assert sql == "SELECT id, status FROM orders WHERE status = :status"
    assert params == {"status": "open"}


def test_sqlserver_translates_named_to_ordered_positional():
    conn = FakeConnection()
    SqlServerDialect().execute_named(
        conn,
        "SELECT id, status FROM orders WHERE status = :status AND id = :id",
        {"status": "open", "id": 1},
    )

    sql, values = conn.cur.calls[0]
    assert sql == "SELECT id, status FROM orders WHERE status = ? AND id = ?"
    assert values == ["open", 1]


def test_postgres_groups_procedure_rows_by_routine_name():
    rows = [
        ("update_order_status", "PROCEDURE", "order_id", "integer", "IN"),
        ("update_order_status", "PROCEDURE", "new_status", "character varying", "IN"),
        ("get_order_total", "FUNCTION", "order_id", "integer", "IN"),
    ]
    conn = FakeProcConnection(rows)
    procs = PostgresDialect().introspect_stored_procedures(conn, "public")

    sql, params = conn.cur.calls[0]
    assert "information_schema.routines" in sql
    assert params == {"schema": "public"}

    by_name = {p.name: p for p in procs}
    assert by_name["update_order_status"].kind == "procedure"
    assert [p.name for p in by_name["update_order_status"].params] == ["order_id", "new_status"]
    assert by_name["get_order_total"].kind == "function"


def test_sqlserver_groups_procedure_rows_and_defaults_schema_to_dbo():
    rows = [("do_thing", "PROCEDURE", "x", "int", "IN")]
    conn = FakeProcConnection(rows)
    procs = SqlServerDialect().introspect_stored_procedures(conn, None)

    sql, params = conn.cur.calls[0]
    assert params == ("dbo",)
    assert procs[0].name == "do_thing"
    assert procs[0].params[0].mode == "IN"


def test_oracle_requires_an_owner():
    conn = FakeProcConnection([])
    with pytest.raises(ValueError):
        OracleDialect().introspect_stored_procedures(conn, None)


def test_oracle_groups_procedure_rows_by_object_name():
    rows = [
        ("UPDATE_ORDER_STATUS", "PROCEDURE", "ORDER_ID", "NUMBER", "IN"),
        ("UPDATE_ORDER_STATUS", "PROCEDURE", "NEW_STATUS", "VARCHAR2", "IN"),
    ]
    conn = FakeProcConnection(rows)
    procs = OracleDialect().introspect_stored_procedures(conn, "myschema")

    sql, params = conn.cur.calls[0]
    assert params == {"owner": "MYSCHEMA"}
    assert procs[0].name == "UPDATE_ORDER_STATUS"
    assert len(procs[0].params) == 2


def test_postgres_lists_base_tables_of_public_by_default():
    conn = FakeProcConnection([("customers",), ("orders",)])

    assert PostgresDialect().list_tables(conn, None) == ["customers", "orders"]
    sql, params = conn.cur.calls[0]
    assert "information_schema.tables" in sql and "table_type = 'BASE TABLE'" in sql
    assert params == {"schema": "public"}
    PostgresDialect().list_tables(conn, "sales")
    assert conn.cur.calls[1][1] == {"schema": "sales"}


def test_sqlserver_lists_base_tables_of_dbo_by_default():
    conn = FakeProcConnection([("Customers",)])

    assert SqlServerDialect().list_tables(conn, None) == ["Customers"]
    sql, params = conn.cur.calls[0]
    assert "table_type = 'BASE TABLE'" in sql
    assert params == ("dbo",)


def test_oracle_lists_its_own_tables_or_another_owners():
    conn = FakeProcConnection([("CUSTOMERS",)])

    assert OracleDialect().list_tables(conn, None) == ["CUSTOMERS"]
    assert "FROM user_tables" in conn.cur.calls[0][0]
    OracleDialect().list_tables(conn, "sales")
    sql, params = conn.cur.calls[1]
    assert "FROM all_tables WHERE owner = :owner" in sql
    assert params == {"owner": "SALES"}
