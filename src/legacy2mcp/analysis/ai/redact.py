"""
Secret redaction by *content*, applied to every byte before it goes to a
model. (`discovery/redact.py` is a different policy: it decides by field
name which captured values never get persisted.)

Patterns cover well-known token formats, private keys, credentials embedded
in connection strings, bearer tokens, JWTs, and quoted values assigned to
secret-sounding names. False positives cost a little context; false
negatives leak a credential, so the patterns lean broad.
"""

from __future__ import annotations

import re

PLACEHOLDER = "[REDACTED]"

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("stripe_key", re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{16,}=*")),
    # scheme://user:password@host -> keep scheme and host, drop the credentials
    ("connection_string", re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s:/@'\"]+:[^\s@'\"]+@")),
    # password = "hunter2", "apiKey": "abc...", SECRET_KEY='...'
    ("secret_assignment", re.compile(
        r"(?i)((?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|auth)"
        r"[\w\-]*[\"']?\s*[:=]\s*)([\"'])([^\"'\s]{6,})\2")),
]


def redact(text: str) -> tuple[str, int]:
    """Return (redacted text, number of secrets removed)."""
    count = 0

    def sub(kind: str):
        def _replace(m: re.Match[str]) -> str:
            nonlocal count
            count += 1
            if kind == "connection_string":
                return f"{m.group(1)}{PLACEHOLDER}@"
            if kind == "secret_assignment":
                return f"{m.group(1)}{m.group(2)}{PLACEHOLDER}{m.group(2)}"
            return PLACEHOLDER
        return _replace

    for kind, pattern in _PATTERNS:
        text = pattern.sub(sub(kind), text)
    return text, count
