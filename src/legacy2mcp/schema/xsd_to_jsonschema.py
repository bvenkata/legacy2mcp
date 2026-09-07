"""
Converts zeep's parsed XSD type objects into JSON Schema.

This is the piece that makes the SOAP adapter safe to point an LLM at:
every WSDL operation's parameters get a real, typed JSON Schema, so
`jsonschema.validate()` can reject malformed calls before they ever
reach the SOAP endpoint (see security/validation.py).

zeep represents WSDL types as a small class hierarchy:
  - Builtin simple types (zeep.xsd.types.builtins.*) -- String, Int,
    Boolean, Float, Double, Decimal, Date, DateTime, etc.
  - ComplexType -- has `.elements`, a list of (name, Element) pairs.
  - An Element wraps a type and carries occurrence info (`max_occurs`,
    `is_optional`).

We walk that structure recursively and produce a plain JSON Schema
dict. Recursion is depth-limited (`_MAX_DEPTH`) because some
real-world enterprise WSDLs have self-referential or very deeply
nested complex types; past that depth we fall back to an open
"any object" schema rather than hanging or recursing forever.
"""

from __future__ import annotations

from typing import Any

_MAX_DEPTH = 8

# Map zeep builtin type class names -> (json_type, format)
_SIMPLE_TYPE_MAP: dict[str, tuple[str, str | None]] = {
    "String": ("string", None),
    "NormalizedString": ("string", None),
    "Token": ("string", None),
    "Name": ("string", None),
    "NCName": ("string", None),
    "Language": ("string", None),
    "AnyURI": ("string", "uri"),
    "QName": ("string", None),
    "Boolean": ("boolean", None),
    "Int": ("integer", None),
    "Integer": ("integer", None),
    "Long": ("integer", None),
    "Short": ("integer", None),
    "Byte": ("integer", None),
    "UnsignedInt": ("integer", None),
    "UnsignedLong": ("integer", None),
    "UnsignedShort": ("integer", None),
    "UnsignedByte": ("integer", None),
    "NonNegativeInteger": ("integer", None),
    "NonPositiveInteger": ("integer", None),
    "PositiveInteger": ("integer", None),
    "NegativeInteger": ("integer", None),
    "Float": ("number", None),
    "Double": ("number", None),
    "Decimal": ("number", None),
    "Date": ("string", "date"),
    "DateTime": ("string", "date-time"),
    "Time": ("string", "time"),
    "Duration": ("string", None),
    "Base64Binary": ("string", "byte"),
    "HexBinary": ("string", None),
    "AnyType": (None, None),  # left untyped on purpose
    "AnySimpleType": (None, None),
}


def _simple_schema(type_obj: Any) -> dict[str, Any] | None:
    """Return a JSON Schema fragment if `type_obj` is a known simple/builtin type."""
    cls_name = type(type_obj).__name__
    mapping = _SIMPLE_TYPE_MAP.get(cls_name)
    if mapping is None:
        return None
    json_type, fmt = mapping
    if json_type is None:
        return {}  # accept anything
    schema: dict[str, Any] = {"type": json_type}
    if fmt:
        schema["format"] = fmt
    return schema


def element_to_jsonschema(element: Any, depth: int = 0) -> dict[str, Any]:
    """
    Convert a single zeep Element (has .type, .is_optional, .max_occurs)
    into a JSON Schema fragment, applying array wrapping when the
    element can repeat (maxOccurs > 1 or "unbounded").
    """
    base = type_to_jsonschema(element.type, depth=depth)

    max_occurs = getattr(element, "max_occurs", 1)
    is_array = max_occurs == "unbounded" or (isinstance(max_occurs, int) and max_occurs > 1)
    if is_array:
        return {"type": "array", "items": base}
    return base


def type_to_jsonschema(type_obj: Any, depth: int = 0) -> dict[str, Any]:
    """
    Convert a zeep xsd type object (simple builtin, ComplexType, or an
    enumeration-restricted simple type) into a JSON Schema fragment.
    """
    if depth > _MAX_DEPTH:
        return {"type": "object", "description": "Nested type truncated (max depth reached)."}

    simple = _simple_schema(type_obj)
    if simple is not None:
        return simple

    # Enumeration (xsd:restriction with xsd:enumeration values) shows up
    # in zeep as a type with an `enum` attribute of allowed values.
    enum_values = getattr(type_obj, "enum", None)
    if enum_values:
        return {"type": "string", "enum": list(enum_values)}

    # Complex type: has a `.elements` list of (name, Element) pairs.
    elements = getattr(type_obj, "elements", None)
    if elements is not None:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for name, el in elements:
            properties[name] = element_to_jsonschema(el, depth=depth + 1)
            if not getattr(el, "is_optional", False):
                required.append(name)
        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        schema["additionalProperties"] = False
        return schema

    # Fallback for anything we don't recognize (xsd:any, extension
    # types zeep couldn't fully resolve, etc.) -- accept any object
    # rather than silently dropping the field.
    return {"type": "object", "description": f"Unrecognized XSD type: {type(type_obj).__name__}"}


def operation_input_schema(operation: Any) -> dict[str, Any]:
    """
    Build the top-level JSON Schema for a WSDL operation's input
    message. Each top-level element/param becomes a schema property,
    mirroring how you'd call `client.service.OperationName(param=...)`
    with zeep.
    """
    body_type = operation.input.body.type
    return type_to_jsonschema(body_type)


def operation_output_schema(operation: Any) -> dict[str, Any]:
    """Build the JSON Schema describing what an operation returns (informational only)."""
    if operation.output is None or operation.output.body is None:
        return {"type": "null"}
    return type_to_jsonschema(operation.output.body.type)
