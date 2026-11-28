from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

import legacy2mcp.cli as cli_module
from legacy2mcp.cli import _cmd_discover
from legacy2mcp.discovery.contract import load_contract


def _args(**overrides) -> argparse.Namespace:
    defaults = dict(
        postman_file=None,
        har_file=None,
        openapi_file=None,
        queue_config=None,
        webpage_url=None,
        html_file=None,
        stored_procs=False,
        db_dialect=None,
        db_dsn_env=None,
        db_schema=None,
        redact_header=None,
        max_pages=25,
        max_depth=2,
        out="./contract.yaml",
        no_chat=False,
        write_config="./config.yaml",
        no_write_config=False,
        chat_out="./chat.html",
        host="127.0.0.1",
        port=0,  # ephemeral -- avoids port clashes between test runs
        no_open=True,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _write_queue_config(path) -> None:
    path.write_text(
        "queues:\n"
        "  - name: orders.created\n"
        "    direction: publish\n"
        "    description: Published when an order is created.\n"
    )


def test_discover_skips_the_demo_when_not_interactive(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    served = []
    monkeypatch.setattr(cli_module.webui, "serve_until_interrupted", lambda server: served.append(server))

    contract_path = tmp_path / "contract.yaml"
    _cmd_discover(_args(queue_config=str(_write_queue_config_file(tmp_path)), out=str(contract_path)))

    assert contract_path.exists()
    assert served == []


def _write_queue_config_file(tmp_path):
    path = tmp_path / "queues.yaml"
    _write_queue_config(path)
    return path


def test_discover_auto_launches_the_demo_for_an_invocable_contract(tmp_path, monkeypatch):
    html = '<html><body><form action="/search" method="GET"><input name="q"></form></body></html>'

    class _FakeResponse:
        status_code = 200
        text = html
        headers: dict = {}

    monkeypatch.setattr("requests.get", lambda url, timeout: _FakeResponse())
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    served = []
    monkeypatch.setattr(cli_module.webui, "serve_until_interrupted", lambda server: served.append(server))

    contract_path = tmp_path / "contract.yaml"
    config_path = tmp_path / "config.yaml"
    chat_path = tmp_path / "chat.html"

    _cmd_discover(
        _args(
            webpage_url="https://example.com/",
            out=str(contract_path),
            write_config=str(config_path),
            chat_out=str(chat_path),
        )
    )

    assert len(served) == 1
    assert config_path.exists()
    assert chat_path.exists()
    reloaded = cli_module.load_config(str(config_path))
    assert reloaded.adapters[0].type == "rest"


def test_discover_skips_the_demo_cleanly_for_a_non_invocable_contract(tmp_path, monkeypatch):
    # A queue-only contract has no runtime adapter at all -- the demo
    # step must skip with a clear message, not crash the whole command.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    served = []
    monkeypatch.setattr(cli_module.webui, "serve_until_interrupted", lambda server: served.append(server))

    contract_path = tmp_path / "contract.yaml"
    queue_path = _write_queue_config_file(tmp_path)

    _cmd_discover(_args(queue_config=str(queue_path), out=str(contract_path)))

    assert contract_path.exists()
    assert served == []


def test_discover_no_chat_flag_suppresses_the_demo(tmp_path, monkeypatch):
    html = '<html><body><form action="/search" method="GET"><input name="q"></form></body></html>'

    class _FakeResponse:
        status_code = 200
        text = html
        headers: dict = {}

    monkeypatch.setattr("requests.get", lambda url, timeout: _FakeResponse())
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    served = []
    monkeypatch.setattr(cli_module.webui, "serve_until_interrupted", lambda server: served.append(server))

    contract_path = tmp_path / "contract.yaml"
    _cmd_discover(_args(webpage_url="https://example.com/", out=str(contract_path), no_chat=True))

    assert served == []


def test_discover_prints_a_client_connection_snippet(tmp_path, monkeypatch, capsys):
    html = '<html><body><form action="/search" method="GET"><input name="q"></form></body></html>'

    class _FakeResponse:
        status_code = 200
        text = html
        headers: dict = {}

    monkeypatch.setattr("requests.get", lambda url, timeout: _FakeResponse())
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(cli_module.webui, "serve_until_interrupted", lambda server: None)

    contract_path = tmp_path / "contract.yaml"
    config_path = tmp_path / "config.yaml"

    _cmd_discover(
        _args(
            webpage_url="https://example.com/",
            out=str(contract_path),
            write_config=str(config_path),
            chat_out=str(tmp_path / "chat.html"),
        )
    )

    err = capsys.readouterr().err
    assert "mcpServers" in err
    assert str(config_path.resolve()) in err


SAVED_PAGE = Path(__file__).parent / "fixtures" / "webpage" / "saved_page.html"


def test_discover_html_file_writes_a_contract_without_fetching(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("--html-file must not make a request")

    monkeypatch.setattr("requests.get", no_network)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    contract_path = tmp_path / "contract.yaml"
    _cmd_discover(
        _args(
            html_file=str(SAVED_PAGE),
            max_pages=None,
            max_depth=None,
            out=str(contract_path),
            no_chat=True,
            write_config=str(tmp_path / "config.yaml"),
        )
    )

    contract = load_contract(str(contract_path))
    assert contract.source_label == str(SAVED_PAGE)
    assert [op.name for op in contract.operations] == ["search_orders", "add_note", "cancel_order"]
    assert all(op.confidence == "low" and op.needs_review for op in contract.operations)


@pytest.mark.parametrize("bound", [{"max_pages": 5}, {"max_depth": 1}])
def test_discover_html_file_refuses_crawl_bounds(tmp_path, capsys, bound):
    contract_path = tmp_path / "contract.yaml"
    args = _args(html_file=str(SAVED_PAGE), max_pages=None, max_depth=None, out=str(contract_path), no_chat=True)
    for key, value in bound.items():
        setattr(args, key, value)

    with pytest.raises(SystemExit) as excinfo:
        _cmd_discover(args)

    assert excinfo.value.code == 1
    assert "--html-file reads a single saved page" in capsys.readouterr().err
    assert not contract_path.exists()


def test_discover_webpage_url_without_bounds_uses_the_crawl_defaults(tmp_path, monkeypatch):
    calls = {}

    def fake_contract_from_webpage(url, **bounds):
        calls.update(bounds)
        raise cli_module.ContractError("stop here")

    monkeypatch.setattr(cli_module.webpage, "contract_from_webpage", fake_contract_from_webpage)

    with pytest.raises(SystemExit):
        _cmd_discover(_args(webpage_url="https://example.com/", max_pages=None, max_depth=None, no_chat=True))

    assert calls == {}  # contract_from_webpage's own defaults (25 pages, depth 2) apply


def test_discover_html_file_and_webpage_url_are_mutually_exclusive(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["legacy2mcp", "discover", "--html-file", "page.html", "--webpage-url", "https://example.com/"]
    )

    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()

    assert excinfo.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
