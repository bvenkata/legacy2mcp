"""
Audit logging.

v0.1 keeps this intentionally simple: one JSON line per tool call,
appended to a local file. That's enough to answer "who called what,
when, with what arguments, and did it succeed" during an incident --
which is the actual question audit logs get used to answer in
practice. OpenTelemetry export and pluggable sinks (stdout, OTLP) are
straightforward additions later (see docs/roadmap.md) but add
dependencies and configuration surface this MVP doesn't need yet.

No authentication/identity system ships in v0.1 (see docs/roadmap.md),
so `identity` is best-effort: the MCP transport-level caller info if
the running MCP SDK version exposes it, else "unknown".
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class AuditLogger:
    def __init__(self, path: str, enabled: bool = True):
        self.path = Path(path)
        self.enabled = enabled
        self._lock = threading.Lock()
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, record: dict[str, Any]) -> None:
        if not self.enabled:
            return
        line = json.dumps(record, default=str)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def log_call_start(self, tool_name: str, arguments: dict[str, Any], identity: str = "unknown") -> float:
        started_at = time.time()
        self._write(
            {
                "event": "tool_call_start",
                "tool": tool_name,
                "identity": identity,
                "arguments": arguments,
                "timestamp": started_at,
            }
        )
        return started_at

    def log_call_success(self, tool_name: str, started_at: float, identity: str = "unknown") -> None:
        now = time.time()
        self._write(
            {
                "event": "tool_call_success",
                "tool": tool_name,
                "identity": identity,
                "duration_ms": round((now - started_at) * 1000, 2),
                "timestamp": now,
            }
        )

    def log_call_error(self, tool_name: str, started_at: float, error: str, identity: str = "unknown") -> None:
        now = time.time()
        self._write(
            {
                "event": "tool_call_error",
                "tool": tool_name,
                "identity": identity,
                "error": error,
                "duration_ms": round((now - started_at) * 1000, 2),
                "timestamp": now,
            }
        )
