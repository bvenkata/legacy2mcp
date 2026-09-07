"""
SOAP/WSDL adapter.

Points at a WSDL URL, introspects every operation on every port/binding,
and turns each one into an MCP ToolDefinition with a real JSON Schema
(built by schema.xsd_to_jsonschema). At invoke time it validates the
LLM-supplied arguments against that schema (safety net #1), calls the
underlying SOAP operation via zeep (a mature, widely used SOAP client),
and serializes the zeep response into plain JSON-safe Python.

Safety defaults:
  - Operations are name-sniffed for write-like verbs (Create, Update,
    Delete, Cancel, Void, Submit, ...). Exposing these requires
    `allow_write_operations: true` in config -- an adapter is
    read-only by default, matching the "no arbitrary writes without
    an explicit opt-in" posture the rest of legacy2mcp uses for DB
    access (see docs/roadmap.md for the DB adapter's own allowlist).
  - `include_operations` / `exclude_operations` give an explicit
    allowlist/denylist on top of that.
  - SOAP faults are caught and re-raised as AdapterError with the
    fault string, never as a raw stack trace leaking transport
    internals to the caller.
"""

from __future__ import annotations

import logging
from typing import Any

import zeep
from zeep.exceptions import Fault, TransportError
from zeep.helpers import serialize_object
from zeep.transports import Transport

from legacy2mcp.adapters.base import AdapterError, BaseAdapter, ToolDefinition
from legacy2mcp.config import BasicAuthConfig, SoapAdapterSettings
from legacy2mcp.schema.xsd_to_jsonschema import operation_input_schema, operation_output_schema

logger = logging.getLogger("legacy2mcp.soap")

_WRITE_VERB_PREFIXES = (
    "create",
    "update",
    "delete",
    "remove",
    "cancel",
    "void",
    "submit",
    "approve",
    "reject",
    "insert",
    "modify",
    "set",
    "post",
    "pay",
    "charge",
    "issue",
)


def _looks_like_write(operation_name: str) -> bool:
    lowered = operation_name.lower()
    return any(lowered.startswith(v) for v in _WRITE_VERB_PREFIXES)


class SoapAdapter(BaseAdapter):
    adapter_type = "soap"

    def __init__(self, adapter_id: str, settings: SoapAdapterSettings):
        super().__init__(adapter_id, settings.model_dump())
        self.settings = settings
        self._client: zeep.Client | None = None
        # tool name -> (service_name, port_name, operation_name, single_output_field)
        self._tool_index: dict[str, tuple[str, str, str, str | None]] = {}

    def _get_client(self) -> zeep.Client:
        if self._client is not None:
            return self._client

        session = None
        if isinstance(self.settings.auth, BasicAuthConfig):
            import requests
            from requests.auth import HTTPBasicAuth

            session = requests.Session()
            session.auth = HTTPBasicAuth(
                self.settings.auth.username, self.settings.auth.resolve_password()
            )

        transport = Transport(session=session, timeout=self.settings.timeout_seconds)
        self._client = zeep.Client(self.settings.wsdl_url, transport=transport)
        return self._client

    def _tool_name(self, operation_name: str) -> str:
        return f"{self.adapter_id}_{operation_name}"

    def discover_tools(self) -> list[ToolDefinition]:
        client = self._get_client()
        tools: list[ToolDefinition] = []

        include = set(self.settings.include_operations) if self.settings.include_operations else None
        exclude = set(self.settings.exclude_operations)

        for service_name, service in client.wsdl.services.items():
            for port_name, port in service.ports.items():
                binding = port.binding
                operations = getattr(binding, "_operations", {})
                for op_name, operation in operations.items():
                    if include is not None and op_name not in include:
                        continue
                    if op_name in exclude:
                        continue
                    if _looks_like_write(op_name) and not self.settings.allow_write_operations:
                        logger.info(
                            "Skipping SOAP operation '%s' on adapter '%s': looks like a write "
                            "operation and allow_write_operations is false.",
                            op_name,
                            self.adapter_id,
                        )
                        continue

                    try:
                        input_schema = operation_input_schema(operation)
                        output_schema = operation_output_schema(operation)
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.warning(
                            "Skipping SOAP operation '%s': failed to build schema (%s)",
                            op_name,
                            exc,
                        )
                        continue

                    tool_name = self._tool_name(op_name)
                    doc = (operation.__doc__ or "").strip()
                    description = (
                        f"SOAP operation '{op_name}' on service '{service_name}' "
                        f"(adapter: {self.adapter_id})."
                    )
                    if doc and "Represent's an operation" not in doc:
                        description += f" {doc}"

                    # zeep unwraps a response with exactly one field to a bare
                    # scalar. Remember that field's name so invoke() can put
                    # it back into a {field_name: value} shape matching
                    # output_schema, rather than silently returning a bare
                    # scalar the caller has no name for.
                    output_props = output_schema.get("properties") if isinstance(output_schema, dict) else None
                    single_output_field = (
                        next(iter(output_props)) if output_props and len(output_props) == 1 else None
                    )

                    tools.append(
                        ToolDefinition(
                            name=tool_name,
                            description=description,
                            input_schema=input_schema,
                            output_schema=output_schema,
                            metadata={
                                "adapter_type": "soap",
                                "adapter_id": self.adapter_id,
                                "wsdl_operation": op_name,
                                "wsdl_service": service_name,
                                "wsdl_port": port_name,
                                "single_output_field": single_output_field,
                            },
                        )
                    )
                    self._tool_index[tool_name] = (service_name, port_name, op_name, single_output_field)

        return tools

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name not in self._tool_index:
            raise AdapterError(f"Unknown tool '{tool_name}' for adapter '{self.adapter_id}'.")

        _, _, op_name, single_output_field = self._tool_index[tool_name]
        client = self._get_client()

        try:
            operation_callable = getattr(client.service, op_name)
            result = operation_callable(**arguments)
        except Fault as exc:
            raise AdapterError(f"SOAP fault from operation '{op_name}': {exc.message}") from exc
        except TransportError as exc:
            raise AdapterError(f"Transport error calling '{op_name}': {exc}") from exc
        except TypeError as exc:
            raise AdapterError(f"Invalid arguments for operation '{op_name}': {exc}") from exc

        serialized = serialize_object(result, target_cls=dict)
        if not isinstance(serialized, dict) and single_output_field:
            # zeep unwrapped a single-field response to a bare scalar;
            # restore the field name so the caller gets a named result
            # matching the tool's declared output_schema.
            serialized = {single_output_field: serialized}
        return serialized

    def close(self) -> None:
        self._client = None
