"""Golden-file tests: analysis.json for each fixture project must match the
checked-in copy exactly (minus the run-specific `meta` block).

After an intentional change to the analyzer, regenerate with:
    UPDATE_GOLDEN=1 pytest tests/test_analysis_golden.py
and review the diff like any other code change.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from legacy2mcp.analysis.model import Analysis
from legacy2mcp.analysis.pipeline import run_analysis

FIXTURES = Path(__file__).parent / "fixtures" / "analyze"
GOLDEN = Path(__file__).parent / "golden" / "analyze"
PROJECTS = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())


def _stable(analysis: Analysis) -> dict:
    data = analysis.to_dict()
    data.pop("meta")
    return data


@pytest.mark.parametrize("project", PROJECTS)
def test_analysis_matches_golden(project):
    actual = _stable(run_analysis(FIXTURES / project))
    golden_path = GOLDEN / f"{project}.json"
    if os.environ.get("UPDATE_GOLDEN"):
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(json.dumps(actual, indent=2) + "\n")
    assert golden_path.exists(), f"missing golden file; run with UPDATE_GOLDEN=1 to create {golden_path}"
    assert actual == json.loads(golden_path.read_text())


@pytest.mark.parametrize("project", PROJECTS)
def test_analysis_round_trips_through_json(project):
    analysis = run_analysis(FIXTURES / project)
    assert Analysis.from_dict(json.loads(analysis.to_json())).to_dict() == analysis.to_dict()


def test_meta_has_no_effect_on_content():
    analysis = run_analysis(FIXTURES / "flask_shop")
    assert set(analysis.meta) == {"legacy2mcp_version", "generated_at", "root", "ai"}
    assert analysis.meta["ai"] is False


def test_rejects_unknown_schema_version():
    with pytest.raises(ValueError, match="schema_version"):
        Analysis.from_dict({"schema_version": "999", "project": "x"})
