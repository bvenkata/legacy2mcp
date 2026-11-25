"""Call, module and data-access graphs over the parsed modules."""

from legacy2mcp.analysis.graph.build import build_graph, resource_for
from legacy2mcp.analysis.graph.resolve import Resolver

__all__ = ["Resolver", "build_graph", "resource_for"]
