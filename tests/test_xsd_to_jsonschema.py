from __future__ import annotations

from legacy2mcp.schema.xsd_to_jsonschema import _simple_schema, type_to_jsonschema
import zeep.xsd.types.builtins as b


def test_simple_type_mapping():
    assert _simple_schema(b.String()) == {"type": "string"}
    assert _simple_schema(b.Int()) == {"type": "integer"}
    assert _simple_schema(b.Boolean()) == {"type": "boolean"}
    assert _simple_schema(b.Double()) == {"type": "number"}
    assert _simple_schema(b.Date()) == {"type": "string", "format": "date"}
    assert _simple_schema(b.DateTime()) == {"type": "string", "format": "date-time"}


def test_unrecognized_type_falls_back_to_object():
    class WeirdType:
        pass

    schema = type_to_jsonschema(WeirdType())
    assert schema["type"] == "object"
    assert "WeirdType" in schema["description"]


def test_depth_limit_prevents_runaway_recursion():
    class SelfRefType:
        def __init__(self):
            self.elements = [("child", self)]

    # SelfRefType has no `.type` on its "element" -- construct a fake
    # element-like object that exposes `.type` pointing back at itself.
    class FakeElement:
        def __init__(self, type_obj):
            self.type = type_obj
            self.is_optional = False
            self.max_occurs = 1

    root = SelfRefType()
    root.elements = [("child", FakeElement(root))]

    schema = type_to_jsonschema(root, depth=0)
    # Should terminate rather than recurse forever, hitting the max-depth
    # fallback somewhere in the nested structure.
    import json

    serialized = json.dumps(schema)
    assert "max depth reached" in serialized
