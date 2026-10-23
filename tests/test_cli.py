from __future__ import annotations

import importlib.metadata
import sys

import pytest

from legacy2mcp import cli


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag_prints_the_installed_version_and_exits(monkeypatch, capsys, flag):
    monkeypatch.setattr(sys, "argv", ["legacy2mcp", flag])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"legacy2mcp {importlib.metadata.version('legacy2mcp')}"
