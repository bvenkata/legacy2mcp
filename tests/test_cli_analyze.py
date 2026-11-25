from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from legacy2mcp import cli

FIXTURE = Path(__file__).parent / "fixtures" / "analyze" / "django_clinic"


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["legacy2mcp", *argv])
    cli.main()


def test_analyze_writes_analysis_json(monkeypatch, tmp_path, capsys):
    _run(monkeypatch, "analyze", str(FIXTURE), "--no-ai", "--out", str(tmp_path))
    data = json.loads((tmp_path / "analysis.json").read_text())
    assert data["schema_version"] == "1"
    assert data["project"] == "django_clinic"
    assert "django" in data["summary"]["frameworks"]
    out = capsys.readouterr()
    assert "Entry points: 7" in out.out
    assert out.err == ""  # --no-ai: no "AI not available" note


def test_analyze_exclude_flag(monkeypatch, tmp_path):
    _run(monkeypatch, "analyze", str(FIXTURE), "--no-ai", "--out", str(tmp_path), "--exclude", "appointments/views.py")
    data = json.loads((tmp_path / "analysis.json").read_text())
    assert data["scan"]["excluded"]["exclude_flag"] == 1
    assert data["scan"]["exclude_patterns"] == ["appointments/views.py"]


def test_analyze_missing_path_exits_1(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "analyze", str(tmp_path / "nope"), "--no-ai", "--out", str(tmp_path))
    assert exc.value.code == 1
    assert "is not a directory" in capsys.readouterr().err


def test_analyze_rejects_unknown_format(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, "analyze", str(FIXTURE), "--format", "pdf", "--out", str(tmp_path))
    assert exc.value.code == 2
