"""`legacy2mcp generate`: approved tools -> runnable MCP server config."""

from legacy2mcp.analysis.generate.config import GenerateError, GenerateResult, build_config, load_selections

__all__ = ["GenerateError", "GenerateResult", "build_config", "load_selections"]
