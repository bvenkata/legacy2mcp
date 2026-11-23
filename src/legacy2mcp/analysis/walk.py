"""
Stage 1 of `legacy2mcp analyze`: decide which files are in scope.

Respects every `.gitignore` in the tree (each scoped to its own directory,
like git), a `.legacy2mcpignore` at the root (same syntax, for things you
track in git but don't want analyzed), `--exclude` patterns, and a small
built-in list of directories that are never application code. Every file
left out is counted under a reason, so the report can say exactly what was
and wasn't scanned.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import pathspec
except ImportError:  # pragma: no cover - exercised only without the extra installed
    pathspec = None

LANGUAGE_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}

# Never application code, whatever the ignore files say.
DEFAULT_EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env", "__pycache__",
    "site-packages", "dist", "build", ".next", ".nuxt", ".tox", ".nox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "coverage", ".idea", ".vscode", "vendor",
    "bower_components", ".turbo", ".cache", "out",
}
TEST_DIRS = {"tests", "test", "__tests__", "spec", "e2e"}
MIGRATION_DIRS = {"migrations", "alembic"}
MAX_FILE_BYTES = 1_000_000

IGNORE_FILE = ".legacy2mcpignore"


class WalkError(Exception):
    pass


@dataclass
class SourceFile:
    path: Path  # absolute
    rel: str  # repo-relative POSIX
    language: str


@dataclass
class WalkResult:
    files: list[SourceFile] = field(default_factory=list)
    excluded: dict[str, int] = field(default_factory=dict)
    ignore_files: list[str] = field(default_factory=list)


def _is_test_file(name: str) -> bool:
    stem = name.split(".", 1)[0]
    return (
        stem.startswith("test_")
        or stem.endswith("_test")
        or ".test." in name
        or ".spec." in name
        or name == "conftest.py"
    )


def _require_pathspec():
    if pathspec is None:
        raise WalkError(
            "`legacy2mcp analyze` needs the 'analyze' extra: pip install \"legacy2mcp[analyze]\""
        )


def _load_spec(path: Path):
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return pathspec.GitIgnoreSpec.from_lines(lines)


def walk(root: Path, exclude: list[str] | None = None, include_tests: bool = False) -> WalkResult:
    _require_pathspec()
    root = root.resolve()
    if not root.is_dir():
        raise WalkError(f"'{root}' is not a directory.")

    result = WalkResult()

    def bump(reason: str) -> None:
        result.excluded[reason] = result.excluded.get(reason, 0) + 1

    exclude_spec = pathspec.GitIgnoreSpec.from_lines(exclude or [])
    custom_spec = None
    if (root / IGNORE_FILE).is_file():
        custom_spec = _load_spec(root / IGNORE_FILE)
        result.ignore_files.append(IGNORE_FILE)

    # (directory relative to root as POSIX, spec) for every .gitignore seen so far
    gitignores: list[tuple[str, object]] = []

    def git_ignored(rel: str, is_dir: bool) -> bool:
        for base, spec in gitignores:
            if base and not (rel == base or rel.startswith(base + "/")):
                continue
            sub = rel[len(base) + 1:] if base else rel
            if spec.match_file(sub + ("/" if is_dir else "")):  # type: ignore[attr-defined]
                return True
        return False

    def excluded_reason(rel: str, name: str, is_dir: bool) -> str | None:
        if is_dir and name in DEFAULT_EXCLUDED_DIRS:
            return "default_excluded_dir"
        probe = rel + ("/" if is_dir else "")
        if exclude_spec.match_file(probe):
            return "exclude_flag"
        if custom_spec is not None and custom_spec.match_file(probe):
            return "legacy2mcpignore"
        if git_ignored(rel, is_dir):
            return "gitignore"
        if not include_tests and ((is_dir and name in TEST_DIRS) or (not is_dir and _is_test_file(name))):
            return "tests"
        if is_dir and name in MIGRATION_DIRS:
            return "migrations"
        return None

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        rel_dir = current.relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir

        if ".gitignore" in filenames:
            gitignores.append((rel_dir, _load_spec(current / ".gitignore")))
            result.ignore_files.append(f"{rel_dir}/.gitignore" if rel_dir else ".gitignore")

        kept_dirs = []
        for d in sorted(dirnames):
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if (current / d).is_symlink():
                bump("symlink")
                continue
            reason = excluded_reason(rel, d, is_dir=True)
            if reason:
                bump(reason)
            else:
                kept_dirs.append(d)
        dirnames[:] = kept_dirs  # prune in place so os.walk skips excluded trees

        for f in sorted(filenames):
            language = LANGUAGE_BY_EXT.get(Path(f).suffix.lower())
            if language is None:
                continue  # not source code; not counted as "excluded"
            rel = f"{rel_dir}/{f}" if rel_dir else f
            full = current / f
            reason = excluded_reason(rel, f, is_dir=False)
            if reason is None and full.is_symlink():
                reason = "symlink"
            if reason is None and f.endswith(".d.ts"):
                reason = "type_declarations"
            if reason is None and full.stat().st_size > MAX_FILE_BYTES:
                reason = "too_large"
            if reason:
                bump(reason)
                continue
            result.files.append(SourceFile(path=full, rel=rel, language=language))

    result.files.sort(key=lambda s: s.rel)
    result.ignore_files.sort()
    return result
