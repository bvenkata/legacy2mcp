"""
Argument validation. This is safety net #1: no arguments reach an
adapter's invoke() without first passing jsonschema.validate() against
that tool's declared input_schema. Adapters can (and do, for SOAP)
still reject at the transport layer too, but this catches bad input
before any network/DB/queue call is attempted.
"""

from __future__ import annotations

from typing import Any

import jsonschema


class ValidationError(Exception):
    """Raised when tool call arguments don't match the tool's input schema."""


def validate_arguments(arguments: dict[str, Any], schema: dict[str, Any], tool_name: str) -> None:
    try:
        jsonschema.validate(instance=arguments, schema=schema)
    except jsonschema.ValidationError as exc:
        raise ValidationError(
            f"Arguments for tool '{tool_name}' failed schema validation: {exc.message}"
        ) from exc
