"""Small plugin interface every language analyzer implements."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from legacy2mcp.analysis.model import EntryPoint, Module
from legacy2mcp.analysis.walk import SourceFile


@dataclass
class Mount:
    """A route table mounted under a prefix from somewhere else, e.g. Django's
    `path("api/", include("shop.urls"))` or Express's `app.use("/api", router)`.
    Resolved across modules by the pipeline once every file is parsed."""

    framework: str
    prefix: str
    target: str  # module name (Python) or module path (JS) whose routes get the prefix
    file: str
    line: int


@dataclass
class FileResult:
    module: Module
    entry_points: list[EntryPoint] = field(default_factory=list)
    mounts: list[Mount] = field(default_factory=list)
    frameworks: set[str] = field(default_factory=set)


class Analyzer(Protocol):
    languages: tuple[str, ...]

    def analyze(self, source: SourceFile, text: str) -> FileResult: ...
