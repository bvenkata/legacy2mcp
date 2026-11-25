from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from legacy2mcp import cli
from legacy2mcp.analysis.ai import enrich, providers
from legacy2mcp.analysis.ai.redact import redact
from legacy2mcp.analysis.pipeline import run_analysis

FIXTURES = Path(__file__).parent / "fixtures" / "analyze"


# ------------------------------------------------------------------ redaction
@pytest.mark.parametrize("secret", [
    "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123",
    "sk_live_51HabcdefghijklmnopQRST",
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "xoxb-1234567890-abcdefghij",
    "AIzaSyA-abcdefghijklmnopqrstuvwxyz012345",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "Bearer abcdefghijklmnopqrstuvwxyz",
])
def test_redacts_token_formats(secret):
    text, count = redact(f"value: {secret} end")
    assert secret not in text and count == 1 and "[REDACTED]" in text


def test_redacts_assignments_connection_strings_and_keys():
    src = ('password = "hunter2hunter2"\nAPI_KEY: \'abcdef123456\'\n'
           'db = "postgres://admin:s3cretpw@db.internal:5432/app"\n'
           '-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----')
    text, count = redact(src)
    assert "hunter2hunter2" not in text and "abcdef123456" not in text and "s3cretpw" not in text
    assert "postgres://[REDACTED]@db.internal:5432/app" in text
    assert "MIIabc" not in text and count == 4


def test_leaves_ordinary_code_alone():
    src = 'def charge_card(order, token):\n    return stripe.Charge.create(amount=1)\n'
    assert redact(src) == (src, 0)


# ------------------------------------------------------------------ planning
def test_plan_sends_structure_not_source():
    a = run_analysis(FIXTURES / "flask_shop")
    plan = enrich.plan_enrichment(a, "mock", "mock-1")
    assert [r.task for r in plan.requests] == ["name_flows", "orchestration"]
    payload = json.loads(plan.requests[0].user)
    assert payload["task"] == "name_flows" and len(payload["flows"]) == len(a.flows)
    assert all("handler_source" not in f for f in payload["flows"])
    assert all("stripe.Charge.create(amount" not in r.user for r in plan.requests)
    assert plan.est_input_tokens > 0 and plan.est_cost_usd is None


def test_snippets_are_opt_in_and_redacted(tmp_path):
    (tmp_path / "app.py").write_text(
        'from flask import Flask\napp = Flask(__name__)\n\n@app.get("/x")\ndef x():\n'
        '    key = "sk_live_51HabcdefghijklmnopQRST"\n    return key\n')
    a = run_analysis(tmp_path)
    plan = enrich.plan_enrichment(a, "mock", "mock-1", snippets=True, root=tmp_path)
    flow = json.loads(plan.requests[0].user)["flows"][0]
    assert "def x():" in flow["handler_source"]
    assert "sk_live_" not in plan.requests[0].user and plan.redactions >= 1


def test_batches_and_cost_estimate():
    a = run_analysis(FIXTURES / "express_store")
    a.flows = a.flows * 3  # 30 flows -> 3 requests of <= 12
    plan = enrich.plan_enrichment(a, "anthropic", "claude-opus-5-5")
    assert [r.task for r in plan.requests].count("name_flows") == 3
    assert plan.est_cost_usd and plan.est_cost_usd > 0


# ------------------------------------------------------------------ running
def test_mock_run_renames_but_never_changes_risk_or_selection():
    a = run_analysis(FIXTURES / "flask_shop")
    before = {f.id: f.risk for f in a.flows}
    selection = {t.flow_id: (t.safe, t.include) for t in a.tools}
    provider = providers.MockProvider()
    plan = enrich.plan_enrichment(a, provider.name, provider.model)
    result = enrich.run_plan(plan, provider, a, budget=None)
    assert result.requests_sent == 2 and not result.errors
    assert {f.id: f.risk for f in a.flows} == before
    assert {t.flow_id: (t.safe, t.include) for t in a.tools} == selection
    checkout = next(f for f in a.flows if f.id == "post-api-orders-order-id-checkout")
    assert checkout.name.startswith("Mock POST")
    assert any(r.text == "AI review note: Mock note." and r.level == "info" for r in checkout.reasons)
    assert all(t.source == "ai" and t.name.startswith("mock_") for t in a.tools)
    assert a.meta["ai"] is True and a.meta["ai_usage"]["requests"] == 2


def test_budget_skips_requests():
    a = run_analysis(FIXTURES / "flask_shop")
    plan = enrich.plan_enrichment(a, "mock", "mock-1")
    result = enrich.run_plan(plan, providers.MockProvider(), a, budget=10)
    assert result.requests_sent == 0 and result.requests_skipped == 2
    assert a.meta["ai"] is False


def test_hostile_answers_are_contained():
    a = run_analysis(FIXTURES / "flask_shop")
    first_tool = a.tools[0]
    n = enrich.apply_flow_results(a, {"flows": [
        {"id": "made-up", "name": "x", "description": "", "risk_notes": "", "tool_name": "", "tool_description": ""},
        {"id": first_tool.flow_id, "name": "N" * 500, "description": "", "risk_notes": "",
         "tool_name": "Drop Table; rm -rf /", "tool_description": ""},
        "not a dict",
    ]})
    assert n == 1
    assert first_tool.name == "drop_table_rm_rf"
    assert len(next(f for f in a.flows if f.id == first_tool.flow_id).name) == 80


class _FailingProvider:
    name, model = "x", "y"

    def complete_json(self, *a, **k):
        raise providers.ProviderError("boom")


def test_provider_failure_keeps_rules():
    a = run_analysis(FIXTURES / "flask_shop")
    names = [f.name for f in a.flows]
    result = enrich.run_plan(enrich.plan_enrichment(a, "x", "y"), _FailingProvider(), a, budget=None)
    assert result.errors == ["boom", "boom"] and [f.name for f in a.flows] == names


# ------------------------------------------------------------------ providers
def _anthropic_response(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=11, output_tokens=7))


def test_anthropic_provider_request_shape(monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    p = providers.AnthropicProvider()
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return _anthropic_response('{"ok": true}')

    p.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    data, usage = p.complete_json("sys", "user", {"type": "object"}, 500)
    assert data == {"ok": True} and (usage.input_tokens, usage.output_tokens) == (11, 7)
    assert seen["model"] == "claude-opus-5-5"
    assert seen["fallbacks"] == "default" and seen["betas"] == ["server-side-fallback-2026-07-01"]
    assert seen["output_config"]["format"] == {"type": "json_schema", "schema": {"type": "object"}}
    assert "thinking" not in seen and "temperature" not in seen


def test_anthropic_refusal_and_truncation_raise(monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    p = providers.AnthropicProvider(model="claude-haiku-5-5")
    for stop in ("refusal", "max_tokens"):
        calls = {}

        def create(stop=stop, **kwargs):
            calls.update(kwargs)
            return _anthropic_response("", stop)

        p.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
        with pytest.raises(providers.ProviderError):
            p.complete_json("s", "u", {}, 10)
        assert "fallbacks" not in calls  # Haiku has no server-side fallback


def test_openai_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    p = providers.OpenAIProvider()
    captured = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": '{"a": 1}'}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2}}

    import requests
    monkeypatch.setattr(requests, "post", lambda url, **kw: captured.update(url=url, **kw) or Resp())
    data, usage = p.complete_json("s", "u", {"type": "object"}, 100)
    assert data == {"a": 1} and usage.input_tokens == 3
    assert captured["json"]["response_format"]["json_schema"]["strict"] is True
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(providers.ProviderError, match="OPENAI_API_KEY"):
        providers.OpenAIProvider()


# ------------------------------------------------------------------ CLI
def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["legacy2mcp", *argv])
    cli.main()


def test_cli_without_key_runs_offline(monkeypatch, tmp_path, capsys):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    _run(monkeypatch, "analyze", str(FIXTURES / "flask_shop"), "--out", str(tmp_path))
    assert "AI enrichment was skipped" in capsys.readouterr().err
    assert json.loads((tmp_path / "analysis.json").read_text())["meta"]["ai"] is False


def test_cli_dry_run_writes_payload_and_sends_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(providers, "get_provider", lambda *a, **k: providers.MockProvider())
    _run(monkeypatch, "analyze", str(FIXTURES / "flask_shop"), "--provider", "anthropic", "--ai-dry-run", "--out", str(tmp_path))
    payload = json.loads((tmp_path / "ai-payload.json").read_text())
    assert payload[0]["task"] == "name_flows"
    assert "nothing was sent" in capsys.readouterr().out


def test_cli_yes_enriches_and_records_usage(monkeypatch, tmp_path):
    monkeypatch.setattr(providers, "get_provider", lambda *a, **k: providers.MockProvider())
    _run(monkeypatch, "analyze", str(FIXTURES / "flask_shop"), "--provider", "openai", "--yes", "--out", str(tmp_path))
    data = json.loads((tmp_path / "analysis.json").read_text())
    assert data["meta"]["ai"] is True and data["meta"]["ai_usage"]["provider"] == "mock"
    assert any(f["name"].startswith("Mock") for f in data["flows"])


def test_cli_non_interactive_without_yes_skips(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(providers, "get_provider", lambda *a, **k: providers.MockProvider())
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: False))
    _run(monkeypatch, "analyze", str(FIXTURES / "flask_shop"), "--provider", "openai", "--out", str(tmp_path))
    assert "re-run with --yes" in capsys.readouterr().err
