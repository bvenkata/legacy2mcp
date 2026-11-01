from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from legacy2mcp.adapters.db_dialects import OracleDialect, ProcedureInfo, ProcedureParamInfo
from legacy2mcp.discovery.contract import ContractError
from legacy2mcp.discovery.storedproc import _operation_from_procedure, contract_from_stored_procedures


def test_operation_from_procedure_maps_params_and_flags_no_adapter():
    proc = ProcedureInfo(
        name="update_order_status",
        kind="procedure",
        params=[
            ProcedureParamInfo(name="order_id", sql_type="integer", mode="IN"),
            ProcedureParamInfo(name="new_status", sql_type="character varying", mode="IN"),
            ProcedureParamInfo(name="result", sql_type="boolean", mode="OUT"),
        ],
    )
    op = _operation_from_procedure(proc)

    assert op.source_kind == "db_proc"
    assert op.side_effect == "write"  # "update_" prefix
    assert op.confidence == "high"
    assert op.needs_review is True
    assert any("no runtime adapter" in n.lower() for n in op.notes)

    by_name = {f.name: f for f in op.fields}
    assert by_name["order_id"].type == "integer"
    assert by_name["order_id"].required is True
    assert by_name["new_status"].type == "string"
    assert by_name["result"].type == "boolean"
    assert by_name["result"].required is False  # OUT params aren't inputs


def test_operation_from_procedure_filters_nameless_params():
    # Oracle functions carry an unnamed RETURN argument -- not a real input.
    proc = ProcedureInfo(name="get_total", kind="function", params=[ProcedureParamInfo(name="", sql_type="number", mode="OUT")])
    op = _operation_from_procedure(proc)
    assert op.fields == []


class _FakeDialect:
    def __init__(self, procs):
        self._procs = procs
        self.connected_with = None

    def connect(self, dsn, timeout_seconds):
        self.connected_with = (dsn, timeout_seconds)
        return object()

    def introspect_stored_procedures(self, conn, db_schema):
        return self._procs


def test_contract_from_stored_procedures_end_to_end():
    fake = _FakeDialect([ProcedureInfo(name="get_order", kind="function", params=[])])
    with patch("legacy2mcp.discovery.storedproc.build_dialect", return_value=fake):
        contract = contract_from_stored_procedures("postgres", "postgresql://x", db_schema="public")

    assert contract.operations[0].name == "get_order"
    assert fake.connected_with == ("postgresql://x", 15.0)


def test_contract_from_stored_procedures_wraps_connection_failure():
    class _FailingDialect:
        def connect(self, dsn, timeout_seconds):
            raise RuntimeError("connection refused")

    with patch("legacy2mcp.discovery.storedproc.build_dialect", return_value=_FailingDialect()):
        with pytest.raises(ContractError):
            contract_from_stored_procedures("postgres", "postgresql://x")


def test_contract_from_stored_procedures_rejects_unknown_dialect():
    with pytest.raises(ContractError):
        contract_from_stored_procedures("mysql", "dsn")


def _oracle_catalog() -> sqlite3.Connection:
    """A tiny stand-in for Oracle's ALL_PROCEDURES / ALL_ARGUMENTS (only the
    columns the query reads), laid out the way Oracle fills them: a packaged
    routine's ALL_PROCEDURES row has OBJECT_NAME = the package, its
    ALL_ARGUMENTS rows have PACKAGE_NAME = the package, OBJECT_NAME = the
    routine, and a function's return value is the unnamed POSITION 0 row."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE all_procedures (owner, object_name, procedure_name, object_type, overload, subprogram_id)"
    )
    conn.execute(
        "CREATE TABLE all_arguments (owner, package_name, object_name, subprogram_id, "
        "argument_name, data_type, in_out, position)"
    )
    conn.executemany(
        "INSERT INTO all_procedures VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("HR", "UPDATE_ORDER_STATUS", None, "PROCEDURE", None, 1),
            ("HR", "BILLING", None, "PACKAGE", None, 0),
            ("HR", "BILLING", "APPLY_PAYMENT", "PACKAGE", None, 1),
            ("HR", "BILLING", "GET_BALANCE", "PACKAGE", None, 2),
            ("HR", "BILLING", "UPDATE_ORDER_STATUS", "PACKAGE", None, 3),
            ("HR", "BILLING", "FIND", "PACKAGE", "1", 4),
            ("HR", "BILLING", "FIND", "PACKAGE", "2", 5),
            ("OTHER", "PAYROLL", "RUN", "PACKAGE", None, 1),
        ],
    )
    conn.executemany(
        "INSERT INTO all_arguments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("HR", None, "UPDATE_ORDER_STATUS", 1, "NEW_STATUS", "VARCHAR2", "IN", 2),
            ("HR", None, "UPDATE_ORDER_STATUS", 1, "ORDER_ID", "NUMBER", "IN", 1),
            ("HR", "BILLING", "APPLY_PAYMENT", 1, "INVOICE_ID", "NUMBER", "IN", 1),
            ("HR", "BILLING", "APPLY_PAYMENT", 1, "AMOUNT", "NUMBER", "IN", 2),
            ("HR", "BILLING", "GET_BALANCE", 2, None, "NUMBER", "OUT", 0),
            ("HR", "BILLING", "GET_BALANCE", 2, "CUSTOMER_ID", "NUMBER", "IN", 1),
            ("HR", "BILLING", "UPDATE_ORDER_STATUS", 3, "P_ORDER", "NUMBER", "IN", 1),
            ("HR", "BILLING", "FIND", 4, "ID", "NUMBER", "IN", 1),
            ("HR", "BILLING", "FIND", 5, "NAME", "VARCHAR2", "IN", 1),
            ("OTHER", "PAYROLL", "RUN", 1, "MONTH", "NUMBER", "IN", 1),
        ],
    )
    return conn


def test_oracle_finds_procedures_nested_in_a_package():
    procs = OracleDialect().introspect_stored_procedures(_oracle_catalog(), "hr")
    by_name = {p.name: p for p in procs}

    assert set(by_name) == {
        "UPDATE_ORDER_STATUS",
        "BILLING.APPLY_PAYMENT",
        "BILLING.GET_BALANCE",
        "BILLING.UPDATE_ORDER_STATUS",
        "BILLING.FIND#1",
        "BILLING.FIND#2",
    }
    assert by_name["BILLING.APPLY_PAYMENT"].kind == "procedure"
    assert [p.name for p in by_name["BILLING.APPLY_PAYMENT"].params] == ["INVOICE_ID", "AMOUNT"]
    assert by_name["BILLING.GET_BALANCE"].kind == "function"
    assert [p.name for p in by_name["BILLING.GET_BALANCE"].params if p.name] == ["CUSTOMER_ID"]
    assert [p.name for p in by_name["BILLING.FIND#1"].params] == ["ID"]
    assert [p.name for p in by_name["BILLING.FIND#2"].params] == ["NAME"]


def test_oracle_standalone_procedure_keeps_only_its_own_arguments():
    # BILLING.UPDATE_ORDER_STATUS shares the standalone procedure's name; its
    # P_ORDER argument must not show up on the standalone one.
    procs = OracleDialect().introspect_stored_procedures(_oracle_catalog(), "HR")
    standalone = next(p for p in procs if p.name == "UPDATE_ORDER_STATUS")

    assert standalone.kind == "procedure"
    assert [p.name for p in standalone.params] == ["ORDER_ID", "NEW_STATUS"]
    packaged = next(p for p in procs if p.name == "BILLING.UPDATE_ORDER_STATUS")
    assert [p.name for p in packaged.params] == ["P_ORDER"]


def test_packaged_procedure_contract_names_its_package():
    with patch.object(OracleDialect, "connect", return_value=_oracle_catalog()):
        contract = contract_from_stored_procedures("oracle", "dsn", db_schema="hr")

    op = next(o for o in contract.operations if o.db_object == "BILLING.APPLY_PAYMENT")
    assert op.source_kind == "db_proc"
    assert [f.name for f in op.fields] == ["INVOICE_ID", "AMOUNT"]
    assert "UPDATE_ORDER_STATUS" in {o.db_object for o in contract.operations}
