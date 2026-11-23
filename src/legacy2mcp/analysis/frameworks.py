"""Framework detection from manifest files (requirements, pyproject, package.json).

Analyzers also report frameworks they see imported; this covers the cases
where a manifest names one but the code we parsed doesn't (yet) import it
in a recognizable way, e.g. a Next.js app whose API routes are found by
file location rather than by an import.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PY_FRAMEWORKS = {"flask", "fastapi", "django", "celery"}
JS_FRAMEWORKS = {"express": "express", "next": "nextjs"}

_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")


def from_manifests(root: Path) -> set[str]:
    found: set[str] = set()
    for req in (root / "requirements.txt", root / "requirements.in"):
        if req.is_file():
            for line in req.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = _REQ_NAME_RE.match(line)
                if m and m.group(1).lower() in PY_FRAMEWORKS:
                    found.add(m.group(1).lower())
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8", errors="ignore").lower()
        for fw in PY_FRAMEWORKS:
            if re.search(rf"['\"]{fw}\b", text):
                found.add(fw)
    package_json = root / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            data = {}
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})} if isinstance(data, dict) else {}
        for dep, fw in JS_FRAMEWORKS.items():
            if dep in deps:
                found.add(fw)
    return found
