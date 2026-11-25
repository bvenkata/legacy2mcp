from __future__ import annotations

from pathlib import Path

from legacy2mcp.analysis.ai import enrich, providers
from legacy2mcp.analysis.model import Analysis
from legacy2mcp.analysis.pipeline import run_analysis

FIXTURES = Path(__file__).parent / "fixtures" / "analyze"


def test_agents_by_domain_with_approvals():
    a = run_analysis(FIXTURES / "express_store")
    o = a.orchestration
    assert o.coordinator is not None and o.coordinator.tool_flows == []
    ids = {ag.id: ag for ag in o.agents}
    assert set(ids) == {"orders-agent", "health-agent", "auth-agent", "operations-agent"}
    assert ids["operations-agent"].tool_flows == [] and len(ids["operations-agent"].flows) == 3
    levels = {ap.flow_id: ap.level for ap in o.approvals}
    assert levels == {
        "delete-api-orders-id": "human", "post-api-orders-id-pay": "human",
        "post-api-auth-login": "human", "post-api-orders": "policy",
    }
    # every write tool exposed to an agent has an approval; read-only ones don't
    tools = {t.flow_id: t for t in a.tools}
    for ag in o.agents:
        for fid in ag.tool_flows:
            assert (fid in levels) == (tools[fid].risk != "read_only")


def test_multimodal_needs_evidence(tmp_path):
    a = run_analysis(FIXTURES / "django_clinic")
    (voice,) = [m for m in a.multimodal if m.kind == "voice"]
    assert set(voice.flows) == {"get-api-appointments", "get-api-appointments-pk"} and voice.evidence
    (tmp_path / "app.py").write_text(
        "from flask import Flask, request\napp = Flask(__name__)\n\n"
        "@app.post('/receipts/upload')\ndef upload_receipt():\n    f = request.form['receipt_pdf']\n")
    kinds = {m.kind for m in run_analysis(tmp_path).multimodal}
    assert "document" in kinds
    (tmp_path / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n\n@app.get('/ping')\ndef ping():\n    return 'ok'\n")
    assert run_analysis(tmp_path).multimodal == []


def test_roadmap_phases_partition_exposable_tools():
    a = run_analysis(FIXTURES / "flask_shop")
    phases = a.roadmap.phases
    assert [p.id for p in phases] == ["phase-1", "phase-2", "phase-3"]
    assigned = [fid for p in phases for fid in p.tool_flows]
    assert sorted(assigned) == sorted(t.flow_id for t in a.tools if t.exposable)
    risk_of = {t.flow_id: t.risk for t in a.tools}
    assert all(risk_of[f] == "read_only" for f in phases[0].tool_flows)
    assert all(risk_of[f] == "high_risk" for f in phases[2].tool_flows)
    assert any("authentication" in x for x in phases[1].prerequisites)  # flask_shop writes have no auth
    assert phases[0].effort in "SML" and phases[2].effort_days >= phases[0].effort_days


def test_orchestration_round_trips_and_ai_merge_is_contained():
    a = run_analysis(FIXTURES / "express_store")
    assert Analysis.from_dict(a.to_dict()).to_dict() == a.to_dict()
    plan = enrich.plan_enrichment(a, "mock", "mock-1")
    assert [r.task for r in plan.requests] == ["name_flows", "orchestration"]
    enrich.run_plan(plan, providers.MockProvider(), a, budget=None)
    assert a.orchestration.coordinator.name == "Mock coordinator"
    assert a.orchestration.notes == "Mock orchestration notes."
    n_before = len(a.multimodal)
    enrich.apply_orchestration_results(a, {"agents": [{"id": "nope", "name": "x", "description": "y"}],
                                           "multimodal": [{"kind": "image", "title": "Photos", "description": "d", "flows": ["invented"]},
                                                          {"kind": "document", "title": "Order PDFs", "description": "d", "flows": ["post-api-orders"]},
                                                          {"kind": "hologram", "title": "x", "description": "", "flows": ["post-api-orders"]}],
                                           "notes": ""})
    added = a.multimodal[n_before:]
    assert [(m.kind, m.title, m.source) for m in added] == [("document", "Order PDFs", "ai")]


def test_many_path_domains_fall_back_to_modules(tmp_path):
    pkg = tmp_path / "app"
    for blueprint, routes in {"auth": ["login", "logout", "register", "reset"], "main": ["explore", "search", "users", "user"],
                              "api": ["tokens", "followers", "following"]}.items():
        (pkg / blueprint).mkdir(parents=True)
        body = "from flask import Blueprint\nbp = Blueprint('x', __name__)\n" + "".join(
            f"\n@bp.get('/{r}')\ndef {r}_view():\n    return ''\n" for r in routes)
        (pkg / blueprint / "routes.py").write_text(body)
    agents = run_analysis(tmp_path).orchestration.agents
    assert sorted(a.domain for a in agents) == ["api", "auth", "main"]
