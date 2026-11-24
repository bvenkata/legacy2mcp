"""
install-skill subcommand.

Writes legacy2mcp usage instructions into the well-known AI-assistant context
files for a project (Claude Code, Cursor, Codex/AGENTS.md, Gemini, GitHub
Copilot), so an assistant working in that repo knows how to drive legacy2mcp
without being told from scratch each session. The files are plain text the
user can review and commit; nothing here is executed.

This mirrors the same install-skill pattern several other CLI tools use
(e.g. DockSec) for the same reason: a SKILL.md submitted to a marketplace is
great for discovery, but a repo's own CLAUDE.md/AGENTS.md/etc. is what an
assistant actually reads by default when it's already working in that repo.
"""

from __future__ import annotations

import os
import sys

# The canonical legacy2mcp instruction block shared across every assistant
# target. Kept intentionally short and command-first so an assistant can act
# on it immediately -- the full walkthrough lives in skill/legacy2mcp/ and
# the README for anyone who wants more detail.
SKILL_CONTENT = """\
legacy2mcp turns an existing SOAP/WSDL service, a database, a REST API, or a
plain website with no API docs into a safe, typed, audit-logged MCP server
-- read-only by default, every call audit-logged, no hand-written adapter
code or hand-maintained schemas.

Common commands:

- Point at a live WSDL (no API key needed; zeep introspects it live):
    legacy2mcp init --wsdl-url "https://service.example.com/Legacy?wsdl" --out config.yaml
- Point at a live REST API (tries OpenAPI/Swagger discovery, no API key needed):
    legacy2mcp init --rest-url "https://api.example.com" --out config.yaml
- Point at a website with no API docs at all (crawls it for forms/content;
  every operation lands confidence=low/needs_review=true -- review before trusting):
    legacy2mcp init --webpage-url "https://your-legacy-app.example.com/some-page" --out config.yaml
- Understand a whole local Python or JS/TS codebase first -- business flows,
  risk per flow, proposed MCP tools, agent topology, roadmap -- in one
  offline report (no API key; nothing executed or uploaded):
    legacy2mcp analyze ./path/to/repo --no-ai --out ./legacy2mcp-analysis
  then turn the flows the user approved into a server config (writes stay
  hidden unless --allow-writes):
    legacy2mcp generate ./legacy2mcp-analysis --selections selections.json --base-url "https://staging.example.com" --out config.yaml
- Scan a GitHub repo or local codebase for a SOAP client / ORM model / routes:
    legacy2mcp init --github-url https://github.com/you/your-api --scan-only --out config.yaml
    legacy2mcp init --local-path ./path/to/repo --scan-only --out config.yaml
- See the real discovered tools before trusting anything (no server started):
    legacy2mcp inspect --config config.yaml
- Start the MCP server (an MCP client normally launches this itself --
  running it by hand just blocks waiting for a client over stdio):
    legacy2mcp run --config config.yaml

After `init`, review config.yaml's `# TODO` comments and the plain-English
config.html report before trusting the generated config -- it's a static/
introspected best effort, not yet verified against the live backend.
Write-like operations stay hidden unless `allow_write_operations: true` is
set explicitly, and every tool call is audit-logged to
./legacy2mcp-audit.log. Database access is an explicit table/column
allowlist generated from the live schema -- never arbitrary SQL.

To wire the generated server into an MCP client, add to its config (use an
absolute path to config.yaml):
    {"mcpServers": {"<name>": {"command": "legacy2mcp", "args": ["run", "--config", "/absolute/path/to/config.yaml"]}}}

Full docs: https://github.com/bvenkata/legacy2mcp
"""

# Heading used when appending into a shared instructions file, so re-running
# install-skill updates the legacy2mcp section in place instead of
# duplicating it.
_SECTION_MARKER = "## legacy2mcp"


def _write_claude_code_skill(project_root: str) -> str:
    """Write a Claude Code slash-command file (/legacy2mcp)."""
    rel = os.path.join(".claude", "commands", "legacy2mcp.md")
    path = os.path.join(project_root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    content = f"<!-- Run this skill with /legacy2mcp in any Claude Code session -->\n\n{SKILL_CONTENT}\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return rel


def _write_cursor_skill(project_root: str) -> str:
    """Write a Cursor rules file."""
    rel = os.path.join(".cursor", "rules", "legacy2mcp.mdc")
    path = os.path.join(project_root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    front_matter = "---\ndescription: legacy2mcp -- legacy SOAP/DB/REST to MCP server\nglobs: []\nalwaysApply: false\n---\n\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(front_matter + SKILL_CONTENT + "\n")
    return rel


def _write_append_skill(project_root: str, rel_path: str) -> str:
    """Write or update a '## legacy2mcp' section in a shared instructions file.

    If the file does not exist it is created with just the legacy2mcp
    section. If it exists and already has a legacy2mcp section, that section
    is replaced in place (up to the next top-level heading); otherwise the
    section is appended.
    """
    section = f"{_SECTION_MARKER}\n\n{SKILL_CONTENT}\n"
    path = os.path.join(project_root, rel_path)

    if not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(section)
        return rel_path

    with open(path, "r", encoding="utf-8") as f:
        existing = f.read()

    idx = existing.find(_SECTION_MARKER)
    if idx == -1:
        sep = "\n" if existing.endswith("\n") else "\n\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(existing + sep + section)
        return rel_path

    # Replace from the marker to the next top-level heading (or end of file).
    after = existing.find("\n## ", idx + len(_SECTION_MARKER))
    before = existing[:idx]
    tail = "" if after == -1 else existing[after:]
    with open(path, "w", encoding="utf-8") as f:
        f.write(before + section + tail)
    return rel_path


def install_skill(project_root: str | None = None) -> None:
    """Install legacy2mcp instruction files for common AI coding assistants."""
    project_root = project_root or os.getcwd()

    written = [
        ("Claude Code", _write_claude_code_skill(project_root)),
        ("Codex CLI", _write_append_skill(project_root, "AGENTS.md")),
        ("Gemini CLI", _write_append_skill(project_root, "GEMINI.md")),
        ("Cursor", _write_cursor_skill(project_root)),
        ("GitHub Copilot", _write_append_skill(project_root, os.path.join(".github", "copilot-instructions.md"))),
    ]

    print("legacy2mcp assistant skills installed:", file=sys.stderr)
    for label, rel in written:
        print(f"  {label:<16} {rel}", file=sys.stderr)
    print("Commit these files to your repo to share them with your team.", file=sys.stderr)
