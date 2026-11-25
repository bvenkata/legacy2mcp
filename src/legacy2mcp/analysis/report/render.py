"""
Stage 9 of `legacy2mcp analyze`: render the analysis as one self-contained
HTML file -- CSS, Cytoscape.js and the app script inlined, the analysis JSON
embedded -- so it opens offline from disk and makes no network requests (a
Content-Security-Policy with `connect-src 'none'` enforces that).
"""

from __future__ import annotations

import html
import importlib.resources
import json
from pathlib import Path

from legacy2mcp.analysis.model import Analysis

_PKG = "legacy2mcp.analysis.report"


def _asset(*parts: str) -> str:
    return importlib.resources.files(_PKG).joinpath(*parts).read_text(encoding="utf-8")


def _script_safe_json(data: object) -> str:
    """JSON that can't terminate its <script> element or open an HTML comment."""
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _script_safe(js: str) -> str:
    return js.replace("</script", "<\\/script")


def render_html(analysis: Analysis) -> str:
    version = str(analysis.meta.get("legacy2mcp_version", ""))
    replacements = {
        "__TITLE__": html.escape(f"{analysis.project} — legacy2mcp report"),
        "__VERSION__": html.escape(version),
        "__CSS__": _asset("assets", "report.css"),
        "__DATA__": _script_safe_json(analysis.to_dict()),
        "__CYTOSCAPE__": _script_safe(_asset("vendor", "cytoscape.min.js")),
        "__APP_JS__": _script_safe(_asset("assets", "report.js")),
    }
    out = _asset("assets", "template.html")
    # Replace data last so a "__CSS__"-like string inside the analysis can't be expanded.
    for key in ("__TITLE__", "__VERSION__", "__CSS__", "__CYTOSCAPE__", "__APP_JS__", "__DATA__"):
        out = out.replace(key, replacements[key], 1)
    return out


def write_report(analysis: Analysis, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(analysis), encoding="utf-8")
    return out
