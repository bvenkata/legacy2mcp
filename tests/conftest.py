# Fixture projects under tests/fixtures/ are inputs to `legacy2mcp analyze`
# (some deliberately contain their own tests/ directory), not test modules.
collect_ignore_glob = ["fixtures/*"]
