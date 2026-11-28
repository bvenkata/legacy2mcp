from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from legacy2mcp import cli
from legacy2mcp.cli import _cmd_init
from legacy2mcp.config import load_config
from legacy2mcp.discovery.llm import DEFAULT_BASE_URL, DEFAULT_MODEL

FASTAPI_APP = '''
from fastapi import FastAPI

app = FastAPI()


@app.get("/widgets/{widget_id}")
def get_widget(widget_id: int):
    """Fetch a widget."""
    return {}
'''


def _args(**overrides) -> argparse.Namespace:
    defaults = dict(
        wsdl_url=None,
        rest_url=None,
        webpage_url=None,
        html_file=None,
        max_pages=25,
        max_depth=2,
        github_url=None,
        local_path=None,
        from_contract=None,
        github_token_env=None,
        llm_api_key_env=None,
        llm_base_url=DEFAULT_BASE_URL,
        llm_model=DEFAULT_MODEL,
        scan_only=True,
        out="./config.yaml",
        html_out=None,
        no_html_report=True,
        no_chat=True,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_init_from_local_path_writes_loadable_config(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text(FASTAPI_APP)
    out_path = tmp_path / "config.yaml"

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    _cmd_init(_args(local_path=str(repo_dir), out=str(out_path)))

    assert out_path.exists()
    config = load_config(str(out_path))
    assert config.adapters[0].type == "rest"
    rest_settings = config.adapters[0].rest_settings()
    assert rest_settings.endpoints[0].name == "get_widget"


def test_init_writes_html_report_alongside_config(tmp_path, monkeypatch):
    out_path = tmp_path / "config.yaml"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    _cmd_init(_args(wsdl_url="https://svc.example.com/Legacy?wsdl", out=str(out_path), no_html_report=False))

    html_path = tmp_path / "config.html"
    assert html_path.exists()
    assert "https://svc.example.com/Legacy?wsdl" in html_path.read_text()


def test_init_opens_chat_when_interactive_and_config_is_runnable(tmp_path, monkeypatch):
    # REST is the one adapter whose discover_tools() never touches the
    # network (the endpoint list comes straight from config), so this is
    # the reliable, offline-safe path for exercising the "it connected
    # successfully" branch -- SOAP/DB would need a live WSDL/DB to reach it.
    import legacy2mcp.cli as cli_module
    from legacy2mcp.discovery.scan import RouteFinding, RouteParam

    def fake_discover(base_url, timeout=10.0):
        return [RouteFinding(name="get_widget", method="GET", path="/widgets/{id}", description="", params=[RouteParam(name="id", location="path", type="integer")])]

    monkeypatch.setattr(cli_module.openapi, "discover", fake_discover)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    out_path = tmp_path / "config.yaml"
    opened_urls = []
    served = []
    monkeypatch.setattr(cli_module.webui, "open_browser", lambda url: opened_urls.append(url))
    monkeypatch.setattr(cli_module.webui, "serve_until_interrupted", lambda server: served.append(server))

    _cmd_init(_args(rest_url="https://api.example.com", out=str(out_path), no_chat=False))

    assert len(opened_urls) == 1
    assert opened_urls[0].startswith("http://127.0.0.1:")
    assert len(served) == 1


def test_init_skips_chat_when_config_needs_review(tmp_path, monkeypatch):
    # A GitHub/local-path scan with no detected routes writes an empty REST
    # scaffold with a REPLACE_ME_BASE_URL placeholder -- not runnable as-is.
    import legacy2mcp.cli as cli_module

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("# no routes here\n")
    out_path = tmp_path / "config.yaml"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    opened_urls = []
    monkeypatch.setattr(cli_module.webui, "open_browser", lambda url: opened_urls.append(url))
    monkeypatch.setattr(
        cli_module.webui, "serve_until_interrupted", lambda server: pytest.fail("should not start the chat server")
    )

    _cmd_init(_args(local_path=str(repo_dir), out=str(out_path), no_chat=False))

    assert opened_urls == []


def test_init_requires_a_source_when_noninteractive(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    import pytest

    with pytest.raises(SystemExit):
        _cmd_init(_args(out=str(tmp_path / "config.yaml")))


def test_init_from_wsdl_url_needs_no_llm_or_network(tmp_path, monkeypatch):
    out_path = tmp_path / "config.yaml"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    _cmd_init(_args(wsdl_url="https://svc.example.com/Legacy?wsdl", out=str(out_path)))


def test_init_from_webpage_url_crawls_and_builds_a_runnable_config(tmp_path, monkeypatch):
    html = '<html><body><form action="/search" method="GET"><input name="q"></form></body></html>'

    class _FakeResponse:
        status_code = 200
        text = html
        headers: dict = {}

    monkeypatch.setattr("requests.get", lambda url, timeout: _FakeResponse())
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    out_path = tmp_path / "config.yaml"
    _cmd_init(_args(webpage_url="https://example.com/", out=str(out_path)))

    reloaded = load_config(str(out_path))
    assert reloaded.adapters[0].type == "rest"
    # discovery/webpage.py's source_label is the live URL -- classify.py
    # derives base_url from it, same as the --from-contract path does.
    assert reloaded.adapters[0].config["base_url"] == "https://example.com"


def test_init_rejects_an_html_out_path_before_writing_anything(tmp_path, monkeypatch):
    # Regression: --out must always be a loadable YAML config (run/inspect/
    # demo --config all read it as one) -- an .html path here used to
    # silently corrupt the file, because the auto-generated report's
    # default name is also *.html and collided with (and overwrote) it.
    out_path = tmp_path / "contract.html"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with pytest.raises(SystemExit):
        _cmd_init(_args(wsdl_url="https://svc.example.com/Legacy?wsdl", out=str(out_path)))

    assert not out_path.exists()


def test_init_skips_the_html_report_if_it_would_overwrite_out(tmp_path, monkeypatch):
    # Defensive even when --out itself is fine: an explicit --html-out that
    # happens to match --out must never clobber the config we just wrote.
    out_path = tmp_path / "config.yaml"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    _cmd_init(
        _args(
            wsdl_url="https://svc.example.com/Legacy?wsdl",
            out=str(out_path),
            html_out=str(out_path),
            no_html_report=False,
        )
    )

    reloaded = load_config(str(out_path))
    assert reloaded.adapters[0].type == "soap"

    config = load_config(str(out_path))
    assert config.adapters[0].type == "soap"
    assert config.adapters[0].soap_settings().wsdl_url == "https://svc.example.com/Legacy?wsdl"


def test_init_from_rest_url_uses_openapi_discovery(tmp_path, monkeypatch):
    import legacy2mcp.cli as cli_module
    from legacy2mcp.discovery.scan import RouteFinding, RouteParam

    def fake_discover(base_url, timeout=10.0):
        return [
            RouteFinding(
                name="get_widget",
                method="GET",
                path="/widgets/{id}",
                description="",
                params=[RouteParam(name="id", location="path", type="integer")],
            )
        ]

    monkeypatch.setattr(cli_module.openapi, "discover", fake_discover)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    out_path = tmp_path / "config.yaml"
    _cmd_init(_args(rest_url="https://api.example.com", out=str(out_path)))

    config = load_config(str(out_path))
    assert config.adapters[0].type == "rest"
    rest_settings = config.adapters[0].rest_settings()
    assert rest_settings.base_url == "https://api.example.com"
    assert rest_settings.endpoints[0].name == "get_widget"


def test_init_from_contract_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    contract_path = tmp_path / "contract.yaml"
    contract_path.write_text(
        "source_label: test\n"
        "generated_at: '2026-01-01T00:00:00+00:00'\n"
        "operations:\n"
        "  - name: get_order\n"
        "    source_kind: rest\n"
        "    method: GET\n"
        "    path: /orders/{id}\n"
        "    confidence: high\n"
        "    error_codes: ['404']\n"
        "    fields:\n"
        "      - name: id\n"
        "        location: path\n"
        "        type: integer\n"
        "        required: true\n"
    )

    out_path = tmp_path / "config.yaml"
    _cmd_init(_args(from_contract=str(contract_path), out=str(out_path)))

    config = load_config(str(out_path))
    assert config.adapters[0].type == "rest"
    rest_settings = config.adapters[0].rest_settings()
    endpoint = rest_settings.endpoints[0]
    assert endpoint.name == "get_order"
    assert endpoint.confidence == "high"
    assert endpoint.error_codes == ["404"]
    assert endpoint.write is False


SAVED_PAGE = Path(__file__).parent / "fixtures" / "webpage" / "saved_page.html"


def test_init_from_html_file_leaves_base_url_for_the_user(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("--html-file must not make a request")

    monkeypatch.setattr("requests.get", no_network)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    out_path = tmp_path / "config.yaml"
    _cmd_init(_args(html_file=str(SAVED_PAGE), max_pages=None, max_depth=None, out=str(out_path)))

    reloaded = load_config(str(out_path))
    settings = reloaded.adapters[0].config
    assert reloaded.adapters[0].type == "rest"
    # A saved file says nothing about where the page was served from: no
    # file:// URL and no local path may stand in for the API's host.
    assert settings["base_url"] == "REPLACE_ME_BASE_URL"
    paths = sorted(endpoint["path"] for endpoint in settings["endpoints"])
    assert paths == ["/", "/orders/search", "/orders/{orderId}/cancel"]
    assert not any("file:" in p or str(SAVED_PAGE.parent) in p for p in paths)
    assert "REPLACE_ME_BASE_URL" in out_path.read_text(encoding="utf-8")


def test_init_from_html_file_refuses_crawl_bounds(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    out_path = tmp_path / "config.yaml"

    with pytest.raises(SystemExit) as excinfo:
        _cmd_init(_args(html_file=str(SAVED_PAGE), max_pages=3, max_depth=None, out=str(out_path)))

    assert excinfo.value.code == 1
    assert "--max-pages/--max-depth" in capsys.readouterr().err
    assert not out_path.exists()


def test_init_html_file_shares_the_source_group(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["legacy2mcp", "init", "--html-file", "page.html", "--rest-url", "https://x/"])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
