from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from legacy2mcp import cli
from legacy2mcp.analysis.model import Analysis
from legacy2mcp.analysis.pipeline import run_analysis
from legacy2mcp.analysis.report import render_html

FIXTURES = Path(__file__).parent / "fixtures" / "analyze"


@pytest.fixture(scope="module")
def html() -> str:
    return render_html(run_analysis(FIXTURES / "express_store"))


def _embedded(html: str) -> dict:
    m = re.search(r'<script id="analysis-data" type="application/json">(.*?)</script>', html, re.S)
    assert m
    return json.loads(m.group(1))


def test_report_is_self_contained(html):
    # No external scripts, styles, fonts, images or fetches.
    assert not re.search(r"<script[^>]+\bsrc=", html)
    assert not re.search(r"<link\b", html)
    assert "@import" not in html
    assert not re.search(r"url\(\s*['\"]?https?:", html)
    assert "connect-src 'none'" in html
    assert re.search(r"fetch\(|XMLHttpRequest", _app_js(html)) is None


def _app_js(html: str) -> str:
    return html.rsplit("<script>", 1)[1].split("</script>", 1)[0]


def test_embedded_analysis_round_trips(html):
    data = _embedded(html)
    assert Analysis.from_dict(data).project == "express_store"
    assert len(data["flows"]) == 10 and data["tools"]


def test_hostile_strings_cannot_break_out_of_script(tmp_path):
    (tmp_path / "app.py").write_text(
        'from flask import Flask\napp = Flask(__name__)\n\n@app.get("/x")\n'
        'def x():\n    """</script><script>alert(1)</script> <!-- &  """\n'
    )
    out = render_html(run_analysis(tmp_path))
    assert "<script>alert(1)" not in out
    assert "</script><script>" not in out.split('id="analysis-data"', 1)[1].split("</script>", 1)[0]
    assert _embedded(out)["flows"][0]["name"].startswith("</script><script>alert(1)</script>")


def test_report_has_every_section_hook(html):
    js = _app_js(html)
    for title in ("Executive summary", "Codebase map", "Call graph and data flow", "Business flows",
                  "Proposed MCP tools", "Appendix: methodology and scope"):
        assert title in js
    assert "legacy2mcp-selections/1" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_inline_scripts_parse(html, tmp_path):
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(scripts) == 2  # cytoscape + app
    for i, js in enumerate(scripts):
        path = tmp_path / f"s{i}.js"
        path.write_text(js)
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_cli_analyze_writes_html_and_report_regenerates(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["legacy2mcp", "analyze", str(FIXTURES / "flask_shop"), "--no-ai", "--out", str(tmp_path)])
    cli.main()
    assert (tmp_path / "report.html").is_file() and (tmp_path / "analysis.json").is_file()
    (tmp_path / "report.html").unlink()
    monkeypatch.setattr(sys, "argv", ["legacy2mcp", "report", str(tmp_path)])
    cli.main()
    assert "flask_shop" in (tmp_path / "report.html").read_text()
    assert "Wrote" in capsys.readouterr().out


def test_cli_report_missing_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["legacy2mcp", "report", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "run `legacy2mcp analyze` first" in capsys.readouterr().err
