"""
Test-only DbDialect backed by stdlib sqlite3.

Not shipped in src/ and not one of the three dialects legacy2mcp
supports in config ("postgres" / "mssql" / "oracle") -- it exists so
test_db_adapter.py can exercise DbAdapter's allowlisting, schema
generation, limit enforcement and write-gating against a real,
executing database instead of a hand-rolled mock of a DB-API
connection. sqlite3 conveniently supports named `:name` placeholders
natively, so no translation step is needed here (unlike SqlServerDialect).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from legacy2mcp.adapters.db_dialects import ColumnInfo, DbDialect, ProcedureInfo


class SqliteTestDialect(DbDialect):
    name = "sqlite"

    def connect(self, dsn: str, timeout_seconds: float) -> Any:
        conn = sqlite3.connect(dsn, timeout=timeout_seconds)
        return conn

    def list_tables(self, conn: Any, db_schema: str | None) -> list[str]:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [r[0] for r in cur.fetchall()]

    def introspect_columns(self, conn: Any, table: str, db_schema: str | None) -> list[ColumnInfo]:
        cur = conn.execute(f"PRAGMA table_info({self.quote_ident(table)})")
        rows = cur.fetchall()
        # row: (cid, name, type, notnull, dflt_value, pk)
        return [
            ColumnInfo(name=r[1], sql_type=r[2], nullable=(r[3] == 0), has_default=r[4] is not None)
            for r in rows
        ]

    def introspect_stored_procedures(self, conn: Any, db_schema: str | None) -> list[ProcedureInfo]:
        return []  # sqlite has no stored procedures; not exercised by test_db_adapter.py

    def execute_named(self, conn: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        cur = conn.execute(sql, params)
        rows = self._rows_from_cursor(cur)
        conn.commit()
        return rows
