"""
`legacy2mcp analyze`: local, static understanding of a whole codebase --
what it does, its entry points and side effects, its business flows -- as
one serializable analysis file that the report and `generate` read back.

Separate from `legacy2mcp.discovery`, which turns a single interface (WSDL,
OpenAPI, HAR, ...) into a config; nothing here changes that path.
"""
