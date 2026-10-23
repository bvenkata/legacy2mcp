from __future__ import annotations

import importlib.metadata

import pytest

from legacy2mcp.cli import main


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag_prints_installed_version_and_exits(flag, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["legacy2mcp", flag])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    assert capsys.readouterr().out == f"legacy2mcp {importlib.metadata.version('legacy2mcp')}\n"
