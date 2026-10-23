from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys
from importlib.metadata import version
from pathlib import Path

from legacy2mcp import client_snippet, webui
from legacy2mcp.config import Legacy2McpConfig, load_config
from legacy2mcp.discovery import classify, generate, har, openapi, postman, queue_contract, repo, report, scan, storedproc, webpage
from legacy2mcp.discovery.contract import ContractError, load_contract
from legacy2mcp.discovery.llm import DEFAULT_BASE_URL, DEFAULT_MODEL, OpenAiLlmClient
from legacy2mcp.discovery.repo import RepoError
from legacy2mcp.discovery.scan import ScanResult, SoapFinding
from legacy2mcp.install_skill import install_skill
from legacy2mcp.server import Legacy2McpApp


def _cmd_run(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    config = load_config(args.config)
    app = Legacy2McpApp(config)

    if config.server.transport != "stdio":
        print(
            f"Transport '{config.server.transport}' is not implemented yet in this "
            f"version of legacy2mcp; only 'stdio' is supported. See docs/roadmap.md.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        asyncio.run(app.run_stdio())
    finally:
        app.close()


def _cmd_inspect(args: argparse.Namespace) -> None:
    """Dry-run: load config, discover tools, print them as JSON. No MCP transport."""
    logging.basicConfig(level=logging.WARNING)
    config = load_config(args.config)
    app = Legacy2McpApp(config)
    try:
        tools = [
            {
                "name": tool_def.name,
                "description": tool_def.description,
                "input_schema": tool_def.input_schema,
                "metadata": tool_def.metadata,
            }
            for _, tool_def in app._tools.values()
        ]
        print(json.dumps(tools, indent=2, default=str))
    finally:
        app.close()


def _cmd_install_skill(args: argparse.Namespace) -> None:
    install_skill(args.project_root)


def _cmd_discover(args: argparse.Namespace) -> None:
    """Discover a legacy interface's real shape -- operations, inputs,
    enums, error codes, confidence -- into a human-reviewable contract.yaml,
    *before* generating a runnable server from it. See `legacy2mcp init
    --from-contract` for the next step."""
    try:
        if args.postman_file:
            contract = postman.contract_from_postman(args.postman_file)
            source_label = args.postman_file
        elif args.har_file:
            extra_headers = frozenset(h.lower() for h in (args.redact_header or []))
            contract = har.contract_from_har(args.har_file, extra_sensitive_headers=extra_headers)
            source_label = args.har_file
        elif args.openapi_file:
            spec = openapi.load_spec_from_file(args.openapi_file)
            contract = openapi.contract_from_spec(spec, args.openapi_file)
            source_label = args.openapi_file
        elif args.queue_config:
            contract = queue_contract.load_queue_contract(args.queue_config)
            source_label = args.queue_config
        elif args.webpage_url:
            contract = webpage.contract_from_webpage(
                args.webpage_url, max_pages=args.max_pages, max_depth=args.max_depth
            )
            source_label = args.webpage_url
        elif args.stored_procs:
            if not args.db_dialect:
                print("--stored-procs needs --db-dialect {postgres,mssql,oracle}.", file=sys.stderr)
                sys.exit(1)
            dsn = os.environ.get(args.db_dsn_env or "")
            if dsn is None:
                print(f"Environment variable '{args.db_dsn_env}' is not set.", file=sys.stderr)
                sys.exit(1)
            contract = storedproc.contract_from_stored_procedures(args.db_dialect, dsn, db_schema=args.db_schema)
            source_label = f"{args.db_dialect} database"
        else:
            print(
                "Pass one of --postman-file, --openapi-file, --har-file, --queue-config, "
                "--webpage-url, or --stored-procs (with --db-dialect and --db-dsn-env).",
                file=sys.stderr,
            )
            sys.exit(1)
    except ContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if Path(args.out).suffix.lower() in (".html", ".htm"):
        text = generate.render_contract_html(contract, source_label)
    else:
        text = generate.render_contract_yaml(contract, source_label)
    out_path = generate.write_config(args.out, text)

    by_confidence: dict[str, int] = {}
    needs_review_count = 0
    for op in contract.operations:
        by_confidence[op.confidence] = by_confidence.get(op.confidence, 0) + 1
        if op.needs_review:
            needs_review_count += 1
    summary = ", ".join(f"{count} {level}-confidence" for level, count in sorted(by_confidence.items())) or "none"

    print(
        f"Wrote {out_path} ({len(contract.operations)} operation(s): {summary}, "
        f"{needs_review_count} flagged for review).",
        file=sys.stderr,
    )
    print(f"Next: legacy2mcp init --from-contract {out_path} --out config.yaml", file=sys.stderr)
    print(f"  or: legacy2mcp demo --from-contract {out_path}  (try it in a chat page right away)", file=sys.stderr)

    if sys.stdin.isatty() and not args.no_chat:
        try:
            config_dict, todos = classify.build_config_from_contract(contract)
        except ContractError as exc:
            # Not every contract is runnable yet (db_proc/queue-only, say)
            # -- that's not a discover failure, just nothing to demo.
            print(f"Skipping the demo -- {exc}", file=sys.stderr)
            return

        app_config = Legacy2McpConfig.model_validate(config_dict)
        config_path = None
        if not args.no_write_config:
            config_text = generate.render_yaml(config_dict, source_label, todos)
            config_path = generate.write_config(args.write_config, config_text)
            print(f"Wrote {config_path} -- the runnable config this demo is serving.", file=sys.stderr)

        _serve_demo(app_config, args.chat_out, args.host, args.port, args.no_open, config_path)


def _cmd_demo(args: argparse.Namespace) -> None:
    """Start the same no-API-key test-chat server `init` can auto-launch,
    straight from a contract (skipping a separate `init --from-contract`
    step) or an existing config -- and save a standalone chat.html that
    points at it, for `--from-contract`/`--config` sources that weren't
    already interactive `init` run. The server has to keep running for
    that saved page to work (this command blocks until Ctrl+C) -- it's a
    thin client page with its own backend of zero, not an offline file;
    see webui.render_page()'s docstring for why (CORS, and because the
    real HTTP call has to be made by something other than the page
    itself, same as the browser's same-origin `/` route already does).

    `--from-contract` also writes a real config.yaml alongside the demo
    (unless `--no-write-config`) -- otherwise there'd be nothing on disk
    for an MCP client to actually point `legacy2mcp run --config` at once
    you're done trying it out here; `--config` already has one."""
    config_path: Path | None = None
    try:
        if args.from_contract:
            contract = load_contract(args.from_contract)
            config_dict, todos = classify.build_config_from_contract(contract)
            app_config = Legacy2McpConfig.model_validate(config_dict)
            if not args.no_write_config:
                text = generate.render_yaml(config_dict, args.from_contract, todos)
                config_path = generate.write_config(args.write_config, text)
                print(f"Wrote {config_path} -- the runnable config this demo is serving.", file=sys.stderr)
        else:
            app_config = load_config(args.config)
            config_path = Path(args.config)
    except ContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    _serve_demo(app_config, args.out, args.host, args.port, args.no_open, config_path)


def _serve_demo(
    app_config: Legacy2McpConfig,
    chat_out: str,
    host: str,
    port: int,
    no_open: bool,
    config_path: Path | None,
) -> None:
    """Shared by `_cmd_demo` and `_cmd_discover`'s auto-launch: start the
    no-API-key test-chat server for an already-resolved config, save a
    standalone chat.html pointing at it (see webui.render_page()), print
    a ready-to-paste MCP client connection snippet when there's a real
    config path to point it at, open a browser, and block until Ctrl+C."""
    app = Legacy2McpApp(app_config)
    try:
        server = webui.start_server(app, host=host, port=port)
    except OSError as exc:
        print(f"error: couldn't bind {host}:{port} ({exc}). Try a different --port.", file=sys.stderr)
        app.close()
        sys.exit(1)

    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    out_path = generate.write_config(chat_out, webui.render_page(base_url))
    print(f"Wrote {out_path} -- a standalone chat page pointing at {base_url}.", file=sys.stderr)
    print("This command has to keep running for that page to work (Ctrl+C to stop).", file=sys.stderr)

    if config_path is not None:
        print("", file=sys.stderr)
        print(client_snippet.render_client_snippets(config_path), file=sys.stderr)

    if not no_open:
        webui.open_browser(out_path.resolve().as_uri())

    try:
        webui.serve_until_interrupted(server)
    finally:
        app.close()


def _resolve_source(args: argparse.Namespace, interactive: bool) -> tuple[str, str]:
    """Return (kind, value): kind is "wsdl" / "rest" / "webpage" / "github_url" / "local_path" / "from_contract"."""
    if args.wsdl_url:
        return "wsdl", args.wsdl_url
    if args.rest_url:
        return "rest", args.rest_url
    if args.webpage_url:
        return "webpage", args.webpage_url
    if args.github_url:
        return "github_url", args.github_url
    if args.local_path:
        return "local_path", args.local_path
    if args.from_contract:
        return "from_contract", args.from_contract

    if not interactive:
        print(
            "Pass one of --wsdl-url, --rest-url, --webpage-url, --github-url, --local-path, "
            "or --from-contract.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("What do you want to point legacy2mcp at?")
    print("  1) WSDL URL (SOAP service)")
    print("  2) REST API URL (tries OpenAPI/Swagger discovery)")
    print("  3) A website with no API docs at all (crawls it for forms/content)")
    print("  4) GitHub repo URL or a local path to its source code")
    choice = input("Choice [1-4]: ").strip()

    if choice == "1":
        return "wsdl", input("WSDL URL: ").strip()
    if choice == "2":
        return "rest", input("REST API base URL: ").strip()
    if choice == "3":
        return "webpage", input("Website URL: ").strip()
    if choice == "4":
        value = input("GitHub repo URL or local path: ").strip()
        kind = "github_url" if value.startswith(("http://", "https://", "git@")) else "local_path"
        return kind, value

    print(f"Invalid choice: '{choice}'.", file=sys.stderr)
    sys.exit(1)


def _resolve_llm(args: argparse.Namespace, interactive: bool) -> OpenAiLlmClient | None:
    if args.scan_only:
        return None

    api_key = None
    llm_base_url = args.llm_base_url
    if args.llm_api_key_env:
        api_key = os.environ.get(args.llm_api_key_env)
        if api_key is None:
            print(f"Environment variable '{args.llm_api_key_env}' is not set.", file=sys.stderr)
            sys.exit(1)
    elif interactive:
        if args.llm_base_url == DEFAULT_BASE_URL:
            entered = input(f"LLM API base URL [{DEFAULT_BASE_URL}]: ").strip()
            llm_base_url = entered or DEFAULT_BASE_URL
        api_key = getpass.getpass(
            "LLM API key for AI-assisted analysis (hidden, never stored; leave blank to skip): "
        ).strip() or None

    if api_key is None:
        print("No LLM key configured -- running static-analysis-only.", file=sys.stderr)
        return None
    return OpenAiLlmClient(api_key=api_key, base_url=llm_base_url, model=args.llm_model)


def _cmd_init(args: argparse.Namespace) -> None:
    """Point legacy2mcp at a WSDL URL, a REST API URL, a website, or a
    GitHub repo / local path, and write a starter config.yaml. The
    WSDL/REST paths need no AI assist -- zeep introspects a WSDL live,
    and REST tries an OpenAPI/Swagger spec at the given URL. --webpage-url
    runs the exact same crawl `discovery/webpage.py` does for `discover
    --webpage-url`, just skipping straight to a config instead of
    stopping at a contract.yaml for review first -- every operation it
    finds still lands at confidence=low/needs_review=true in the
    generated TODOs, same as it would via discover. Only the
    GitHub/source-code path is ambiguous enough to optionally use an
    LLM, and only to refine what static AST analysis already found --
    see discovery/."""
    interactive = sys.stdin.isatty()

    if Path(args.out).suffix.lower() in (".html", ".htm"):
        # --out always has to be a loadable YAML config -- `run`/`inspect`/
        # `demo --config` all read it as one. An .html path here is almost
        # always someone reaching for `discover --out contract.html`'s
        # extension-picks-the-format behavior by mistake; that only applies
        # to `discover`, not `init`. Catch it before writing anything --
        # letting it through used to silently corrupt the file, since the
        # auto-generated report's default name is also *.html and would
        # collide with (and overwrite) this same path.
        print(
            f"error: --out must be a YAML path (e.g. config.yaml) -- '{args.out}' looks like HTML. "
            f"The human-readable report is written automatically as *.html next to --out already "
            f"(see --html-out/--no-html-report) -- you don't need to point --out at one yourself. "
            f"For a Legacy Interface Contract as HTML, see `legacy2mcp discover --out contract.html`.",
            file=sys.stderr,
        )
        sys.exit(1)

    kind, value = _resolve_source(args, interactive)

    cloned_path = None
    try:
        if kind == "wsdl":
            config, todos = classify.build_config(
                ScanResult(soap=SoapFinding(wsdl_url=value, file="user-provided WSDL URL"))
            )
            source_label = value

        elif kind == "rest":
            source_label = value
            try:
                routes = openapi.discover(value)
            except openapi.OpenApiError as exc:
                print(f"warning: {exc}", file=sys.stderr)
                routes = []
            config, todos = classify.build_config(ScanResult(routes=routes), base_url=value)

        elif kind == "webpage":
            source_label = value
            contract = webpage.contract_from_webpage(value, max_pages=args.max_pages, max_depth=args.max_depth)
            config, todos = classify.build_config_from_contract(contract)

        elif kind == "from_contract":
            source_label = value
            contract = load_contract(value)
            config, todos = classify.build_config_from_contract(contract)

        else:  # github_url / local_path
            llm = _resolve_llm(args, interactive)
            if kind == "local_path":
                source = repo.resolve_local(value)
                source_label = str(source)
            else:
                github_token_env = args.github_token_env
                if github_token_env is None and interactive:
                    entered_token = getpass.getpass(
                        "GitHub token, for private repos (hidden, never stored; leave blank for public): "
                    ).strip()
                    if entered_token:
                        os.environ["LEGACY2MCP_INIT_GITHUB_TOKEN"] = entered_token
                        github_token_env = "LEGACY2MCP_INIT_GITHUB_TOKEN"

                print(f"Cloning {value} ...", file=sys.stderr)
                cloned_path = repo.clone(value, token_env=github_token_env)
                source = cloned_path
                source_label = value

            scan_result = scan.scan_repo(source)
            config, todos = classify.build_config(scan_result, llm=llm)

        text = generate.render_yaml(config, source_label, todos)
        out_path = generate.write_config(args.out, text)

        html_path = None
        if not args.no_html_report:
            html_out = args.html_out or str(Path(args.out).with_suffix(".html"))
            if Path(html_out).resolve() == Path(args.out).resolve():
                # Guards any other way these two could end up equal (an
                # explicit --html-out matching --out, say) -- never
                # silently overwrite the config we just wrote.
                print(
                    f"warning: the HTML report's path ('{html_out}') would overwrite --out -- "
                    f"skipping it. Pass a different --html-out, or --no-html-report.",
                    file=sys.stderr,
                )
            else:
                html_text = report.render_html(config, source_label, todos)
                html_path = report.write_report(html_out, html_text)
    except (RepoError, ContractError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        if cloned_path is not None:
            repo.cleanup(cloned_path)

    adapter_type = config["adapters"][0]["type"]
    adapter_settings = config["adapters"][0]["config"]
    if adapter_type == "rest":
        tool_count = len(adapter_settings["endpoints"])
    elif adapter_type == "db":
        tool_count = len(adapter_settings["tables"])
    else:
        tool_count = 1

    print(f"Wrote {out_path} ({adapter_type} adapter, {tool_count} tool source(s) detected).", file=sys.stderr)
    if html_path is not None:
        print(f"Wrote {html_path} -- a plain-English summary of what this MCP server does.", file=sys.stderr)
    if todos:
        print(f"{len(todos)} TODO(s) left in the file -- review before running.", file=sys.stderr)
    print(f"Next: legacy2mcp inspect --config {out_path}", file=sys.stderr)
    print("", file=sys.stderr)
    print(client_snippet.render_client_snippets(out_path), file=sys.stderr)

    if interactive and not args.no_chat:
        _maybe_launch_chat(config, out_path)


def _config_is_runnable(config: dict) -> bool:
    """Whether this config can actually be connected to as-is, with no
    manual editing -- i.e. no REPLACE_ME_* placeholder, and (for the db
    adapter) its DSN env var is already set. Used to decide whether it's
    worth trying to open the test chat at all, rather than opening a
    browser tab that can only fail."""
    adapter = config["adapters"][0]
    adapter_type = adapter["type"]
    settings = adapter["config"]

    if adapter_type == "rest":
        return not str(settings.get("base_url", "")).startswith("REPLACE_ME")
    if adapter_type == "soap":
        return not str(settings.get("wsdl_url", "")).startswith("REPLACE_ME")
    if adapter_type == "db":
        dsn_env = settings.get("dsn_env")
        return bool(dsn_env and os.environ.get(dsn_env))
    return False


def _maybe_launch_chat(config: dict, out_path) -> None:
    """Open a local, no-API-key "try it" chat page (see webui.py) so you
    can immediately ask it something and see a real response, without
    needing to wire up an MCP client first. Never fatal -- config.yaml is
    already written by the time this runs, so any failure here just prints
    a note and falls back to pointing at `legacy2mcp run`."""
    if not _config_is_runnable(config):
        print(
            "Skipping the test chat -- fill in the TODOs above first, "
            f"then run `legacy2mcp run --config {out_path}`.",
            file=sys.stderr,
        )
        return

    try:
        app = Legacy2McpApp(load_config(str(out_path)))
    except Exception as exc:
        print(f"Skipping the test chat -- couldn't connect ({exc}).", file=sys.stderr)
        return

    try:
        server = webui.start_server(app)
        url = f"http://{server.server_address[0]}:{server.server_address[1]}/"
        print(f"Opening a test chat at {url} (no AI key needed -- Ctrl+C to stop).", file=sys.stderr)
        webui.open_browser(url)
        webui.serve_until_interrupted(server)
    finally:
        app.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="legacy2mcp")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {version('legacy2mcp')}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the MCP server defined by a config file.")
    run_parser.add_argument("--config", required=True, help="Path to config.yaml")
    run_parser.add_argument("--log-level", default="INFO")
    run_parser.set_defaults(func=_cmd_run)

    inspect_parser = subparsers.add_parser(
        "inspect", help="Load a config, discover tools, and print them as JSON (no server started)."
    )
    inspect_parser.add_argument("--config", required=True, help="Path to config.yaml")
    inspect_parser.set_defaults(func=_cmd_inspect)

    install_skill_parser = subparsers.add_parser(
        "install-skill",
        help=(
            "Write legacy2mcp usage instructions into this repo's AI-assistant context "
            "files (Claude Code, Cursor, AGENTS.md, GEMINI.md, GitHub Copilot) so an "
            "assistant working here already knows how to drive legacy2mcp. Safe to "
            "re-run -- it updates its own section in place."
        ),
    )
    install_skill_parser.add_argument(
        "--project-root", default=None, help="Repo root to write into (default: current directory)."
    )
    install_skill_parser.set_defaults(func=_cmd_install_skill)

    discover_parser = subparsers.add_parser(
        "discover",
        help=(
            "Discover a legacy interface's real shape (operations, inputs, enums, error "
            "codes, confidence) into a human-reviewable contract.yaml, before `init "
            "--from-contract` turns it into a runnable server. Covers formats `init` "
            "can't read directly: Postman collections, local OpenAPI/Swagger files, a "
            "database's stored-procedure catalog, declarative queue definitions, and a "
            "plain webpage's HTML forms."
        ),
    )
    discover_source_group = discover_parser.add_mutually_exclusive_group()
    discover_source_group.add_argument("--postman-file", help="A Postman Collection (v2.1 schema) JSON file.")
    discover_source_group.add_argument("--openapi-file", help="A local OpenAPI/Swagger spec file (JSON or YAML).")
    discover_source_group.add_argument(
        "--har-file",
        help=(
            "A HAR (HTTP Archive) capture -- from browser devtools' 'Export HAR', mitmproxy's "
            "built-in HAR exporter (mitmdump --set hardump=out.har), Postman's traffic capture, "
            "or anything else that produces standard HAR. Lowest confidence of any source: "
            "inferred from sampled traffic, every operation flagged for review."
        ),
    )
    discover_source_group.add_argument(
        "--queue-config", help="A small YAML file describing known queues (see discovery/queue_contract.py for the shape)."
    )
    discover_source_group.add_argument(
        "--webpage-url",
        help=(
            "A live website with no API docs at all -- crawls same-site pages starting here "
            "(follows <a href> links, --max-pages/--max-depth bound it) and turns every <form> "
            "found into a REST operation (method, action, one field per input/select/textarea), "
            "plus one read-only 'get this page' operation per page that has no form but does "
            "have real text content. No JavaScript is executed, no link off-site is followed, "
            "and nothing is ever submitted. Lowest confidence of any source, same as --har-file: "
            "every operation flagged for review."
        ),
    )
    discover_source_group.add_argument(
        "--stored-procs", action="store_true", help="Introspect a live database's stored-procedure/function catalog."
    )
    discover_parser.add_argument(
        "--max-pages", type=int, default=25, help="Max pages to crawl for --webpage-url (default: 25)."
    )
    discover_parser.add_argument(
        "--max-depth", type=int, default=2, help="Max link depth to follow for --webpage-url (default: 2)."
    )
    discover_parser.add_argument(
        "--db-dialect", choices=["postgres", "mssql", "oracle"], help="Required with --stored-procs."
    )
    discover_parser.add_argument(
        "--db-dsn-env", help="Env var holding the DB connection string (required with --stored-procs)."
    )
    discover_parser.add_argument("--db-schema", help="Schema/owner to introspect (--stored-procs only).")
    discover_parser.add_argument(
        "--redact-header",
        action="append",
        help=(
            "A header name (beyond the built-in list -- Authorization, Cookie, etc.) whose "
            "presence should produce an auth note instead of being treated as a typed field. "
            "Repeatable. --har-file only."
        ),
    )
    discover_parser.add_argument(
        "--out",
        default="./contract.yaml",
        help=(
            "Where to write the generated contract. A '.yaml'/'.yml' path writes the plain "
            "contract; a '.html'/'.htm' path writes a human-readable report with the same "
            "contract embedded in it, so it works as --from-contract input too."
        ),
    )
    discover_parser.add_argument(
        "--no-chat",
        action="store_true",
        help=(
            "Don't auto-launch the no-API-key test-chat page in an interactive terminal once the "
            "contract is written (it's skipped automatically in non-interactive/CI runs already)."
        ),
    )
    discover_parser.add_argument(
        "--write-config", default="./config.yaml", help="Where to also write the runnable config for the demo."
    )
    discover_parser.add_argument("--no-write-config", action="store_true", help="Don't write a config.yaml at all.")
    discover_parser.add_argument("--chat-out", default="./chat.html", help="Where to save the standalone chat page.")
    discover_parser.add_argument("--host", default="127.0.0.1", help="Host to bind the local demo server to.")
    discover_parser.add_argument("--port", type=int, default=8787, help="Port to bind the local demo server to.")
    discover_parser.add_argument(
        "--no-open", action="store_true", help="Don't open chat.html in a browser automatically."
    )
    discover_parser.set_defaults(func=_cmd_discover)

    demo_parser = subparsers.add_parser(
        "demo",
        help=(
            "Start the same no-API-key test-chat server `init` can auto-launch, directly from "
            "a contract or an existing config, and save a standalone chat.html pointing at it."
        ),
    )
    demo_source_group = demo_parser.add_mutually_exclusive_group(required=True)
    demo_source_group.add_argument(
        "--from-contract", help="A contract.yaml or contract.html written by `legacy2mcp discover`."
    )
    demo_source_group.add_argument("--config", help="An existing config.yaml to serve directly.")
    demo_parser.add_argument("--out", default="./chat.html", help="Where to save the standalone chat page.")
    demo_parser.add_argument(
        "--write-config",
        default="./config.yaml",
        help="--from-contract only: where to also write the runnable config this demo serves.",
    )
    demo_parser.add_argument(
        "--no-write-config", action="store_true", help="--from-contract only: don't write a config.yaml at all."
    )
    demo_parser.add_argument("--host", default="127.0.0.1", help="Host to bind the local server to.")
    demo_parser.add_argument("--port", type=int, default=8787, help="Port to bind the local server to.")
    demo_parser.add_argument("--no-open", action="store_true", help="Don't open chat.html in a browser automatically.")
    demo_parser.set_defaults(func=_cmd_demo)

    init_parser = subparsers.add_parser(
        "init",
        help=(
            "Point legacy2mcp at a WSDL URL, a REST API URL, a website, or a GitHub repo / "
            "local source path, and generate a starter config.yaml. Prompts interactively for "
            "whichever of those you don't pass as a flag."
        ),
    )
    source_group = init_parser.add_mutually_exclusive_group()
    source_group.add_argument("--wsdl-url", help="A live WSDL URL (SOAP service) to point the generated config at directly.")
    source_group.add_argument("--rest-url", help="A live REST API base URL; tries OpenAPI/Swagger discovery against it.")
    source_group.add_argument(
        "--webpage-url",
        help=(
            "A live website with no API docs at all -- crawls same-site pages starting here "
            "(--max-pages/--max-depth bound it) the same way `legacy2mcp discover --webpage-url` "
            "does, then turns the result straight into a config instead of stopping at a "
            "contract.yaml for review first. Every operation still lands at confidence=low / "
            "needs_review=true in the generated config's TODOs -- going straight to `init` "
            "skips a file, not the review."
        ),
    )
    source_group.add_argument("--github-url", help="Repo to clone and statically scan for a SOAP client / DB model / routes.")
    source_group.add_argument("--local-path", help="Already-checked-out repo to scan (skips cloning).")
    source_group.add_argument(
        "--from-contract",
        help="A contract.yaml written by `legacy2mcp discover` -- turns its reviewed operations into a runnable config.",
    )
    init_parser.add_argument(
        "--max-pages", type=int, default=25, help="Max pages to crawl for --webpage-url (default: 25)."
    )
    init_parser.add_argument(
        "--max-depth", type=int, default=2, help="Max link depth to follow for --webpage-url (default: 2)."
    )
    init_parser.add_argument(
        "--github-token-env", help="Env var holding a GitHub token, for private repos (--github-url only)."
    )
    init_parser.add_argument(
        "--llm-api-key-env",
        help=(
            "Env var holding an OpenAI-compatible API key for LLM-assisted analysis "
            "(only used with --github-url/--local-path; the WSDL/REST paths don't need it)."
        ),
    )
    init_parser.add_argument("--llm-base-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible API base URL.")
    init_parser.add_argument("--llm-model", default=DEFAULT_MODEL)
    init_parser.add_argument(
        "--scan-only", action="store_true", help="Static analysis only -- skip LLM assist, no API key needed."
    )
    init_parser.add_argument("--out", default="./config.yaml", help="Where to write the generated config.")
    init_parser.add_argument(
        "--html-out",
        help=(
            "Where to write the plain-English HTML report describing the generated MCP "
            "server (default: same name as --out, with a .html extension)."
        ),
    )
    init_parser.add_argument(
        "--no-html-report", action="store_true", help="Skip generating the HTML report."
    )
    init_parser.add_argument(
        "--no-chat",
        action="store_true",
        help=(
            "Don't open the local test-chat page after writing the config. (It only opens "
            "when run interactively and the config needs no further edits to connect.)"
        ),
    )
    init_parser.set_defaults(func=_cmd_init)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
