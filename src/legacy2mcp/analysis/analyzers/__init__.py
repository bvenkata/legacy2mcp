"""Language analyzers. Each one maps a source file to a `FileResult`."""

from __future__ import annotations

from legacy2mcp.analysis.analyzers import javascript
from legacy2mcp.analysis.analyzers.base import Analyzer, FileResult, Mount
from legacy2mcp.analysis.analyzers.python import PythonAnalyzer


def default_analyzers() -> list[Analyzer]:
    analyzers: list[Analyzer] = [PythonAnalyzer()]
    if javascript.available():
        analyzers.append(javascript.JavaScriptAnalyzer())
    return analyzers


__all__ = ["Analyzer", "FileResult", "Mount", "PythonAnalyzer", "default_analyzers"]
