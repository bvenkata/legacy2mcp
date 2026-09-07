# Security model (v0.1)

This document says plainly what protections exist today and what
doesn't, so you can make an informed decision about where to point
this at.

## What v0.1 actually does

1. **Every tool call is validated against a JSON Schema before it
   reaches the SOAP endpoint.** The schema is derived from the WSDL's
   own XSD types (see `src/legacy2mcp/schema/xsd_to_jsonschema.py`),
   so an LLM can't send a string where the WSDL declares an integer,
   can't omit a required field, and (schemas are generated with
   `additionalProperties: false`) can't smuggle in extra fields the
   operation doesn't expect.
2. **Write-like operations are excluded by default.** Operation names
   are checked against a list of write-suggesting verb prefixes
   (`Create`, `Update`, `Delete`, `Cancel`, `Submit`, `Void`, `Pay`,
   ...) at discovery time. An adapter only exposes those tools if you
   set `allow_write_operations: true` on it explicitly. This is a
   heuristic, not a guarantee -- see "What this does NOT do" below.
3. **Every call is audit-logged.** Tool name, arguments, identity (if
   available), timestamp, and success/failure are written as one JSON
   line per event to the path in `security.audit.path`. Good enough to
   answer "who called what, with what arguments, and did it succeed"
   during an incident review.
4. **Credentials never live in the config file.** Basic auth passwords
   are read from an environment variable named in `password_env`, not
   written into YAML.
5. **SOAP faults don't leak raw stack traces to the caller.** They're
   caught and re-raised as a plain error message.

## What this does NOT do (yet)

- **No authentication or authorization on the MCP server itself.**
  Anything that can spawn/connect to the server can call any exposed
  tool. If you need per-caller identity and role-based tool access,
  that's on the [roadmap](roadmap.md) (v0.2) but not implemented --
  don't expose a v0.1 server to untrusted callers.
- **The write-operation filter is a name heuristic, not semantic
  analysis.** A WSDL operation named `ProcessRecord` that actually
  deletes data would not be caught by the `_looks_like_write()` check.
  Review `include_operations` / `exclude_operations` explicitly for
  any adapter pointed at a system where a wrong call has real
  consequences -- don't rely on the heuristic alone.
- **No rate limiting.** A misbehaving or malicious caller can call a
  tool as fast as the underlying SOAP service allows.
- **No TLS/mTLS configuration is provided for the WSDL endpoint**
  beyond whatever `zeep`/`requests` do by default over HTTPS. If your
  legacy system needs client certificates, you'll need to extend
  `SoapAdapter._get_client()` yourself for now.
- **No encryption at rest for the audit log.** It's a plain JSONL
  file; arguments (which may include business data) are logged as-is.
  Point `security.audit.path` somewhere with appropriate file
  permissions, and don't point it at a WSDL whose arguments contain
  secrets you don't want written to disk.

## Reporting a vulnerability

Please open a GitHub issue marked `security`, or contact the
maintainer directly if the issue is sensitive enough that a public
issue isn't appropriate.
