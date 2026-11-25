"""
Cross-module name resolution shared by the pipeline and the graph builder.

Analyzers record calls the way they were written, qualified through the
file's imports -- `shop.services.payments.charge_card` (Python) or
`src/services/payments.chargeCard` (JS). This turns such a name back into a
function key (`<file>::<qualname>`) when it points into the scanned repo,
and returns None for anything external (a package, a builtin) or ambiguous.
"""

from __future__ import annotations

from legacy2mcp.analysis.model import Class, Function, Module

_ASYNC_SUFFIXES = (".delay", ".apply_async", ".send_task")


class Resolver:
    def __init__(self, modules: list[Module]):
        self.modules = modules
        self.by_name: dict[str, Module] = {}
        self.functions: dict[str, tuple[Module, Function]] = {}
        self.classes: dict[str, tuple[Module, Class]] = {}
        for m in modules:
            for alias in self._aliases(m):
                self.by_name.setdefault(alias, m)
            for f in m.functions:
                self.functions[f"{m.path}::{f.qualname}"] = (m, f)
            for c in m.classes:
                self.classes[f"{m.path}::{c.name}"] = (m, c)

    @staticmethod
    def _aliases(m: Module) -> list[str]:
        names = [m.name] if m.name else []
        if m.language != "python" and m.name:
            if m.name.endswith("/index"):
                names.append(m.name[: -len("/index")])
        return names

    def module(self, spec: str) -> Module | None:
        """A module by name, trying JS path aliases (`@/x`, `~/x`)."""
        found = self.by_name.get(spec)
        if found is not None:
            return found
        for prefix in ("@/", "~/"):
            if spec.startswith(prefix):
                rest = spec[len(prefix):]
                for candidate in (rest, f"src/{rest}"):
                    if candidate in self.by_name:
                        return self.by_name[candidate]
        return None

    def split(self, qualified: str) -> tuple[Module, str] | None:
        """Longest module prefix of `qualified`, plus the remaining attribute path."""
        whole = self.module(qualified)
        if whole is not None:
            return whole, ""
        parts = qualified.split(".")
        for i in range(len(parts) - 1, 0, -1):
            mod = self.module(".".join(parts[:i]))
            if mod is not None:
                return mod, ".".join(parts[i:])
        return None

    def _in_module(self, mod: Module, attr: str) -> str | None:
        if not attr:
            attr = "default"
        head, _, rest = attr.partition(".")
        local = mod.exports.get(head, head) if mod.exports else head
        if mod.language != "python" and head == "default" and "default" not in mod.exports:
            return None
        qualname = f"{local}.{rest}" if rest else local
        key = f"{mod.path}::{qualname}"
        if key in self.functions:
            return key
        return None

    def function(self, name: str, caller_module: Module | None = None, caller_qualname: str = "") -> str | None:
        """Resolve a call name to `<file>::<qualname>`, or None."""
        if "()" in name or "[]" in name:
            return None
        if "::" in name:
            return name if name in self.functions else None
        if caller_module is not None:
            if "." not in name:
                key = f"{caller_module.path}::{name}"
                if key in self.functions:
                    return key
            for prefix in ("self.", "this.", "cls."):
                if name.startswith(prefix) and "." in caller_qualname:
                    cls = caller_qualname.split(".")[0]
                    key = f"{caller_module.path}::{cls}.{name[len(prefix):]}"
                    if key in self.functions:
                        return key
        found = self.split(name)
        if found is None:
            # A local class used qualified by its own module path is the only other case.
            return None
        mod, attr = found
        return self._in_module(mod, attr)

    def async_target(self, name: str, caller_module: Module | None = None) -> str | None:
        """`sync_inventory.delay` -> the `sync_inventory` function (enqueued, not called)."""
        for suffix in _ASYNC_SUFFIXES:
            if name.endswith(suffix):
                return self.function(name[: -len(suffix)], caller_module)
        return None

    def cls(self, name: str) -> tuple[Module, Class] | None:
        if "::" in name:
            return self.classes.get(name)
        found = self.split(name)
        if found is None:
            return None
        mod, attr = found
        return self.classes.get(f"{mod.path}::{mod.exports.get(attr, attr) if mod.exports else attr}")
