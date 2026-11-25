"""Language-independent helpers shared by the analyzers."""

from __future__ import annotations

import re

SQL_WRITE_VERBS = {"INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "TRUNCATE", "ALTER", "CREATE", "REPLACE", "UPSERT"}
SQL_READ_VERBS = {"SELECT", "WITH", "SHOW", "EXPLAIN"}

_SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|INTO|UPDATE|JOIN|TABLE)\s+[\"`\[]?([A-Za-z_][\w.]*)", re.IGNORECASE
)


def sql_effect(sql: str) -> tuple[str, str, str] | None:
    """(op, detail, verb) for a literal SQL string, or None if it doesn't look like SQL.

    detail is the first table named (FROM/INTO/UPDATE/...), falling back to
    the statement verb when no table can be pulled out."""
    stripped = sql.strip()
    if not stripped:
        return None
    verb = stripped.split(None, 1)[0].upper()
    if verb in SQL_WRITE_VERBS:
        op = "write"
    elif verb in SQL_READ_VERBS:
        op = "read"
    else:
        return None
    m = _SQL_TABLE_RE.search(stripped)
    return op, (m.group(1) if m else verb), verb
