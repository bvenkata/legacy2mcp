"""
Turn a `scan.ScanResult` into a `Legacy2McpConfig`-shaped dict (plus a list
of human-readable TODOs for anything the scan couldn't pin down).

Precedence mirrors what legacy2mcp already supports, cheapest/most-certain
first: SOAP usage beats DB usage beats a plain route table, because a repo
that talks to a WSDL is almost certainly a thin wrapper around that SOAP
service, same for an ORM-backed app -- those map onto the existing `soap`/
`db` adapters with real schema introspection at startup. Only a repo with
no SOAP/DB signal falls back to the new `rest` adapter, which has to trust
the config as scanned since there's no live schema to check it against.

Nothing here ever enables a write operation -- `allow_write_operations`
is always emitted `false`, matching the rest of legacy2mcp's safety
posture; the generated config only ever flags which operations *could*
be turned on.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from legacy2mcp.discovery.contract import ContractError, ContractOperation, LegacyInterfaceContract
from legacy2mcp.discovery.heuristics import looks_like_write
from legacy2mcp.discovery.llm import OpenAiLlmClient
from legacy2mcp.discovery.scan import DbFinding, RouteFinding, ScanResult, SoapFinding

# source_kind values that `init --from-contract` can actually turn into a
# runnable adapter today. db_proc/queue have no runtime adapter yet (see
# docs/roadmap.md) -- their contract entries are documentation only, and
# get skipped with an explanatory TODO rather than silently dropped.
_INVOCABLE_SOURCE_KINDS = {"rest"}


def build_config(
    scan: ScanResult,
    llm: OpenAiLlmClient | None = None,
    base_url: str | None = None,
) -> tuple[dict, list[str]]:
    """`base_url` is only meaningful for the REST fallback -- pass it when
    the caller already knows where the API is deployed (e.g. the user gave
    a REST API URL directly) so the generated config doesn't need a
    placeholder + TODO for something that's already known."""
    if scan.soap is not None:
        return _soap_config(scan.soap)
    if scan.db is not None:
        return _db_config(scan.db)
    return _rest_config(scan.routes, llm, base_url=base_url)


def _soap_config(soap: SoapFinding) -> tuple[dict, list[str]]:
    todos: list[str] = []
    wsdl_url = soap.wsdl_url
    if not wsdl_url:
        wsdl_url = "REPLACE_ME_WSDL_URL"
        todos.append(
            f"Set adapters[0].config.wsdl_url -- a zeep.Client(...) call was found in "
            f"'{soap.file}' but its WSDL URL wasn't a literal string, so it couldn't be read statically."
        )
    else:
        todos.append(f"SOAP target: '{soap.file}'. Review auth: and allow_write_operations: before running.")

    config = {
        "server": {"name": "legacy-mcp"},
        "adapters": [
            {
                "id": "legacy",
                "type": "soap",
                "config": {
                    "wsdl_url": wsdl_url,
                    "allow_write_operations": False,
                },
            }
        ],
        "security": {"audit": {"enabled": True, "path": "./legacy2mcp-audit.log"}},
    }
    return config, todos


def _db_config(db: DbFinding) -> tuple[dict, list[str]]:
    todos: list[str] = []
    dialect = db.dialect
    if dialect is None:
        dialect = "postgres"
        todos.append(
            "Set adapters[0].config.dialect -- an ORM model was found but no "
            "postgres/mssql/oracle driver import, so the dialect defaulted to 'postgres'."
        )

    dsn_env = db.dsn_env or "LEGACY_DB_DSN"
    if db.dsn_env:
        todos.append(
            f"Keep the {dsn_env} environment variable set when running `legacy2mcp run` "
            f"(dsn_env just names the variable, never the value)."
        )
        todos.append(
            "Review this table list -- every table of the schema was read from the live "
            "database; delete any table an agent shouldn't see. Reading a table is not a "
            "decision that it's safe to expose."
        )
    else:
        todos.append(
            "Set the LEGACY_DB_DSN environment variable to your real connection string "
            "before running `legacy2mcp run` (dsn_env just names the variable, never the value)."
        )

    tables = []
    for table in db.tables:
        entry = {
            "name": table.table,
            "operations": ["select"],
            "columns": table.columns,
        }
        if db.db_schema:
            entry["schema"] = db.db_schema
        tables.append(entry)
        todos.append(
            f"Review columns for table '{table.table}' (from '{table.file}') -- "
            f"drop any that shouldn't be exposed to an agent (PII, internal flags, ...)."
        )

    config = {
        "server": {"name": "legacy-mcp"},
        "adapters": [
            {
                "id": "legacy",
                "type": "db",
                "config": {
                    "dialect": dialect,
                    "dsn_env": dsn_env,
                    "allow_write_operations": False,
                    "tables": tables,
                },
            }
        ],
        "security": {"audit": {"enabled": True, "path": "./legacy2mcp-audit.log"}},
    }
    return config, todos


def _dedupe_names(routes: list[RouteFinding]) -> dict[int, str]:
    seen: dict[str, int] = {}
    names: dict[int, str] = {}
    for i, route in enumerate(routes):
        count = seen.get(route.name, 0)
        seen[route.name] = count + 1
        names[i] = route.name if count == 0 else f"{route.name}_{count}"
    return names


def _rest_config(
    routes: list[RouteFinding], llm: OpenAiLlmClient | None, base_url: str | None = None
) -> tuple[dict, list[str]]:
    todos: list[str] = []
    resolved_base_url = base_url or "REPLACE_ME_BASE_URL"

    if not routes:
        if base_url is None:
            todos.append(
                "No SOAP client, ORM model, or Flask/FastAPI route was detected statically. "
                "This is an empty scaffold -- fill in adapters[0].config by hand, or point "
                "`legacy2mcp init` at a different path/repo."
            )
        else:
            todos.append(
                f"No OpenAPI/Swagger spec was found at '{base_url}'. This is an empty scaffold -- "
                f"add endpoints by hand, or point `legacy2mcp init` at the source repo instead."
            )
        config = {
            "server": {"name": "legacy-mcp"},
            "adapters": [
                {
                    "id": "legacy",
                    "type": "rest",
                    "config": {"base_url": resolved_base_url, "allow_write_operations": False, "endpoints": []},
                }
            ],
            "security": {"audit": {"enabled": True, "path": "./legacy2mcp-audit.log"}},
        }
        return config, todos

    names = _dedupe_names(routes)
    write_flags = {i: (route.method != "GET") for i, route in enumerate(routes)}
    descriptions = {i: route.description for i, route in enumerate(routes)}
    param_type_overrides: dict[int, dict[str, str]] = {}

    ambiguous_indices = [
        i
        for i, route in enumerate(routes)
        if write_flags[i] or any(p.needs_review for p in route.params)
    ]
    if llm is not None and ambiguous_indices:
        llm_input = [
            {
                "name": names[i],
                "method": routes[i].method,
                "path": routes[i].path,
                "params": [{"name": p.name, "type": p.type, "needs_review": p.needs_review} for p in routes[i].params],
            }
            for i in ambiguous_indices
        ]
        refined = llm.refine_routes(llm_input)
        refined_by_name = {r.get("name"): r for r in refined if isinstance(r, dict)}
        for i in ambiguous_indices:
            r = refined_by_name.get(names[i])
            if not r:
                continue
            if "write" in r:
                write_flags[i] = bool(r["write"])
            if r.get("description"):
                descriptions[i] = r["description"]
            if isinstance(r.get("param_types"), dict):
                param_type_overrides[i] = r["param_types"]
    else:
        # No LLM available: fall back to the same name-based write heuristic
        # the SOAP adapter already uses, on top of the method-based default.
        for i, route in enumerate(routes):
            if not write_flags[i] and looks_like_write(route.name):
                write_flags[i] = True

    endpoints = []
    for i, route in enumerate(routes):
        params = []
        for p in route.params:
            final_type = param_type_overrides.get(i, {}).get(p.name, p.type)
            params.append(
                {
                    "name": p.name,
                    "location": p.location,
                    "type": final_type,
                    "required": p.required,
                }
            )
            if p.needs_review and final_type == p.type:
                todos.append(
                    f"Verify type of param '{p.name}' on endpoint '{names[i]}' ({route.file}) -- "
                    f"defaulted to 'string', not confidently inferred."
                )

        endpoints.append(
            {
                "name": names[i],
                "method": route.method,
                "path": route.path,
                "description": descriptions[i],
                "params": params,
                "write": write_flags[i],
            }
        )

    write_count = sum(1 for e in endpoints if e["write"])
    if write_count:
        todos.append(
            f"{write_count} endpoint(s) were classified as write operations and stay hidden "
            f"unless you set allow_write_operations: true. Review the 'write' flags above."
        )
    if base_url is None:
        todos.append("Set adapters[0].config.base_url to where this API is actually deployed/running.")

    config = {
        "server": {"name": "legacy-mcp"},
        "adapters": [
            {
                "id": "legacy",
                "type": "rest",
                "config": {
                    "base_url": resolved_base_url,
                    "allow_write_operations": False,
                    "endpoints": endpoints,
                },
            }
        ],
        "security": {"audit": {"enabled": True, "path": "./legacy2mcp-audit.log"}},
    }
    return config, todos


def _endpoint_from_contract_operation(op: ContractOperation, name: str) -> tuple[dict, list[str]]:
    todos: list[str] = []
    params = []
    for field in op.fields:
        param: dict = {
            "name": field.name,
            "location": field.location if field.location != "header" else "query",
            "type": field.type if field.type in ("string", "integer", "number", "boolean") else "string",
            "required": field.required,
        }
        if field.enum:
            param["enum"] = field.enum
        params.append(param)
        if field.needs_review:
            todos.append(f"Review field '{field.name}' on '{name}' -- flagged needs_review by the contract.")
        if field.location == "header":
            todos.append(f"Field '{field.name}' on '{name}' was a header param -- the rest adapter only supports path/query/body; mapped to query, review before running.")
        if field.type not in ("string", "integer", "number", "boolean"):
            todos.append(f"Field '{field.name}' on '{name}' has type '{field.type}', which the rest adapter can't represent -- defaulted to 'string'.")

    method = op.method or "GET"
    # Trust an explicit "read" (every current importer only ever sets that
    # when it's actually confident -- a GET with no write-verb name match).
    # Anything else ("write", or "unknown" from a hand-built/future contract
    # source) falls back to the same method/name heuristic _rest_config
    # already uses for a static scan -- erring toward treating it as a
    # write rather than silently exposing an unreviewed operation as if it
    # were safely read-only.
    is_write = op.side_effect == "write" or (
        op.side_effect != "read" and (method != "GET" or looks_like_write(name))
    )

    endpoint = {
        "name": name,
        "method": method,
        "path": op.path or "/",
        "description": op.description,
        "params": params,
        "write": is_write,
        "confidence": op.confidence,
        "error_codes": op.error_codes,
        "needs_review": op.needs_review,
    }
    if op.needs_review:
        todos.append(f"Operation '{name}' was flagged needs_review by the contract -- check its notes: {'; '.join(op.notes) or '(none)'}.")
    return endpoint, todos


def _base_url_from_source_label(source_label: str) -> str | None:
    """`discovery/webpage.py` is the one importer whose `source_label` is
    always the live URL it fetched (every other importer's source_label
    is a local file path, or e.g. "postgres database") -- when it is a
    real http(s) URL, the host that served the page is also the host its
    forms/links resolve against, so there's no REPLACE_ME_BASE_URL to
    fill in by hand: `init --from-contract` already knows where this API
    actually lives."""
    parts = urlsplit(source_label)
    if parts.scheme in ("http", "https") and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return None


def build_config_from_contract(contract: LegacyInterfaceContract) -> tuple[dict, list[str]]:
    """`legacy2mcp init --from-contract`: turn a human-reviewed Legacy
    Interface Contract (from `legacy2mcp discover`) into a runnable config,
    the same shape `build_config` produces from a static scan. Only ever
    builds ONE adapter per run -- a contract whose invocable operations span
    more than one source_kind needs splitting into separate `discover` runs
    and separate `init --from-contract` calls, consistent with every other
    path through this module (SOAP/DB/REST are always one adapter each)."""
    todos: list[str] = []
    invocable = [op for op in contract.operations if op.source_kind in _INVOCABLE_SOURCE_KINDS]
    skipped = [op for op in contract.operations if op.source_kind not in _INVOCABLE_SOURCE_KINDS]

    for op in skipped:
        reason = "; ".join(op.notes) or "no runtime adapter yet"
        todos.append(f"Skipped '{op.name}' ({op.source_kind}): {reason}.")

    kinds_present = {op.source_kind for op in invocable}
    if len(kinds_present) > 1:
        raise ContractError(
            f"This contract mixes invocable source kinds {sorted(kinds_present)} -- "
            f"`init --from-contract` builds one adapter per run. Split the contract "
            f"(or re-run `discover` separately per source) and convert each on its own."
        )
    if not invocable:
        raise ContractError(
            "This contract has no operations `init --from-contract` can turn into a runnable "
            "tool yet (db_proc/queue have no runtime adapter -- see docs/roadmap.md)."
        )

    names = _dedupe_names(invocable)
    endpoints = []
    for i, op in enumerate(invocable):
        endpoint, op_todos = _endpoint_from_contract_operation(op, names[i])
        endpoints.append(endpoint)
        todos.extend(op_todos)

    write_count = sum(1 for e in endpoints if e["write"])
    if write_count:
        todos.append(
            f"{write_count} endpoint(s) were classified as write operations and stay hidden "
            f"unless you set allow_write_operations: true. Review the 'write' flags above."
        )

    resolved_base_url = _base_url_from_source_label(contract.source_label)
    if resolved_base_url is None:
        resolved_base_url = "REPLACE_ME_BASE_URL"
        todos.append("Set adapters[0].config.base_url to where this API is actually deployed/running.")

    config = {
        "server": {"name": "legacy-mcp"},
        "adapters": [
            {
                "id": "legacy",
                "type": "rest",
                "config": {
                    "base_url": resolved_base_url,
                    "allow_write_operations": False,
                    "endpoints": endpoints,
                },
            }
        ],
        "security": {"audit": {"enabled": True, "path": "./legacy2mcp-audit.log"}},
    }
    return config, todos
