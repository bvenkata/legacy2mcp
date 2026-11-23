"""
Python analyzer for `legacy2mcp analyze`, built on the standard library's
`ast` module. Nothing is imported or executed from the scanned repo.

Per file it extracts imports (alias -> fully qualified name), functions and
methods with their call sites, classes, side effects at each call site
(database, outbound HTTP, email, payment, queue, file, auth), and entry
points: Flask/FastAPI route decorators, Django URLconfs, click/typer
commands, Celery tasks, APScheduler jobs and Django management commands.

Static analysis is best effort. A call through a variable whose type isn't
visible locally (`client.get(...)` where `client` came from elsewhere) is
recorded as a call but not tagged as a side effect, rather than guessed.
"""

from __future__ import annotations

import ast
import re

from legacy2mcp.analysis.analyzers.base import FileResult, Mount
from legacy2mcp.analysis.analyzers.common import sql_effect
from legacy2mcp.analysis.model import Class, EntryPoint, Function, MethodBranch, Module, Param, SideEffect
from legacy2mcp.analysis.walk import SourceFile

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_SKIP_PARAMS = {"self", "cls", "request", "req", "response", "res"}
_FLASK_PATH_RE = re.compile(r"<(?:[\w]+:)?(\w+)>")
_DJANGO_REGEX_GROUP_RE = re.compile(r"\(\?P<(\w+)>[^)]*\)")

_HTTP_LIBS = ("requests.", "httpx.", "aiohttp.", "urllib.request.", "urllib3.")
_HTTP_READ_VERBS = {"get", "head", "options", "urlopen"}
_PAYMENT_LIBS = ("stripe.", "braintree.", "paypalrestsdk.", "square.", "adyen.", "razorpay.")
_PAYMENT_READ_VERBS = {"retrieve", "list", "search", "find", "get"}
_EMAIL_LIBS = ("smtplib.", "django.core.mail.", "sendgrid.", "flask_mail.", "yagmail.")
_EMAIL_CALLS = {"send_mail", "sendmail", "send_email", "send_mass_mail", "send_templated_mail"}
_QUEUE_CALLS = {"delay", "apply_async", "send_task", "basic_publish", "enqueue", "enqueue_call", "enqueue_in"}
_AUTH_WRITE = {
    "set_password", "login_user", "logout_user", "create_access_token", "create_refresh_token",
    "grant_permission", "add_permission", "set_unusable_password",
}
_AUTH_WRITE_QUALIFIED = {"django.contrib.auth.login", "django.contrib.auth.logout", "jwt.encode"}
_AUTH_READ = {"check_password", "authenticate", "verify_password", "get_jwt_identity"}
_AUTH_READ_QUALIFIED = {"jwt.decode"}
_SQLA_SESSION_WRITE = {"add", "add_all", "delete", "merge", "commit", "flush", "bulk_save_objects"}
_DJANGO_ORM_WRITE = {
    "create", "update", "delete", "bulk_create", "bulk_update", "get_or_create", "update_or_create",
}
_MONGO_WRITE = {
    "insert_one", "insert_many", "update_one", "update_many", "delete_one", "delete_many",
    "replace_one", "find_one_and_update", "find_one_and_delete",
}
_FILE_WRITE_QUALIFIED = {"os.remove", "os.unlink", "os.rename", "os.makedirs", "shutil.rmtree", "shutil.move", "shutil.copy"}
_FILE_WRITE_METHODS = {"write_text", "write_bytes", "unlink", "rmdir"}
_REQUEST_CONTAINERS = {
    "request.args": "query", "request.GET": "query", "request.query_params": "query",
    "request.form": "form", "request.POST": "form", "request.data": "body",
}
_DB_RECEIVER_HINT = re.compile(r"(cursor|conn|connection|session|db|engine)", re.IGNORECASE)


def module_name_for(rel: str) -> str:
    parts = rel[: -len(".py")].split("/") if rel.endswith(".py") else rel.split("/")
    if parts and parts[0] == "src" and len(parts) > 1:
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def dotted(node: ast.AST) -> str | None:
    """Render a call target as written: `a.b().c[]`. None for anything exotic."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    if isinstance(node, ast.Call):
        base = dotted(node.func)
        return f"{base}()" if base else None
    if isinstance(node, ast.Subscript):
        base = dotted(node.value)
        return f"{base}[]" if base else None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "''"
    return None


def _str_const(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _str_template(node: ast.AST | None) -> str | None:
    """A string literal, or an f-string with each `{...}` part shown as `{}`
    (keeps the scheme and host of f"https://api.example.com/{path}")."""
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}" for v in node.values)
    # "https://host/path?x={}".format(...)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return _str_const(node.func.value)
    return _str_const(node)


def _first_doc_line(node: ast.AST) -> str:
    doc = ast.get_docstring(node)  # type: ignore[arg-type]
    return doc.strip().splitlines()[0].strip() if doc and doc.strip() else ""


def _segments(name: str) -> list[str]:
    return [s.rstrip("()[]") for s in name.split(".")]


class _Imports:
    def __init__(self, module_name: str, is_package: bool):
        self.module_name = module_name
        self.is_package = is_package
        self.map: dict[str, str] = {}

    def _resolve_relative(self, level: int, module: str | None) -> str:
        package = self.module_name.split(".") if self.is_package else self.module_name.split(".")[:-1]
        if level > 1:
            package = package[: len(package) - (level - 1)]
        base = ".".join(p for p in package if p)
        if module:
            return f"{base}.{module}" if base else module
        return base

    def add(self, node: ast.Import | ast.ImportFrom) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    self.map[alias.asname] = alias.name
                else:
                    head = alias.name.split(".")[0]
                    self.map[head] = head
            return
        source = self._resolve_relative(node.level, node.module) if node.level else (node.module or "")
        for alias in node.names:
            if alias.name == "*":
                continue
            target = f"{source}.{alias.name}" if source else alias.name
            self.map[alias.asname or alias.name] = target

    def qualify(self, name: str) -> str:
        """`post` -> `requests.post` if `from requests import post`."""
        head, _, rest = name.partition(".")
        target = self.map.get(head.rstrip("()[]"))
        if target is None:
            return name
        suffix = head[len(head.rstrip("()[]")):]
        return f"{target}{suffix}.{rest}" if rest else f"{target}{suffix}"


def classify_call(call: ast.Call, name: str, imports: _Imports) -> SideEffect | None:
    """Tag one call site with a side-effect kind, or None if it has none we can see."""
    qualified = imports.qualify(name)
    segs = _segments(qualified)
    last = segs[-1]
    receiver = ".".join(segs[:-1])
    line = call.lineno

    def effect(kind: str, op: str, detail: str | None = "", verb: str | None = None) -> SideEffect:
        return SideEffect(kind=kind, op=op, call=qualified, line=line, detail=detail or "",
                          verb=verb if verb is not None else qualified.split(".")[-1].rstrip("()[]"))

    if qualified.startswith(_PAYMENT_LIBS):
        return effect("payment", "read" if last in _PAYMENT_READ_VERBS else "write", segs[0])

    if qualified.startswith(_EMAIL_LIBS) or last in _EMAIL_CALLS or (last == "send" and "mail" in receiver.lower()):
        return effect("email", "write")

    if last in _QUEUE_CALLS or (last == "send_message" and re.search(r"sqs|queue", receiver, re.I)):
        return effect("queue", "write", receiver.split(".")[-1] if last in {"delay", "apply_async"} else "")

    if qualified.startswith(_HTTP_LIBS):
        url = _str_template(call.args[0]) if call.args else None
        if last == "request" and call.args:
            verb = (_str_const(call.args[0]) or "").lower()
            url = _str_const(call.args[1]) if len(call.args) > 1 else None
            op = "read" if verb in _HTTP_READ_VERBS else ("write" if verb else "unknown")
        else:
            op = "read" if last in _HTTP_READ_VERBS else ("write" if last in HTTP_METHODS else "unknown")
        return effect("http_out", op, url or "")

    if qualified in _AUTH_WRITE_QUALIFIED or last in _AUTH_WRITE:
        return effect("auth", "write")
    if qualified in _AUTH_READ_QUALIFIED or last in _AUTH_READ:
        return effect("auth", "read")

    # Django ORM: Model.objects.<op>(...)
    if "objects" in segs:
        idx = segs.index("objects")
        model = segs[idx - 1] if idx > 0 else ""
        ops = segs[idx + 1:]
        op = "write" if any(o in _DJANGO_ORM_WRITE for o in ops) else "read"
        return effect("db", op, model)
    # SQLAlchemy session / Flask-SQLAlchemy
    if "session" in segs and last in _SQLA_SESSION_WRITE:
        return effect("db", "write")
    if "session" in segs and last in {"query", "get", "scalars", "scalar"}:
        return effect("db", "read", _str_const(call.args[0]) or (dotted(call.args[0]) or "") if call.args else "")
    if "query" in segs and segs.index("query") > 0:
        model = segs[segs.index("query") - 1]
        return effect("db", "read", "" if model == "session" else model)
    if last in _MONGO_WRITE:
        return effect("db", "write", segs[-2] if len(segs) > 1 else "")
    if last in {"execute", "executemany"} and _DB_RECEIVER_HINT.search(receiver):
        sql = _str_const(call.args[0]) if call.args else None
        parsed = sql_effect(sql) if sql else None
        if parsed:
            return effect("db", parsed[0], parsed[1], verb=parsed[2])
        return effect("db", "unknown")
    if last in {"save", "delete"} and receiver and call.args == [] and not receiver.startswith(("self", "os", "Path")):
        owner = receiver.split(".")[-1]
        return effect("db", "write", owner if owner[:1].isupper() else "")

    # Files
    if qualified == "open" or last == "open" and receiver in {"io", "builtins"}:
        mode = _str_const(call.args[1]) if len(call.args) > 1 else None
        for kw in call.keywords:
            if kw.arg == "mode":
                mode = _str_const(kw.value)
        if mode and any(c in mode for c in "wax+"):
            return effect("file", "write", _str_const(call.args[0]) if call.args else "")
        return None
    if qualified in _FILE_WRITE_QUALIFIED or (last in _FILE_WRITE_METHODS and receiver):
        return effect("file", "write")

    return None


class _FunctionVisitor(ast.NodeVisitor):
    """Collect calls inside one function body, including nested defs/lambdas."""

    def __init__(self, imports: _Imports):
        self.imports = imports
        self.calls: set[str] = set()
        self.effects: list[SideEffect] = []
        self.request_methods: set[str] = set()
        self.request_params: dict[str, Param] = {}
        self.call_sites: dict[str, int] = {}
        self.branches: list[MethodBranch] = []
        self._chained: set[int] = set()

    def visit_Call(self, node: ast.Call) -> None:
        name = dotted(node.func)
        if name:
            qualified_name = self.imports.qualify(name)
            self.calls.add(qualified_name)
            self.call_sites.setdefault(qualified_name, node.lineno)
            # `Order.query.filter_by(...).all()`: tag only the outermost call of a
            # chain that matches a rule, so one statement doesn't count as several
            # side effects -- but fall through to inner calls when the outer one
            # (`.first()` on `session.execute(...)`) matches nothing.
            effect = None
            if id(node) not in self._chained:
                effect = classify_call(node, name, self.imports)
                if effect:
                    self.effects.append(effect)
            suppress_inner = effect is not None or id(node) in self._chained
            inner = node.func.value if suppress_inner and isinstance(node.func, ast.Attribute) else None
            while isinstance(inner, (ast.Call, ast.Attribute, ast.Subscript)):
                if isinstance(inner, ast.Call):
                    self._chained.add(id(inner))
                    inner = inner.func
                else:
                    inner = inner.value
            self._request_param(node, name)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # request.args["x"] / request.POST["x"] -> required input
        container = dotted(node.value) or ""
        key = _str_const(node.slice)
        location = _REQUEST_CONTAINERS.get(container)
        if location and key and key not in self.request_params:
            self.request_params[key] = Param(name=key, required=True, location=location)
        self.generic_visit(node)

    def _request_param(self, node: ast.Call, name: str) -> None:
        # request.args.get("x") / request.GET.get("x", default) -> optional input
        container, _, method = name.rpartition(".")
        location = _REQUEST_CONTAINERS.get(container)
        if location and method in {"get", "getlist"} and node.args:
            key = _str_const(node.args[0])
            if key and key not in self.request_params:
                self.request_params[key] = Param(name=key, required=False, location=location)

    def visit_If(self, node: ast.If) -> None:
        # `if request.method == "POST":` -> the body only runs for POST.
        test = node.test
        # Flask-WTF: `if form.validate_on_submit():` only passes on a POST.
        if isinstance(test, ast.Call) and (dotted(test.func) or "").endswith(".validate_on_submit") and node.body:
            start, end = node.body[0].lineno, node.body[-1].end_lineno or node.body[-1].lineno
            self.branches.append(MethodBranch(method="POST", start=start, end=end))
            self.request_methods.add("POST")
        elif isinstance(test, ast.Compare) and dotted(test.left) == "request.method" and node.body \
                and len(test.ops) == 1 and isinstance(test.ops[0], (ast.Eq, ast.In)):
            comp = test.comparators[0]
            values = comp.elts if isinstance(comp, (ast.Tuple, ast.List, ast.Set)) else [comp]
            start, end = node.body[0].lineno, node.body[-1].end_lineno or node.body[-1].lineno
            for v in values:
                s = _str_const(v)
                if s:
                    self.branches.append(MethodBranch(method=s.upper(), start=start, end=end))
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        # Django function views branching on `request.method == "POST"`.
        if dotted(node.left) == "request.method":
            for comp in node.comparators:
                values = comp.elts if isinstance(comp, (ast.Tuple, ast.List, ast.Set)) else [comp]
                for v in values:
                    s = _str_const(v)
                    if s:
                        self.request_methods.add(s.upper())
        self.generic_visit(node)


def _params(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[Param]:
    args = func.args
    positional = args.posonlyargs + args.args
    defaults: list[ast.AST | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    pairs = list(zip(positional, defaults)) + list(zip(args.kwonlyargs, args.kw_defaults))
    params = []
    for arg, default in pairs:
        if arg.arg in _SKIP_PARAMS:
            continue
        if isinstance(default, ast.Call) and (dotted(default.func) or "").split(".")[-1] == "Depends":
            continue  # FastAPI dependency injection, not a caller-supplied input
        params.append(
            Param(
                name=arg.arg,
                type=ast.unparse(arg.annotation) if arg.annotation is not None else None,
                required=default is None,
            )
        )
    return params


_REQUIRE_DECORATORS = {"require_GET": {"GET"}, "require_POST": {"POST"}, "require_safe": {"GET", "HEAD"}}


def _view_decorator_methods(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Methods pinned by Django's require_* decorators or DRF's @api_view([...])."""
    methods: set[str] = set()
    for dec in func.decorator_list:
        call = dec if isinstance(dec, ast.Call) else None
        last = (dotted(call.func if call else dec) or "").split(".")[-1]
        if last in _REQUIRE_DECORATORS:
            methods |= _REQUIRE_DECORATORS[last]
        elif last in {"require_http_methods", "api_view"} and call and call.args:
            arg = call.args[0]
            if isinstance(arg, (ast.List, ast.Tuple)):
                methods |= {m.upper() for m in (_str_const(e) for e in arg.elts) if m}
    return methods


def normalize_path(raw: str) -> str:
    path = _FLASK_PATH_RE.sub(r"{\1}", raw)
    return path if path.startswith("/") else "/" + path


def django_route(raw: str, regex: bool) -> str:
    if regex:
        raw = _DJANGO_REGEX_GROUP_RE.sub(r"<\1>", raw).lstrip("^").rstrip("$")
    return _FLASK_PATH_RE.sub(r"{\1}", raw)


def join_route(prefix: str, route: str) -> str:
    joined = prefix.rstrip("/") + "/" + route.lstrip("/") if prefix else route
    return joined if joined.startswith("/") else "/" + joined


class PythonAnalyzer:
    languages = ("python",)

    def analyze(self, source: SourceFile, text: str) -> FileResult:
        name = module_name_for(source.rel)
        module = Module(path=source.rel, language="python", name=name, lines=text.count("\n") + (0 if text.endswith("\n") or not text else 1))
        result = FileResult(module=module)
        try:
            tree = ast.parse(text, filename=source.rel)
        except (SyntaxError, ValueError) as exc:
            module.parse_error = f"{type(exc).__name__}: {exc}"
            return result

        imports = _Imports(name, source.rel.endswith("__init__.py"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.add(node)
        module.imports = dict(sorted(imports.map.items()))
        result.frameworks |= self._frameworks(imports)

        prefixes = self._router_prefixes(tree, imports)

        def add_function(node: ast.FunctionDef | ast.AsyncFunctionDef, qualname: str) -> None:
            visitor = _FunctionVisitor(imports)
            for stmt in node.body:
                visitor.visit(stmt)
            http_methods = set(visitor.request_methods)
            if http_methods:
                http_methods.add("GET")  # the non-POST branch of `if request.method == "POST"`
            http_methods |= _view_decorator_methods(node)
            module.functions.append(
                Function(
                    qualname=qualname,
                    line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    params=_params(node) + [p for n, p in sorted(visitor.request_params.items())
                                            if n not in {a.arg for a in node.args.args + node.args.kwonlyargs}],
                    decorators=[imports.qualify(d) for d in (dotted(dec.func if isinstance(dec, ast.Call) else dec) for dec in node.decorator_list) if d],
                    calls=sorted(visitor.calls),
                    side_effects=sorted(visitor.effects, key=lambda e: (e.line, e.kind, e.call)),
                    docstring=_first_doc_line(node),
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                    http_methods=sorted(http_methods),
                    call_sites=dict(sorted(visitor.call_sites.items())),
                    method_branches=sorted(visitor.branches, key=lambda b: (b.start, b.method)),
                )
            )
            result.entry_points.extend(self._decorator_entry_points(node, qualname, source.rel, imports, prefixes))

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                add_function(node, node.name)
            elif isinstance(node, ast.ClassDef):
                methods = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append(item.name)
                        add_function(item, f"{node.name}.{item.name}")
                bases = [imports.qualify(b) for b in (dotted(b) for b in node.bases) if b]
                module.classes.append(
                    Class(name=node.name, line=node.lineno, bases=bases, methods=methods, docstring=_first_doc_line(node))
                )
                if "/management/commands/" in f"/{source.rel}" and node.name == "Command" and "handle" in methods:
                    command = source.rel.rsplit("/", 1)[-1][: -len(".py")]
                    result.entry_points.append(
                        EntryPoint(
                            kind="cli_command", framework="django", handler=f"{source.rel}::Command.handle",
                            file=source.rel, line=node.lineno, name=f"manage.py {command}",
                        )
                    )
                    result.frameworks.add("django")

        if source.rel.endswith("urls.py"):
            eps, mounts = self._django_urls(tree, source.rel, imports)
            result.entry_points.extend(eps)
            result.mounts.extend(mounts)
            if eps or mounts:
                result.frameworks.add("django")

        result.entry_points.extend(self._call_entry_points(tree, source.rel, imports))
        return result

    # -- frameworks ---------------------------------------------------------

    @staticmethod
    def _frameworks(imports: _Imports) -> set[str]:
        found = set()
        targets = set(imports.map.values())
        for fw in ("flask", "fastapi", "django", "celery", "click", "typer", "apscheduler"):
            if any(t == fw or t.startswith(fw + ".") for t in targets):
                found.add(fw)
        if "flask" not in found and any(t.startswith("flask_") for t in targets):
            found.add("flask")
        return found

    @staticmethod
    def _router_prefixes(tree: ast.Module, imports: _Imports) -> dict[str, str]:
        """`bp = Blueprint(..., url_prefix="/api")` / `router = APIRouter(prefix="/api")`."""
        prefixes: dict[str, str] = {}
        for node in tree.body:
            if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and len(node.targets) == 1):
                continue
            target = node.targets[0]
            ctor = imports.qualify(dotted(node.value.func) or "").split(".")[-1]
            if not isinstance(target, ast.Name) or ctor not in {"Blueprint", "APIRouter"}:
                continue
            for kw in node.value.keywords:
                if kw.arg in {"url_prefix", "prefix"} and _str_const(kw.value):
                    prefixes[target.id] = _str_const(kw.value) or ""
        return prefixes

    # -- entry points ---------------------------------------------------------

    def _decorator_entry_points(self, func, qualname, rel, imports, prefixes) -> list[EntryPoint]:
        eps: list[EntryPoint] = []
        handler = f"{rel}::{qualname}"
        params = _params(func)
        for dec in func.decorator_list:
            call = dec if isinstance(dec, ast.Call) else None
            raw_name = dotted(call.func if call else dec)
            if not raw_name:
                continue
            qualified = imports.qualify(raw_name)
            segs = qualified.split(".")
            attr, receiver = segs[-1], raw_name.rsplit(".", 1)[0] if "." in raw_name else ""

            # Flask / FastAPI routes
            if call and attr in ("route", *HTTP_METHODS) and receiver and call.args and _str_const(call.args[0]) is not None:
                raw_path = _str_const(call.args[0]) or ""
                if attr == "route":
                    methods = ["GET"]
                    for kw in call.keywords:
                        if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                            methods = [m.upper() for m in (_str_const(e) for e in kw.value.elts) if m] or ["GET"]
                else:
                    methods = [attr.upper()]
                framework = "fastapi" if any(t.startswith("fastapi") for t in imports.map.values()) else "flask"
                path = normalize_path(join_route(prefixes.get(receiver, ""), raw_path))
                for method in methods:
                    eps.append(
                        EntryPoint(
                            kind="http_route", framework=framework, handler=handler, file=rel, line=func.lineno,
                            name=f"{method} {path}", method=method, path=path, params=params,
                        )
                    )
                continue

            # Django REST framework / django.views.decorators method restrictions are
            # applied in _django_urls via func_methods; nothing to emit here.

            # click / typer
            if attr == "command" and any(t.startswith(("click", "typer")) for t in imports.map.values()):
                fw = "typer" if any(t.startswith("typer") for t in imports.map.values()) else "click"
                cmd = (_str_const(call.args[0]) if call and call.args else None) or func.name.replace("_", "-")
                eps.append(
                    EntryPoint(kind="cli_command", framework=fw, handler=handler, file=rel, line=func.lineno,
                               name=f"cli {cmd}", params=params)
                )
                continue

            # Celery tasks are consumed from a broker queue -> message handlers.
            if qualified in {"celery.shared_task"} or (attr == "task" and receiver and "celery" in {t.split(".")[0] for t in imports.map.values()}):
                eps.append(
                    EntryPoint(kind="message_handler", framework="celery", handler=handler, file=rel, line=func.lineno,
                               name=f"task {func.name}", params=params)
                )
                continue
            if attr in {"periodic_task", "scheduled_job"}:
                eps.append(
                    EntryPoint(kind="scheduled_job", framework="celery" if attr == "periodic_task" else "apscheduler",
                               handler=handler, file=rel, line=func.lineno, name=f"job {func.name}")
                )
        return eps

    def _call_entry_points(self, tree: ast.Module, rel: str, imports: _Imports) -> list[EntryPoint]:
        """Entry points registered by a call rather than a decorator."""
        eps: list[EntryPoint] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = dotted(node.func) or ""
            last = name.split(".")[-1]
            # scheduler.add_job(func, "cron", ...)
            if last == "add_job" and node.args:
                target = dotted(node.args[0])
                if target and "()" not in target:
                    eps.append(EntryPoint(kind="scheduled_job", framework="apscheduler",
                                          handler=imports.qualify(target) if "." in target or target in imports.map else f"{rel}::{target}",
                                          file=rel, line=node.lineno, name=f"job {target.split('.')[-1]}"))
            # schedule.every(...).<unit>.do(func)
            elif last == "do" and name.startswith("schedule.every") and node.args:
                target = dotted(node.args[0])
                if target:
                    eps.append(EntryPoint(kind="scheduled_job", framework="schedule",
                                          handler=imports.qualify(target) if target in imports.map else f"{rel}::{target}",
                                          file=rel, line=node.lineno, name=f"job {target.split('.')[-1]}"))
            # channel.basic_consume(queue=..., on_message_callback=func)
            elif last == "basic_consume":
                for kw in node.keywords:
                    if kw.arg == "on_message_callback":
                        target = dotted(kw.value)
                        if target:
                            eps.append(EntryPoint(kind="message_handler", framework="pika",
                                                  handler=imports.qualify(target) if target in imports.map else f"{rel}::{target}",
                                                  file=rel, line=node.lineno, name=f"consumer {target.split('.')[-1]}"))
        return eps

    def _django_urls(self, tree, rel, imports) -> tuple[list[EntryPoint], list[Mount]]:
        """Every path()/re_path() is emitted with method "ANY"; the pipeline
        narrows that once the view function (often in another module) is known."""
        eps: list[EntryPoint] = []
        mounts: list[Mount] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = imports.qualify(dotted(node.func) or "")
            last = fn.split(".")[-1]
            if last not in {"path", "re_path", "url"} or len(node.args) < 2:
                continue
            raw = _str_const(node.args[0])
            if raw is None:
                continue
            route = django_route(raw, regex=last != "path")
            view = node.args[1]

            # include("app.urls") / include(("app.urls", "ns"))
            if isinstance(view, ast.Call) and (dotted(view.func) or "").split(".")[-1] == "include" and view.args:
                arg = view.args[0]
                if isinstance(arg, (ast.Tuple, ast.List)) and arg.elts:
                    arg = arg.elts[0]
                target = _str_const(arg)
                if target is None and dotted(arg):
                    target = imports.qualify(dotted(arg) or "")
                if target:
                    mounts.append(Mount(framework="django", prefix=route, target=target, file=rel, line=node.lineno))
                continue

            # Class-based view: SomeView.as_view() -> expanded per HTTP method by the pipeline.
            if isinstance(view, ast.Call) and (dotted(view.func) or "").endswith(".as_view"):
                target = (dotted(view.func) or "")[: -len(".as_view")]
            else:
                target = dotted(view)
            if not target or "()" in target:
                continue
            local = target if "." not in target and target not in imports.map else None
            handler = f"{rel}::{local}" if local else imports.qualify(target)
            path = join_route("", route)
            eps.append(
                EntryPoint(kind="http_route", framework="django", handler=handler, file=rel, line=node.lineno,
                           name=f"ANY {path}", method="ANY", path=path)
            )
        return eps, mounts
