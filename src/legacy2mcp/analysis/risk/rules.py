"""
Rule-based risk classification for flow steps and whole flows.

Three levels, from the plan: read-only, low-risk write, and high-risk
(payments, deletes, external sends, auth changes). Rules run first and
offline; AI enrichment (M5) may add reasoning text but never lowers a
rule-assigned level.
"""

from __future__ import annotations

import re

from legacy2mcp.analysis.model import RISK_LEVELS, EntryPoint, RiskReason, SideEffect
from legacy2mcp.discovery.heuristics import looks_like_write

READ_ONLY, LOW, HIGH = RISK_LEVELS

DELETE_VERBS = {
    "delete", "destroy", "remove", "deletemany", "deleteone", "delete_one", "delete_many", "del", "truncate",
    "drop", "findbyidanddelete", "findoneanddelete", "find_one_and_delete", "unlink", "unlinksync", "rm",
    "rmsync", "rmtree", "rmdir", "rmdirsync", "bulk_delete",
}
_AUTH_MIDDLEWARE_RE = re.compile(
    r"(auth|login|jwt|token|permission|session|protect|guard|require_?user|verify|passport|current_user|admin)",
    re.IGNORECASE,
)
# Decorators/middleware that switch authentication *off* or aren't about it:
# @login_not_required, @permission_classes([AllowAny]), @csrf_exempt, ...
_NOT_AUTH_MIDDLEWARE_RE = re.compile(
    r"(not_?required|exempt|allow_?any|public|anonymous|skip_?auth|no_?auth|optional|csrf)",
    re.IGNORECASE,
)


def _middleware_name(qualified: str) -> str:
    """The middleware's own name, without the module it came from: only that
    part says what it does (`myapp.auth.cache_page` is not an auth check)."""
    module, _, name = qualified.rpartition(".")
    if name == "default" and module:  # JS default import: the module is the name
        name = module.rsplit("/", 1)[-1]
    return name


def is_auth_middleware(qualified: str) -> bool:
    name = _middleware_name(qualified)
    return bool(_AUTH_MIDDLEWARE_RE.search(name)) and not _NOT_AUTH_MIDDLEWARE_RE.search(name)
_HTTP_WRITE_METHODS = {"POST", "PUT", "PATCH"}
# Routes that are public by design: asking them to require authentication is wrong advice.
PUBLIC_AUTH_ROUTE_RE = re.compile(
    r"/(login|logout|signin|sign-in|signup|sign-up|register|forgot[-_]?password|reset[-_]?password\w*|"
    r"password[-_]?reset|oauth|callback|verify[-_]?email|confirm)(/|$|\{)", re.IGNORECASE)


# Auth reads that verify the *caller* (a token or session), as opposed to a
# credential check that is itself the login (check_password, bcrypt.compare).
CALLER_AUTH_VERBS = {"verify", "decode", "get_jwt_identity", "verify_jwt_in_request", "getServerSession",
                     "getSession", "auth", "currentUser", "requireAuth"}


def verifies_caller(effect: SideEffect) -> bool:
    return effect.kind == "auth" and effect.op == "read" and effect.verb in CALLER_AUTH_VERBS


def max_level(*levels: str) -> str:
    ranked = [lvl for lvl in levels if lvl in RISK_LEVELS]
    return max(ranked, key=RISK_LEVELS.index) if ranked else READ_ONLY


def is_delete(effect: SideEffect) -> bool:
    return effect.verb.lower() in DELETE_VERBS


def classify_effect(effect: SideEffect, resource_label: str = "") -> tuple[str, str | None]:
    """(risk level, reason text or None for read-only) for one side effect."""
    kind, op = effect.kind, effect.op
    target = resource_label or effect.detail or kind
    if op == "read":
        return READ_ONLY, None
    if kind == "payment":
        return HIGH, f"moves money through {effect.detail or 'a payment provider'} ({effect.call})"
    if kind == "auth":
        return HIGH, f"changes credentials or issues tokens ({effect.call})"
    if kind == "email":
        return HIGH, "sends email outside the system"
    if kind == "http_out":
        if effect.detail.startswith("/"):
            return LOW, "calls the app's own API"
        if op == "unknown":
            return LOW, f"outbound HTTP call with an unknown method ({effect.call})"
        return HIGH, f"sends data to an external service ({target})"
    if kind == "db":
        if op == "unknown":
            return LOW, "runs SQL that couldn't be classified as read or write"
        if is_delete(effect):
            return HIGH, f"deletes {target} records"
        if effect.verb in ("commit", "flush") and not effect.detail:
            return LOW, "commits database changes"
        return LOW, f"writes {target}"
    if kind == "queue":
        return LOW, f"enqueues background work ({target})"
    if kind == "file":
        if is_delete(effect):
            return LOW, "deletes local files"
        return LOW, f"writes files ({target})" if effect.detail else "writes files"
    return LOW, f"{kind} {op}"


def entry_reasons(entry: EntryPoint, level_so_far: str, handler_name: str) -> list[RiskReason]:
    """Rules that come from the entry point itself rather than any one step."""
    reasons: list[RiskReason] = []
    where = f"{entry.file}:{entry.line}"
    if entry.kind == "http_route":
        if entry.method == "DELETE":
            reasons.append(RiskReason(HIGH, "HTTP DELETE endpoint", where))
        elif entry.method in _HTTP_WRITE_METHODS and level_so_far == READ_ONLY:
            reasons.append(RiskReason(LOW, f"{entry.method} endpoint, but no write was detected -- verify", where))
    if level_so_far == READ_ONLY and looks_like_write(handler_name.split(".")[-1].strip("<>")):
        reasons.append(RiskReason(LOW, f"handler name suggests a write ({handler_name})", where))
    return reasons


def is_authenticated(entry: EntryPoint, auth_read_in_flow: bool) -> bool | None:
    """True if middleware/decorators or a token/session check inside the flow
    suggest the caller is authenticated; False if neither is visible; None
    when the question doesn't apply (CLI commands, jobs, message handlers,
    and public sign-in/sign-up/password-reset routes)."""
    if entry.kind != "http_route":
        return None
    if any(is_auth_middleware(m) for m in entry.middleware) or auth_read_in_flow:
        return True
    if PUBLIC_AUTH_ROUTE_RE.search(entry.path or ""):
        return None  # sign-in / sign-up / reset endpoints are public by design
    return False
