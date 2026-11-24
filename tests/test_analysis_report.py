from __future__ import annotations

import json
import re
from html.parser import HTMLParser
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


class _Scripts(HTMLParser):
    """Collect every <script> element (any case) with its attributes and text."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts: list[tuple[dict, str]] = []
        self.links = 0
        self._attrs: dict | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._attrs, self._buf = dict(attrs), []
        elif tag == "link":
            self.links += 1

    def handle_data(self, data):
        if self._attrs is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._attrs is not None:
            self.scripts.append((self._attrs, "".join(self._buf)))
            self._attrs = None


def _parse(html: str) -> _Scripts:
    parser = _Scripts()
    parser.feed(html)
    return parser


def _embedded(html: str) -> dict:
    (data,) = [text for attrs, text in _parse(html).scripts if attrs.get("id") == "analysis-data"]
    return json.loads(data)


def test_report_is_self_contained(html):
    # No external scripts, styles, fonts, images or fetches.
    parsed = _parse(html)
    assert all("src" not in attrs for attrs, _ in parsed.scripts)
    assert parsed.links == 0
    assert "@import" not in html
    assert not re.search(r"url\(\s*['\"]?https?:", html)
    assert "connect-src 'none'" in html
    assert re.search(r"fetch\(|XMLHttpRequest", _app_js(html)) is None


def _inline_js(html: str) -> list[str]:
    return [text for attrs, text in _parse(html).scripts if not attrs]


def _app_js(html: str) -> str:
    return _inline_js(html)[-1]


def test_embedded_analysis_round_trips(html):
    data = _embedded(html)
    assert Analysis.from_dict(data).project == "express_store"
    assert len(data["flows"]) == 10 and data["tools"]


def _embedded_raw(html: str) -> str:
    (data,) = [text for attrs, text in _parse(html).scripts if attrs.get("id") == "analysis-data"]
    return data


def test_hostile_strings_cannot_break_out_of_script(tmp_path):
    (tmp_path / "app.py").write_text(
        'from flask import Flask\napp = Flask(__name__)\n\n@app.get("/x")\n'
        'def x():\n    """</script><script>alert(1)</script> <!-- &  """\n'
    )
    out = render_html(run_analysis(tmp_path))
    assert "<script>alert(1)" not in out
    assert "</script>" not in _embedded_raw(out)
    assert _embedded(out)["flows"][0]["name"].startswith("</script><script>alert(1)</script>")


def test_report_has_every_section_hook(html):
    js = _app_js(html)
    for title in ("Executive summary", "Codebase map", "Call graph and data flow", "Business flows",
                  "Proposed MCP tools", "Appendix: methodology and scope"):
        assert title in js
    assert "legacy2mcp-selections/1" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_inline_scripts_parse(html, tmp_path):
    scripts = _inline_js(html)
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
