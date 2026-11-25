from __future__ import annotations

from pathlib import Path

from legacy2mcp.analysis.walk import walk


def _touch(root: Path, rel: str, text: str = "x = 1\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _rels(result) -> list[str]:
    return [f.rel for f in result.files]


def test_detects_languages_and_skips_non_source(tmp_path):
    _touch(tmp_path, "app.py")
    _touch(tmp_path, "web/index.ts")
    _touch(tmp_path, "web/util.mjs")
    _touch(tmp_path, "README.md")
    result = walk(tmp_path)
    assert {(f.rel, f.language) for f in result.files} == {
        ("app.py", "python"), ("web/index.ts", "typescript"), ("web/util.mjs", "javascript"),
    }
    assert result.excluded == {}


def test_respects_nested_gitignore_scoped_to_its_directory(tmp_path):
    _touch(tmp_path, ".gitignore", "*.gen.py\n")
    _touch(tmp_path, "pkg/.gitignore", "local_only.py\n")
    _touch(tmp_path, "a.gen.py")
    _touch(tmp_path, "pkg/local_only.py")
    _touch(tmp_path, "local_only.py")  # not ignored: pkg/.gitignore doesn't apply at the root
    _touch(tmp_path, "pkg/keep.py")
    result = walk(tmp_path)
    assert _rels(result) == ["local_only.py", "pkg/keep.py"]
    assert result.excluded == {"gitignore": 2}
    assert result.ignore_files == [".gitignore", "pkg/.gitignore"]


def test_legacy2mcpignore_and_exclude_flag(tmp_path):
    _touch(tmp_path, ".legacy2mcpignore", "scripts/\n")
    _touch(tmp_path, "scripts/one_off.py")
    _touch(tmp_path, "vendored/lib.py")
    _touch(tmp_path, "app.py")
    result = walk(tmp_path, exclude=["vendored/"])
    assert _rels(result) == ["app.py"]
    assert result.excluded == {"legacy2mcpignore": 1, "exclude_flag": 1}


def test_default_dirs_tests_and_migrations(tmp_path):
    _touch(tmp_path, "node_modules/x/index.js")
    _touch(tmp_path, ".venv/lib/site.py")
    _touch(tmp_path, "tests/test_app.py")
    _touch(tmp_path, "app/test_helpers.py")
    _touch(tmp_path, "web/button.test.tsx")
    _touch(tmp_path, "app/migrations/0001.py")
    _touch(tmp_path, "app/views.py")
    _touch(tmp_path, "types/global.d.ts")
    result = walk(tmp_path)
    assert _rels(result) == ["app/views.py"]
    assert result.excluded == {
        "default_excluded_dir": 2, "tests": 3, "migrations": 1, "type_declarations": 1,
    }

    with_tests = walk(tmp_path, include_tests=True)
    assert "tests/test_app.py" in _rels(with_tests)
    assert "web/button.test.tsx" in _rels(with_tests)


def test_skips_oversized_files(tmp_path):
    _touch(tmp_path, "huge.py", "x = 1\n" * 200_000)
    result = walk(tmp_path)
    assert result.files == []
    assert result.excluded == {"too_large": 1}
