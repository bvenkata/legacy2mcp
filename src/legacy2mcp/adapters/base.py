"""
The adapter contract every legacy-system connector implements.

v0.1 ships one concrete adapter (SoapAdapter). The interface is kept
deliberately narrow -- discover_tools() + invoke() -- so a future
DbAdapter or QueueAdapter (see docs/roadmap.md) can be dropped in
without touching the MCP server core in server.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDefinition:
    """One MCP tool, discovered from the underlying legacy system."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] = field(default_factory=dict)
    # Free-form provenance info surfaced in `legacy2mcp inspect` and
    # useful for audit logs (e.g. {"wsdl_operation": "GetRecordStatus"}).
    metadata: dict[str, Any] = field(default_factory=dict)


class AdapterError(Exception):
    """Raised for adapter-level failures (connection, invocation, mapping)."""


class BaseAdapter(ABC):
    """Base class for all legacy2mcp adapters."""

    #: short machine-readable adapter type, e.g. "soap"
    adapter_type: str = "base"

    def __init__(self, adapter_id: str, config: dict[str, Any]):
        self.adapter_id = adapter_id
        self.config = config

    @abstractmethod
    def discover_tools(self) -> list[ToolDefinition]:
        """Introspect the underlying system and return the tools it exposes."""
        raise NotImplementedError

    @abstractmethod
    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """
        Invoke a previously-discovered tool with already-validated
        arguments. Must return a JSON-serializable value.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Optional cleanup hook (closing connections, etc.). No-op by default."""
        return None
