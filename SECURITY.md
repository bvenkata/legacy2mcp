# Security Policy

legacy2mcp is an [OWASP Incubator Project](https://owasp.org/projects/legacy2mcp).
For what the tool validates and audit-logs today (and what it doesn't yet),
see the [security model](docs/security.md).

## Supported versions

Security fixes go into the latest released version on
[PyPI](https://pypi.org/project/legacy2mcp/).

## Reporting a vulnerability

Please report security issues in legacy2mcp itself privately. Do not open a
public GitHub issue for an undisclosed vulnerability.

- Preferred: GitHub private vulnerability reporting. Open the
  [Security advisories page](https://github.com/bvenkata/legacy2mcp/security/advisories)
  and click "Report a vulnerability".
- Or email the project leader at balaji.venkatasubramaniyar@owasp.org.

Please include a description of the issue, the affected version or commit,
steps to reproduce, and the possible impact.

## Scope

This policy covers legacy2mcp itself: its CLI, the generated MCP server,
the adapters, and the safety controls (read-only default, write-operation
gating, database allowlists, audit logging).

It does not cover vulnerabilities in the backend systems you point
legacy2mcp at, or in third-party dependencies that are already publicly
disclosed elsewhere.

## Disclosure

We'll confirm the report, assess impact, prepare a fix, and disclose
responsibly once users have a version to update to.
