"""
Per-database dialect plumbing for the DB adapter.

Three things differ across Postgres / SQL Server / Oracle and are
isolated here so db_adapter.py stays dialect-agnostic:
  1. Connecting (driver-specific; imported lazily so installing
     legacy2mcp doesn't require all three DB-API drivers).
  2. Introspecting a table's columns (different system catalogs).
  3. Executing SQL written with our own dialect-neutral `:name`
     placeholder convention and a dict of params -- each dialect
     translates that to whatever its driver actually accepts
     (psycopg: %(name)s with a dict; oracledb: :name with a dict
     natively; pyodbc: positional `?` with an ordered list, since
     ODBC has no named-parameter support).

db_adapter.py builds all SQL (SELECT/INSERT for allowlisted tables,
plus the config-supplied named queries) using only `:name` tokens, so
query construction never depends on which dialect is in play -- only
`execute_named()` does.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

_PARAM_TOKEN = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


@dataclass
class ColumnInfo:
    name: str
    sql_type: str
    nullable: bool
    has_default: bool


@dataclass
class ProcedureParamInfo:
    name: str
    sql_type: str
    mode: str  # "IN" / "OUT" / "INOUT", or "" if the catalog didn't say


@dataclass
class ProcedureInfo:
    name: str
    kind: str  # "procedure" | "function"
    params: list[ProcedureParamInfo]


class DbDialect(ABC):
    """One implementation per supported database. Pure SQL-string helpers
    (quote_ident, qualify) live here too since quoting rules are
    dialect-specific; building SELECT/INSERT statements from them stays
    in db_adapter.py so the allowlist/schema logic has one home."""

    name: str = "base"

    @abstractmethod
    def connect(self, dsn: str, timeout_seconds: float) -> Any:
        """Open a DB-API connection. Raises on failure."""
        raise NotImplementedError

    @abstractmethod
    def introspect_columns(self, conn: Any, table: str, db_schema: str | None) -> list[ColumnInfo]:
        raise NotImplementedError

    @abstractmethod
    def introspect_stored_procedures(self, conn: Any, db_schema: str | None) -> list[ProcedureInfo]:
        """Live catalog read for `legacy2mcp discover`'s stored-procedure
        import -- documentation only today: no adapter can *call* a stored
        procedure yet (see docs/roadmap.md), so this exists purely to
        capture what's there for a human to review in the generated
        contract."""
        raise NotImplementedError

    @abstractmethod
    def execute_named(self, conn: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute `sql` (written with :name placeholders) bound to `params`
        and return the result rows as plain dicts. For statements with no
        result set (INSERT), returns []."""
        raise NotImplementedError

    def quote_ident(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    def qualify(self, table: str, db_schema: str | None) -> str:
        if db_schema:
            return f"{self.quote_ident(db_schema)}.{self.quote_ident(table)}"
        return self.quote_ident(table)

    def _rows_from_cursor(self, cursor: Any) -> list[dict[str, Any]]:
        if cursor.description is None:
            return []
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _group_procedure_rows(
        self, rows: list[tuple], kind_of: Any = lambda routine_type: routine_type
    ) -> list[ProcedureInfo]:
        """Shared grouping for the information_schema-style one-row-per-
        parameter catalog shape (Postgres/SQL Server): rows of
        (routine_name, routine_type, param_name_or_None, data_type_or_None,
        param_mode_or_None) ordered by routine name then ordinal position."""
        procedures: dict[str, ProcedureInfo] = {}
        for routine_name, routine_type, param_name, data_type, param_mode in rows:
            proc = procedures.get(routine_name)
            if proc is None:
                proc = ProcedureInfo(name=routine_name, kind=kind_of(routine_type), params=[])
                procedures[routine_name] = proc
            if param_name is not None:
                proc.params.append(ProcedureParamInfo(name=param_name, sql_type=data_type or "", mode=param_mode or ""))
        return list(procedures.values())


class PostgresDialect(DbDialect):
    name = "postgres"

    def connect(self, dsn: str, timeout_seconds: float) -> Any:
        import psycopg

        return psycopg.connect(dsn, connect_timeout=timeout_seconds)

    def introspect_columns(self, conn: Any, table: str, db_schema: str | None) -> list[ColumnInfo]:
        sql = (
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = %(table)s AND table_schema = %(schema)s "
            "ORDER BY ordinal_position"
        )
        with conn.cursor() as cur:
            cur.execute(sql, {"table": table, "schema": db_schema or "public"})
            rows = cur.fetchall()
        return [
            ColumnInfo(name=r[0], sql_type=r[1], nullable=(r[2] == "YES"), has_default=r[3] is not None)
            for r in rows
        ]

    def introspect_stored_procedures(self, conn: Any, db_schema: str | None) -> list[ProcedureInfo]:
        sql = (
            "SELECT r.routine_name, r.routine_type, p.parameter_name, p.data_type, p.parameter_mode "
            "FROM information_schema.routines r "
            "LEFT JOIN information_schema.parameters p "
            "  ON r.specific_name = p.specific_name AND r.specific_schema = p.specific_schema "
            "WHERE r.routine_schema = %(schema)s AND r.routine_type IN ('PROCEDURE', 'FUNCTION') "
            "ORDER BY r.routine_name, p.ordinal_position"
        )
        with conn.cursor() as cur:
            cur.execute(sql, {"schema": db_schema or "public"})
            rows = cur.fetchall()
        return self._group_procedure_rows(rows, kind_of=lambda t: "function" if t == "FUNCTION" else "procedure")

    def execute_named(self, conn: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        translated = _PARAM_TOKEN.sub(lambda m: f"%({m.group(1)})s", sql)
        with conn.cursor() as cur:
            cur.execute(translated, params)
            rows = self._rows_from_cursor(cur)
            conn.commit()
            return rows


class SqlServerDialect(DbDialect):
    name = "mssql"

    def connect(self, dsn: str, timeout_seconds: float) -> Any:
        import pyodbc

        return pyodbc.connect(dsn, timeout=int(timeout_seconds))

    def introspect_columns(self, conn: Any, table: str, db_schema: str | None) -> list[ColumnInfo]:
        sql = (
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = ? AND table_schema = ? "
            "ORDER BY ordinal_position"
        )
        cur = conn.cursor()
        cur.execute(sql, (table, db_schema or "dbo"))
        rows = cur.fetchall()
        return [
            ColumnInfo(name=r[0], sql_type=r[1], nullable=(r[2] == "YES"), has_default=r[3] is not None)
            for r in rows
        ]

    def introspect_stored_procedures(self, conn: Any, db_schema: str | None) -> list[ProcedureInfo]:
        sql = (
            "SELECT r.routine_name, r.routine_type, p.parameter_name, p.data_type, p.parameter_mode "
            "FROM information_schema.routines r "
            "LEFT JOIN information_schema.parameters p "
            "  ON r.specific_name = p.specific_name AND r.specific_schema = p.specific_schema "
            "WHERE r.routine_schema = ? AND r.routine_type IN ('PROCEDURE', 'FUNCTION') "
            "ORDER BY r.routine_name, p.ordinal_position"
        )
        cur = conn.cursor()
        cur.execute(sql, (db_schema or "dbo",))
        rows = cur.fetchall()
        return self._group_procedure_rows(rows, kind_of=lambda t: "function" if t == "FUNCTION" else "procedure")

    def execute_named(self, conn: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        order: list[str] = []

        def _replace(m: re.Match) -> str:
            order.append(m.group(1))
            return "?"

        translated = _PARAM_TOKEN.sub(_replace, sql)
        values = [params[name] for name in order]
        cur = conn.cursor()
        cur.execute(translated, values)
        rows = self._rows_from_cursor(cur)
        conn.commit()
        return rows


class OracleDialect(DbDialect):
    name = "oracle"

    def connect(self, dsn: str, timeout_seconds: float) -> Any:
        import oracledb

        return oracledb.connect(dsn, tcp_connect_timeout=timeout_seconds)

    def introspect_columns(self, conn: Any, table: str, db_schema: str | None) -> list[ColumnInfo]:
        if db_schema:
            sql = (
                "SELECT column_name, data_type, nullable, data_default "
                "FROM all_tab_columns "
                "WHERE table_name = :table AND owner = :owner "
                "ORDER BY column_id"
            )
            params = {"table": table.upper(), "owner": db_schema.upper()}
        else:
            sql = (
                "SELECT column_name, data_type, nullable, data_default "
                "FROM user_tab_columns "
                "WHERE table_name = :table "
                "ORDER BY column_id"
            )
            params = {"table": table.upper()}
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        return [
            ColumnInfo(name=r[0], sql_type=r[1], nullable=(r[2] == "Y"), has_default=r[3] is not None)
            for r in rows
        ]

    def introspect_stored_procedures(self, conn: Any, db_schema: str | None) -> list[ProcedureInfo]:
        # Standalone procedures/functions (ALL_PROCEDURES.PROCEDURE_NAME IS
        # NULL) plus the ones nested inside a package. A packaged routine's
        # ALL_PROCEDURES row has OBJECT_NAME = the package, while its
        # ALL_ARGUMENTS rows have PACKAGE_NAME = the package and OBJECT_NAME =
        # the routine, so they're joined on PACKAGE_NAME + SUBPROGRAM_ID
        # (which also keeps overloads apart). Standalone arguments are those
        # with no PACKAGE_NAME -- otherwise a packaged routine sharing a
        # standalone one's name would leak its arguments into it. The package
        # row itself has no OBJECT_TYPE of PROCEDURE/FUNCTION, so a function
        # is recognised by its return value: the unnamed argument at
        # POSITION 0. Packaged names are "PACKAGE.ROUTINE", with "#<overload>"
        # appended when the package overloads that name.
        owner = (db_schema or "").upper()
        if not owner:
            raise ValueError("Oracle stored-procedure introspection needs an owner/schema -- pass --db-dialect's schema.")
        sql = (
            "SELECT routine_name, routine_type, argument_name, data_type, in_out FROM ("
            "SELECT p.object_name AS routine_name, p.object_type AS routine_type, "
            "  a.argument_name, a.data_type, a.in_out, a.position "
            "FROM all_procedures p "
            "LEFT JOIN all_arguments a ON a.owner = p.owner AND a.object_name = p.object_name "
            "  AND a.package_name IS NULL "
            "WHERE p.owner = :owner AND p.procedure_name IS NULL "
            "  AND p.object_type IN ('PROCEDURE', 'FUNCTION') "
            "UNION ALL "
            "SELECT p.object_name || '.' || p.procedure_name "
            "  || CASE WHEN p.overload IS NOT NULL THEN '#' || p.overload ELSE '' END, "
            "  CASE WHEN EXISTS (SELECT 1 FROM all_arguments r WHERE r.owner = p.owner "
            "    AND r.package_name = p.object_name AND r.subprogram_id = p.subprogram_id "
            "    AND r.position = 0 AND r.argument_name IS NULL) "
            "  THEN 'FUNCTION' ELSE 'PROCEDURE' END, "
            "  a.argument_name, a.data_type, a.in_out, a.position "
            "FROM all_procedures p "
            "LEFT JOIN all_arguments a ON a.owner = p.owner AND a.package_name = p.object_name "
            "  AND a.subprogram_id = p.subprogram_id "
            "WHERE p.owner = :owner AND p.object_type = 'PACKAGE' AND p.procedure_name IS NOT NULL"
            ") ORDER BY routine_name, position"
        )
        cur = conn.cursor()
        cur.execute(sql, {"owner": owner})
        rows = cur.fetchall()
        return self._group_procedure_rows(rows, kind_of=lambda t: "function" if t == "FUNCTION" else "procedure")

    def execute_named(self, conn: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = self._rows_from_cursor(cur)
        conn.commit()
        return rows


_DIALECTS: dict[str, type[DbDialect]] = {
    "postgres": PostgresDialect,
    "mssql": SqlServerDialect,
    "oracle": OracleDialect,
}


def build_dialect(name: str) -> DbDialect:
    cls = _DIALECTS.get(name)
    if cls is None:
        raise ValueError(f"Unknown DB dialect: '{name}'")
    return cls()
