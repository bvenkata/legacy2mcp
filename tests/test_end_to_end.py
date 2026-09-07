from __future__ import annotations

import json
import threading
import time

import pytest

from legacy2mcp.config import AdapterConfig, AuditConfig, Legacy2McpConfig, SecurityConfig, ServerConfig
from legacy2mcp.server import Legacy2McpApp
from tests.mock_soap_service import create_app

PORT = 8199
WSDL_URL = f"http://127.0.0.1:{PORT}/calculator?wsdl"


@pytest.fixture(scope="module", autouse=True)
def mock_soap_service():
    app = create_app(f"http://127.0.0.1:{PORT}/calculator")
    thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=PORT, use_reloader=False),
        daemon=True,
    )
    thread.start()
    time.sleep(1)
    yield


def _build_app(tmp_path, allow_write_operations: bool = False, exclude=None, include=None) -> Legacy2McpApp:
    adapter_cfg = AdapterConfig(
        id="calc",
        type="soap",
        enabled=True,
        config={
            "wsdl_url": WSDL_URL,
            "allow_write_operations": allow_write_operations,
            "exclude_operations": exclude or [],
            "include_operations": include,
        },
    )
    config = Legacy2McpConfig(
        server=ServerConfig(name="test"),
        adapters=[adapter_cfg],
        security=SecurityConfig(audit=AuditConfig(enabled=True, path=str(tmp_path / "audit.log"))),
    )
    return Legacy2McpApp(config)


def test_discovers_all_calculator_operations(tmp_path):
    app = _build_app(tmp_path)
    names = {tool_def.name for _, tool_def in app._tools.values()}
    assert names == {"calc_Add", "calc_Subtract", "calc_Multiply", "calc_Divide"}
    app.close()


def test_input_schema_matches_wsdl_types(tmp_path):
    app = _build_app(tmp_path)
    _, tool_def = app._tools["calc_Add"]
    assert tool_def.input_schema == {
        "type": "object",
        "properties": {"intA": {"type": "integer"}, "intB": {"type": "integer"}},
        "required": ["intA", "intB"],
        "additionalProperties": False,
    }
    app.close()


@pytest.mark.asyncio
async def test_valid_call_returns_named_result(tmp_path):
    app = _build_app(tmp_path)
    result = await app._dispatch("calc_Add", {"intA": 5, "intB": 6})
    assert json.loads(result[0].text) == {"AddResult": 11}
    app.close()


@pytest.mark.asyncio
async def test_type_mismatch_rejected_before_reaching_soap(tmp_path):
    app = _build_app(tmp_path)
    with pytest.raises(ValueError, match="failed schema validation"):
        await app._dispatch("calc_Add", {"intA": "five", "intB": 6})
    app.close()


@pytest.mark.asyncio
async def test_missing_required_field_rejected(tmp_path):
    app = _build_app(tmp_path)
    with pytest.raises(ValueError, match="failed schema validation"):
        await app._dispatch("calc_Add", {"intA": 5})
    app.close()


@pytest.mark.asyncio
async def test_extra_field_rejected(tmp_path):
    app = _build_app(tmp_path)
    with pytest.raises(ValueError, match="failed schema validation"):
        await app._dispatch("calc_Add", {"intA": 5, "intB": 6, "intC": 7})
    app.close()


@pytest.mark.asyncio
async def test_unknown_tool_rejected(tmp_path):
    app = _build_app(tmp_path)
    with pytest.raises(ValueError, match="Unknown tool"):
        await app._dispatch("calc_DoesNotExist", {})
    app.close()


@pytest.mark.asyncio
async def test_division_by_zero_soap_fault_surfaces_as_adapter_error(tmp_path):
    app = _build_app(tmp_path)
    with pytest.raises(ValueError):
        await app._dispatch("calc_Divide", {"intA": 10, "intB": 0})
    app.close()


def test_exclude_operations_filters_tool_out(tmp_path):
    app = _build_app(tmp_path, exclude=["Divide"])
    names = {tool_def.name for _, tool_def in app._tools.values()}
    assert "calc_Divide" not in names
    assert "calc_Add" in names
    app.close()


def test_include_operations_allowlist(tmp_path):
    app = _build_app(tmp_path, include=["Add"])
    names = {tool_def.name for _, tool_def in app._tools.values()}
    assert names == {"calc_Add"}
    app.close()


def test_every_tool_call_is_audited(tmp_path):
    import asyncio

    app = _build_app(tmp_path)
    asyncio.run(app._dispatch("calc_Add", {"intA": 1, "intB": 2}))
    audit_path = app.audit.path
    lines = audit_path.read_text().strip().splitlines()
    events = [json.loads(line)["event"] for line in lines]
    assert "tool_call_start" in events
    assert "tool_call_success" in events
    app.close()


def test_write_like_operation_naming_is_gated_by_default():
    # This mock service has no write-like ops to test against directly,
    # so we test the name-sniffing helper itself, which is what
    # actually enforces the safe-by-default posture.
    from legacy2mcp.adapters.soap_adapter import _looks_like_write

    assert _looks_like_write("CreateOrder")
    assert _looks_like_write("DeleteRecord")
    assert _looks_like_write("UpdateStatus")
    assert not _looks_like_write("GetRecordStatus")
    assert not _looks_like_write("Add")
    assert not _looks_like_write("ListRecords")
