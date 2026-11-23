"""Language analyzers. Each one maps a source file to a `FileResult`."""

from __future__ import annotations

from legacy2mcp.analysis.analyzers.base import Analyzer, FileResult, Mount
from legacy2mcp.analysis.analyzers.python import PythonAnalyzer


def default_analyzers() -> list[Analyzer]:
    return [PythonAnalyzer()]


__all__ = ["Analyzer", "FileResult", "Mount", "PythonAnalyzer", "default_analyzers"]
