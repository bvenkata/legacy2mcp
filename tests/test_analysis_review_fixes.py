"""Regression tests for issues found in the v0.6.0 code review."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from legacy2mcp import cli
from legacy2mcp.analysis.ai import enrich
from legacy2mcp.analysis.ai.providers import ProviderError, Usage
from legacy2mcp.analysis.generate import build_config
from legacy2mcp.analysis.pipeline import run_analysis
from legacy2mcp.analysis.risk.rules import is_auth_middleware

FIXTURES = Path(__file__).parent / "fixtures" / "analyze"


def test_double_quoted_secrets_in_snippets_are_redacted(tmp_path):
    # json.dumps escapes double quotes, which hid them from the
    # secret_assignment pattern when redaction ran on the encoded payload.
    (tmp_path / "app.py").write_text(
        'from flask import Flask\napp = Flask(__name__)\n\n@app.get("/x")\ndef x():\n'
        '    SECRET_KEY = "supersecretvalue123"\n    config = {"password": "hunter22x"}\n'
        '    return SECRET_KEY\n')
    a = run_analysis(tmp_path)
    plan = enrich.plan_enrichment(a, "mock", "mock-1", snippets=True, root=tmp_path)
    sent = plan.requests[0].user
    assert "SECRET_KEY" in json.loads(sent)["flows"][0]["handler_source"]
    assert "supersecretvalue123" not in sent and "hunter22x" not in sent
    assert plan.redactions >= 2


@pytest.mark.parametrize("name,expected", [
    ("src/middleware/auth.requireAuth", True),
    ("src/middleware/auth.default", True),
    ("rest_framework.permissions.IsAuthenticated", True),
    ("flask_login.login_required", True),
    ("rest_framework.permissions.AllowAny", False),
    ("django.contrib.auth.decorators.login_not_required", False),
    ("django.views.decorators.csrf.csrf_exempt", False),
    ("myapp.auth.cache_page", False),  # the module name says nothing about the decorator
    ("myapp.sessions.timed", False),
])
def test_auth_middleware_uses_the_decorator_name(name, expected):
    assert is_auth_middleware(name) is expected


def test_drf_allow_any_write_is_not_treated_as_authenticated(tmp_path):
    (tmp_path / "views.py").write_text(
        "from rest_framework.decorators import api_view, permission_classes\n"
        "from rest_framework.permissions import AllowAny, IsAuthenticated\n\n"
        "@api_view(['POST'])\n@permission_classes([AllowAny])\ndef open_view(request):\n    return 1\n\n"
        "@api_view(['POST'])\n@permission_classes([IsAuthenticated])\ndef closed_view(request):\n    return 1\n")
    (tmp_path / "urls.py").write_text(
        "from django.urls import path\nfrom . import views\n\n"
        "urlpatterns = [path('open/', views.open_view), path('closed/', views.closed_view)]\n")
    a = run_analysis(tmp_path)
    by_handler = {f.handler.split("::")[-1]: f for f in a.flows}
    assert by_handler["open_view"].authenticated is False
    assert by_handler["closed_view"].authenticated is True


class _Provider:
    name, model = "fake", "fake-1"

    def __init__(self, fail=False):
        self.fail = fail
        self.users: list[str] = []

    def complete_json(self, system, user, schema, max_tokens):
        self.users.append(user)
        if self.fail:
            raise ProviderError("hit max_tokens", Usage(4000, 1000))
        return {}, Usage(10, 10)


def _plan(n, rebuild=None):
    plan = enrich.AIPlan(provider="fake", model="fake-1")
    for _ in range(n):
        plan.requests.append(enrich.AIRequest(task="other", system="s", user="{}", schema={}, max_tokens=1000,
                                              est_input_tokens=100, rebuild=rebuild))
    return plan


def test_failed_requests_still_count_against_the_budget():
    a = run_analysis(FIXTURES / "flask_shop")
    provider = _Provider(fail=True)
    out = enrich.run_plan(_plan(3), provider, a, budget=7000)
    # First request bills 5000; the next (est. 1100) would pass 7000 only if
    # that spend were ignored.
    assert len(provider.users) == 2  # the third no longer fits after 2 x 5000 billed
    assert out.input_tokens == 8000 and out.output_tokens == 2000
    assert a.meta["ai_usage"]["input_tokens"] == out.input_tokens


def test_rebuild_runs_at_send_time():
    a = run_analysis(FIXTURES / "flask_shop")
    provider = _Provider()
    enrich.run_plan(_plan(1, rebuild=lambda an: (json.dumps({"flows": len(an.flows)}), 0)), provider, a, budget=None)
    assert provider.users == [json.dumps({"flows": len(a.flows)})]


def test_orchestration_request_sees_names_from_earlier_answers():
    a = run_analysis(FIXTURES / "flask_shop")
    plan = enrich.plan_enrichment(a, "mock", "mock-1")
    assert plan.requests[-1].task == "orchestration" and plan.requests[-1].rebuild is not None
    from legacy2mcp.analysis.ai.providers import MockProvider

    provider = MockProvider()
    enrich.run_plan(plan, provider, a, budget=None)
    orch = provider.calls[-1]
    names = [f["name"] for ag in orch["agents"] for f in ag["flows"]]
    assert names and all(n.startswith("Mock ") for n in names)


def test_selection_by_flow_id_beats_a_stale_tool_name():
    a = run_analysis(FIXTURES / "flask_shop")
    read_tool = next(t for t in a.tools if t.include)
    write_tool = next(t for t in a.tools if t.risk != "read_only" and t.exposable)
    # The write tool now carries the name the user once ticked for a read tool.
    selections = {write_tool.name: True, f"flow:{write_tool.flow_id}": False, f"flow:{read_tool.flow_id}": True}
    result = build_config(a, selections=selections)
    names = {t.name for t in result.selected}
    assert read_tool.name in names and write_tool.name not in names


def test_server_name_is_used_as_given():
    a = run_analysis(FIXTURES / "flask_shop")
    assert build_config(a, server_name="orders-mcp").config["server"]["name"] == "orders-mcp"
    assert build_config(a).config["server"]["name"].endswith("-mcp")


def test_flow_ids_never_collide(tmp_path):
    (tmp_path / "app.py").write_text(
        "from flask import Flask\napp = Flask(__name__)\n\n"
        "@app.get('/a/b')\ndef one():\n    return 1\n\n"
        "@app.get('/a-b')\ndef two():\n    return 2\n\n"
        "@app.get('/a-b-2')\ndef three():\n    return 3\n")
    ids = [f.id for f in run_analysis(tmp_path).flows]
    assert len(ids) == 3 and len(set(ids)) == 3


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs POSIX permissions and a non-root user")
def test_unreadable_file_is_counted_not_fatal(tmp_path):
    (tmp_path / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
    locked = tmp_path / "locked.py"
    locked.write_text("x = 1\n")
    locked.chmod(0)
    try:
        a = run_analysis(tmp_path)
    finally:
        locked.chmod(0o644)
    assert a.scan.excluded.get("unreadable") == 1 and a.scan.included_files == 1


def test_report_command_rejects_non_object_json(tmp_path, capsys, monkeypatch):
    bad = tmp_path / "analysis.json"
    bad.write_text("[1, 2, 3]")
    monkeypatch.setattr(sys, "argv", ["legacy2mcp", "report", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "not a readable analysis file" in capsys.readouterr().err
