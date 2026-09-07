from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from legacy2mcp.config import load_config
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="legacy2mcp")
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

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
