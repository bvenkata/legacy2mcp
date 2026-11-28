"""Language analyzers. Each one maps a source file to a `FileResult`."""

from __future__ import annotations

from legacy2mcp.analysis.analyzers import java, javascript
from legacy2mcp.analysis.analyzers.base import Analyzer, FileResult, Mount
from legacy2mcp.analysis.analyzers.java import JavaAnalyzer
from legacy2mcp.analysis.analyzers.python import PythonAnalyzer


def default_analyzers() -> list[Analyzer]:
    analyzers: list[Analyzer] = [PythonAnalyzer()]
    if javascript.available():
        analyzers.append(javascript.JavaScriptAnalyzer())
    if java.available():
        analyzers.append(java.JavaAnalyzer())
    return analyzers


__all__ = ["Analyzer", "FileResult", "JavaAnalyzer", "Mount", "PythonAnalyzer", "default_analyzers"]
