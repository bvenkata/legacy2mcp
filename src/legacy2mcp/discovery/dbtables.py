"""
Read a live database's tables into the same `DbFinding` the ORM scan
produces, so `legacy2mcp init --db-dialect` gets a starter `type: db`
config straight from a database, the way `init --rest-url` does from an
OpenAPI spec. classify.build_config() turns the finding into the config;
nothing here knows the config's shape.

Only system catalogs are read (the dialect's `list_tables()` and
`introspect_columns()`), the same posture as discovery/storedproc.py: no
row of any table is selected, and nothing is written. The DSN is read from
the environment variable the caller names and is never stored -- the
finding carries the variable's name, which is what the config records.

Every table lands at `operations: [select]` with writes off. A schema with
more than `max_tables` base tables is refused unless the caller names the
tables to use (`--db-tables`): exposing a whole large schema to an agent
should be a choice, not a default.
"""

from __future__ import annotations

from legacy2mcp.adapters.db_dialects import DbDialect, build_dialect
from legacy2mcp.discovery.contract import ContractError
from legacy2mcp.discovery.scan import DbFinding, DbTableFinding

#: Tables read without an explicit --db-tables list.
DEFAULT_MAX_TABLES = 25


def discover_db_tables(
    dialect_name: str,
    dsn: str,
    dsn_env: str,
    db_schema: str | None = None,
    tables: list[str] | None = None,
    max_tables: int = DEFAULT_MAX_TABLES,
    timeout_seconds: float = 15.0,
    dialect: DbDialect | None = None,
) -> DbFinding:
    """A `DbFinding` for `tables` of `db_schema` (every base table when
    `tables` is None, up to `max_tables`), each with its columns. `dialect`
    replaces build_dialect(dialect_name) (a test seam). Raises
    ContractError when the database can't be reached or read, a named
    table doesn't exist, or the schema has no tables or too many."""
    if dialect is None:
        try:
            dialect = build_dialect(dialect_name)
        except ValueError as exc:
            raise ContractError(str(exc)) from exc

    where = f"schema '{db_schema}'" if db_schema else "the default schema"
    try:
        conn = dialect.connect(dsn, timeout_seconds)
    except Exception as exc:
        # The driver's message can quote the DSN; name the variable instead.
        raise ContractError(
            f"Could not connect to the {dialect_name} database in ${dsn_env}: {type(exc).__name__}"
        ) from exc

    try:
        try:
            available = dialect.list_tables(conn, db_schema)
        except Exception as exc:
            raise ContractError(f"Could not list the tables of {where}: {exc}") from exc

        chosen = _choose(available, tables, where, max_tables)
        found = []
        for table in chosen:
            try:
                columns = dialect.introspect_columns(conn, table, db_schema)
            except Exception as exc:
                raise ContractError(f"Could not read the columns of table '{table}': {exc}") from exc
            found.append(
                DbTableFinding(table=table, columns=[c.name for c in columns], file=f"{dialect_name} database")
            )
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return DbFinding(dialect=dialect_name, tables=found, dsn_env=dsn_env, db_schema=db_schema)


def _choose(available: list[str], tables: list[str] | None, where: str, max_tables: int) -> list[str]:
    if tables:
        # Oracle's catalog names are upper case; match a name as the
        # database would resolve it unquoted, but keep the catalog's spelling.
        by_folded = {name.casefold(): name for name in available}
        missing = [t for t in tables if t.casefold() not in by_folded]
        if missing:
            raise ContractError(f"No base table named {', '.join(repr(t) for t in missing)} in {where}.")
        # one entry per table, however often or in whatever case it was named
        return list(dict.fromkeys(by_folded[t.casefold()] for t in tables))

    if not available:
        raise ContractError(f"Found no base tables in {where}. Pass --db-schema to read another schema.")
    if len(available) > max_tables:
        shown = ", ".join(available[:10]) + (", ..." if len(available) > 10 else "")
        raise ContractError(
            f"{where.capitalize()} has {len(available)} base tables, more than the {max_tables} "
            f"read without a list ({shown}). Pass --db-tables with the ones an agent should see, "
            f"or raise --db-max-tables."
        )
    return available
