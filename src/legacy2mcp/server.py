"""
The MCP server core.

Deliberately uses the MCP SDK's low-level `Server` (list_tools/call_tool
handlers) rather than the FastMCP decorator API: FastMCP infers a
tool's input schema from a Python function's type-hinted signature,
which works well when you're hand-writing tools but not when tools
(and their schemas) are discovered dynamically at startup from a WSDL
that isn't known until config is loaded. The low-level API lets us
hand it an arbitrary JSON Schema per tool, which is exactly what
schema.xsd_to_jsonschema produces.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from legacy2mcp.adapters.base import AdapterError, BaseAdapter, ToolDefinition
from legacy2mcp.adapters.soap_adapter import SoapAdapter
from legacy2mcp.config import AdapterConfig, Legacy2McpConfig
from legacy2mcp.security.audit import AuditLogger
from legacy2mcp.security.validation import ValidationError, validate_arguments

logger = logging.getLogger("legacy2mcp.server")


def build_adapter(adapter_config: AdapterConfig) -> BaseAdapter:
    if adapter_config.type == "soap":
        return SoapAdapter(adapter_config.id, adapter_config.soap_settings())
    if adapter_config.type in ("db", "queue"):
        raise NotImplementedError(
            f"Adapter type '{adapter_config.type}' is on the roadmap (see docs/roadmap.md) "
            f"but not implemented in this version of legacy2mcp. "
            f"Adapter '{adapter_config.id}' cannot be started."
        )
    raise ValueError(f"Unknown adapter type: '{adapter_config.type}'")


class Legacy2McpApp:
    """Wires config -> adapters -> discovered tools -> MCP Server handlers."""

    def __init__(self, config: Legacy2McpConfig):
        self.config = config
        self.mcp_server = Server(config.server.name)
        self.audit = AuditLogger(
            path=config.security.audit.path, enabled=config.security.audit.enabled
        )

        self._adapters: dict[str, BaseAdapter] = {}
        self._tools: dict[str, tuple[BaseAdapter, ToolDefinition]] = {}

        self._load_adapters()
        self._register_handlers()

    def _load_adapters(self) -> None:
        for adapter_config in self.config.adapters:
            if not adapter_config.enabled:
                logger.info("Adapter '%s' is disabled, skipping.", adapter_config.id)
                continue

            adapter = build_adapter(adapter_config)
            self._adapters[adapter_config.id] = adapter

            tool_defs = adapter.discover_tools()
            for tool_def in tool_defs:
                if tool_def.name in self._tools:
                    raise ValueError(
                        f"Duplicate tool name '{tool_def.name}' -- two adapters produced "
                        f"the same tool name. Give adapters distinct ids."
                    )
                self._tools[tool_def.name] = (adapter, tool_def)

            logger.info(
                "Adapter '%s' (%s) exposed %d tool(s).",
                adapter_config.id,
                adapter_config.type,
                len(tool_defs),
            )

        if not self._tools:
            logger.warning(
                "No tools were discovered from any adapter. Check that adapters are "
                "enabled and reachable, and that operations aren't all filtered out."
            )

    def _register_handlers(self) -> None:
        @self.mcp_server.list_tools()
        async def list_tools() -> list[types.Tool]:
            return [
                types.Tool(
                    name=tool_def.name,
                    description=tool_def.description,
                    inputSchema=tool_def.input_schema or {"type": "object"},
                )
                for _, tool_def in self._tools.values()
            ]

        @self.mcp_server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
            arguments = arguments or {}
            return await self._dispatch(name, arguments)

    async def _dispatch(self, name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        if name not in self._tools:
            raise ValueError(f"Unknown tool: '{name}'")

        adapter, tool_def = self._tools[name]
        started_at = self.audit.log_call_start(name, arguments)

        try:
            validate_arguments(arguments, tool_def.input_schema, name)
            result = await adapter.invoke(name, arguments)
        except ValidationError as exc:
            self.audit.log_call_error(name, started_at, str(exc))
            raise ValueError(str(exc)) from exc
        except AdapterError as exc:
            self.audit.log_call_error(name, started_at, str(exc))
            raise ValueError(str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive catch-all
            self.audit.log_call_error(name, started_at, f"Unexpected error: {exc}")
            raise

        self.audit.log_call_success(name, started_at)
        return [types.TextContent(type="text", text=json.dumps(result, default=str))]

    def close(self) -> None:
        for adapter in self._adapters.values():
            adapter.close()

    async def run_stdio(self) -> None:
        async with stdio_server() as (read_stream, write_stream):
            await self.mcp_server.run(
                read_stream,
                write_stream,
                self.mcp_server.create_initialization_options(),
            )
