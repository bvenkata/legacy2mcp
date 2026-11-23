"""
JavaScript / TypeScript analyzer for `legacy2mcp analyze`, built on
tree-sitter. Nothing from the scanned repo is executed or installed.

Per file it extracts imports (ES modules and CommonJS `require`), exports,
top-level functions (declarations, arrow/function expressions assigned to a
name, class methods), each function's calls and side effects (db, outbound
HTTP, email, payment, queue, file, auth), and entry points:

- Express: `app.get(...)`, `router.post(...)`, `router.route("/x").get(...)`,
  with `app.use("/prefix", router)` recorded as a mount.
- Next.js API routes: `pages/api/**` default-export handlers and
  `app/**/route.(js|ts)` exported `GET`/`POST`/... functions.
- node-cron / node-schedule jobs, BullMQ workers, amqplib consumers,
  kafkajs consumers and commander CLI commands.

Local import specifiers ("./services/pay") are resolved to repo-relative
module names without extension ("src/services/pay"); package imports stay as
written ("stripe"). Linking those names to functions in other files happens
later, in `graph/resolve.py`.
"""

from __future__ import annotations

import posixpath
import re

from legacy2mcp.analysis.analyzers.base import FileResult, Mount
from legacy2mcp.analysis.analyzers.common import sql_effect
from legacy2mcp.analysis.model import Class, EntryPoint, Function, Module, Param, SideEffect
from legacy2mcp.analysis.walk import SourceFile

try:
    import tree_sitter
    import tree_sitter_javascript
    import tree_sitter_typescript
except ImportError:  # pragma: no cover - exercised only without the extra installed
    tree_sitter = None

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_ROUTER_METHODS = (*HTTP_METHODS, "all", "options", "head")
_JS_EXTS = (".tsx", ".ts", ".jsx", ".mjs", ".cjs", ".js")
_ROUTER_NAME_RE = re.compile(r"^(app|server|router|api|routes?|\w+Router)$")
_EXPRESS_PATH_PARAM_RE = re.compile(r":(\w+)\??")
_NEXT_SEGMENT_RE = re.compile(r"^\[(?:\.\.\.)?(\w+)\]$|^\[\[\.\.\.(\w+)\]\]$")
_REQ_NAMES = {"req", "request"}
_FUNCTION_TYPES = {"arrow_function", "function_expression", "function", "generator_function"}

_HTTP_PACKAGES = ("axios", "node-fetch", "got", "superagent", "ky", "undici", "http", "https", "cross-fetch")
_HTTP_READ = {"get", "head", "options"}
_PAYMENT_PACKAGES = ("stripe", "braintree", "@paypal/", "paypal-rest-sdk", "square", "razorpay", "@adyen/")
_PAYMENT_READ = {"retrieve", "list", "search", "get", "find"}
_EMAIL_PACKAGES = ("nodemailer", "@sendgrid/mail", "resend", "postmark", "mailgun", "mailgun.js", "@aws-sdk/client-ses", "sendmail")
_EMAIL_CALLS = {"sendMail", "sendEmail", "sendmail", "sendTemplate", "sendTransacEmail"}
_QUEUE_CALLS = {"sendToQueue", "sendMessage", "publishMessage"}
_PRISMA_READ = {"findMany", "findUnique", "findUniqueOrThrow", "findFirst", "findFirstOrThrow", "count", "aggregate", "groupBy"}
_PRISMA_WRITE = {"create", "createMany", "update", "updateMany", "upsert", "delete", "deleteMany"}
_ORM_READ = {"find", "findOne", "findById", "findAll", "findByPk", "findAndCountAll", "countDocuments", "aggregate", "findOneBy", "findBy"}
_ORM_WRITE = {
    "create", "insertMany", "updateOne", "updateMany", "deleteOne", "deleteMany", "findByIdAndUpdate",
    "findByIdAndDelete", "findOneAndUpdate", "findOneAndDelete", "destroy", "bulkCreate", "upsert", "insert", "remove",
}
_FS_WRITE = {
    "writeFile", "writeFileSync", "appendFile", "appendFileSync", "unlink", "unlinkSync", "rm", "rmSync",
    "rename", "renameSync", "mkdir", "mkdirSync", "rmdir", "rmdirSync", "copyFile", "copyFileSync", "createWriteStream",
}
_AUTH_WRITE = {"jwt.sign", "jsonwebtoken.sign", "bcrypt.hash", "bcryptjs.hash", "argon2.hash"}
_AUTH_READ = {"jwt.verify", "jsonwebtoken.verify", "bcrypt.compare", "bcryptjs.compare", "argon2.verify", "passport.authenticate"}
_JS_BUILTINS = {
    "Object", "Array", "Promise", "JSON", "Math", "Date", "Reflect", "Number", "String", "Map", "Set",
    "WeakMap", "Buffer", "URL", "Intl", "Symbol", "Error", "Response", "Request", "NextResponse", "Headers",
}
_DB_RECEIVER_RE = re.compile(r"(pool|client|db|conn|connection|knex|sql|database|pg|mysql)", re.IGNORECASE)


def available() -> bool:
    return tree_sitter is not None


def _text(node) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


def _line(node) -> int:
    return node.start_point[0] + 1


def _string_value(node) -> str | None:
    """Literal value of a string / template string without substitutions."""
    if node is None:
        return None
    if node.type == "string":
        return "".join(_text(c) for c in node.named_children if c.type in ("string_fragment", "escape_sequence"))
    if node.type == "template_string":
        if any(c.type == "template_substitution" for c in node.named_children):
            # `/api/${version}/x` -> "/api/{}/x": still useful for hosts and SQL tables.
            out = []
            for c in node.children:
                if c.type == "template_substitution":
                    out.append("{}")
                elif c.type in ("string_fragment", "escape_sequence"):
                    out.append(_text(c))
            return "".join(out)
        return "".join(_text(c) for c in node.named_children if c.type in ("string_fragment", "escape_sequence"))
    return None


def _dotted(node) -> str | None:
    """`a.b().c` style rendering of a callee / member expression."""
    if node is None:
        return None
    t = node.type
    if t in ("identifier", "property_identifier", "this", "super", "shorthand_property_identifier"):
        return _text(node)
    if t == "member_expression":
        obj = _dotted(node.child_by_field_name("object"))
        prop = node.child_by_field_name("property")
        return f"{obj}.{_text(prop)}" if obj and prop is not None else None
    if t == "call_expression":
        fn = _dotted(node.child_by_field_name("function"))
        return f"{fn}()" if fn else None
    if t == "await_expression":
        return _dotted(node.named_children[0]) if node.named_children else None
    if t == "parenthesized_expression":
        return _dotted(node.named_children[0]) if node.named_children else None
    if t == "subscript_expression":
        obj = _dotted(node.child_by_field_name("object"))
        return f"{obj}[]" if obj else None
    if t == "non_null_expression":
        return _dotted(node.named_children[0]) if node.named_children else None
    return None


def _args(call) -> list:
    args = call.child_by_field_name("arguments")
    return list(args.named_children) if args is not None else []


def _first_line_of_jsdoc(node) -> str:
    prev = node.prev_named_sibling
    if prev is not None and prev.type == "comment":
        raw = _text(prev)
        if raw.startswith("/**"):
            for line in raw[3:-2].splitlines():
                line = line.strip().lstrip("*").strip()
                if line and not line.startswith("@"):
                    return line
    return ""


def module_name_for(rel: str) -> str:
    for ext in _JS_EXTS:
        if rel.endswith(ext):
            return rel[: -len(ext)]
    return rel


def resolve_specifier(spec: str, importer_rel: str) -> str:
    """'./pay' from 'src/routes/x.js' -> 'src/routes/pay'; packages unchanged."""
    if spec.startswith("."):
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(importer_rel), spec))
        return module_name_for(joined)
    return module_name_for(spec) if spec.startswith(("@/", "~/")) else spec


class _Imports:
    def __init__(self, rel: str):
        self.rel = rel
        self.map: dict[str, str] = {}
        self.instances: dict[str, str] = {}  # var -> constructed-from, e.g. stripe -> "stripe()"
        self.local_modules: set[str] = set()  # resolved targets that are repo modules

    def _target(self, spec: str) -> str:
        target = resolve_specifier(spec, self.rel)
        if spec.startswith((".", "@/", "~/")):
            self.local_modules.add(target)
        return target

    def add_es(self, node) -> None:
        source = _string_value(node.child_by_field_name("source"))
        if source is None:
            return
        target = self._target(source)
        local = target in self.local_modules
        clause = next((c for c in node.named_children if c.type == "import_clause"), None)
        if clause is None:
            return
        for c in clause.named_children:
            if c.type == "identifier":  # default import
                self.map[_text(c)] = f"{target}.default" if local else target
            elif c.type == "namespace_import":
                ident = next((x for x in c.named_children if x.type == "identifier"), None)
                if ident is not None:
                    self.map[_text(ident)] = target
            elif c.type == "named_imports":
                for spec in c.named_children:
                    if spec.type != "import_specifier":
                        continue
                    name = spec.child_by_field_name("name")
                    alias = spec.child_by_field_name("alias")
                    self.map[_text(alias or name)] = f"{target}.{_text(name)}"

    def add_require(self, pattern, call) -> bool:
        """`const x = require("m")`, `const {a, b: c} = require("m")`,
        `const stripe = require("stripe")(key)`. Returns True if handled."""
        inner = call
        while inner is not None and inner.type == "call_expression" and _text(inner.child_by_field_name("function")) != "require":
            fn = inner.child_by_field_name("function")
            inner = fn if fn is not None and fn.type == "call_expression" else None
        if inner is None:
            return False
        args = _args(inner)
        spec = _string_value(args[0]) if args else None
        if spec is None:
            return False
        target = self._target(spec)
        if pattern.type == "identifier":
            self.map[_text(pattern)] = target
        elif pattern.type == "object_pattern":
            for prop in pattern.named_children:
                if prop.type == "shorthand_property_identifier_pattern":
                    self.map[_text(prop)] = f"{target}.{_text(prop)}"
                elif prop.type == "pair_pattern":
                    key, value = prop.child_by_field_name("key"), prop.child_by_field_name("value")
                    if key is not None and value is not None and value.type == "identifier":
                        self.map[_text(value)] = f"{target}.{_text(key)}"
        return True

    def is_imported(self, name: str) -> bool:
        return bool(name) and name.split(".")[0].rstrip("()[]") in self.map

    def qualify(self, name: str) -> str:
        head, sep, rest = name.partition(".")
        bare = head.rstrip("()[]")
        target = self.map.get(bare) or self.instances.get(bare)
        if target is None:
            return name
        return f"{target}{head[len(bare):]}{sep}{rest}"


def classify_js_call(call, name: str, qualified: str) -> SideEffect | None:
    segs = [s.rstrip("()[]") for s in qualified.split(".")]
    last = segs[-1]
    receiver = ".".join(segs[:-1])
    root = segs[0]
    args = _args(call)
    first = _string_value(args[0]) if args else None
    line = _line(call)

    def effect(kind: str, op: str, detail: str | None = "") -> SideEffect:
        return SideEffect(kind=kind, op=op, call=qualified, line=line, detail=detail or "")

    def pkg(prefixes) -> bool:
        return any(root == p.rstrip("/") or (p.endswith("/") and root.startswith(p)) for p in prefixes)

    orig = [s.rstrip("()[]") for s in name.split(".")]

    if pkg(_PAYMENT_PACKAGES) or root.lower() == "stripe":
        return effect("payment", "read" if last in _PAYMENT_READ else "write", root.split("/")[0].lstrip("@").lower() or root)

    if pkg(_EMAIL_PACKAGES) or last in _EMAIL_CALLS or (last == "send" and re.search(r"mail|email|resend|sgMail", receiver, re.I)):
        return effect("email", "write")

    if last in _QUEUE_CALLS or (last == "publish" and re.search(r"channel|publisher|sns|pubsub|topic", receiver, re.I)) \
            or (last == "add" and re.search(r"queue", receiver, re.I)) or (last == "send" and re.search(r"producer", receiver, re.I)):
        return effect("queue", "write", segs[-2] if len(segs) > 1 else "")

    if qualified in _AUTH_WRITE or last in {"login", "logIn"} and root in _REQ_NAMES:
        return effect("auth", "write")
    if qualified in _AUTH_READ:
        return effect("auth", "read")

    # fetch(url, { method: "POST" })
    if name == "fetch" or qualified in {"node-fetch", "cross-fetch", "undici.fetch"}:
        method = "GET"
        if len(args) > 1 and args[1].type == "object":
            for pair in args[1].named_children:
                if pair.type == "pair" and _text(pair.child_by_field_name("key")).strip("'\"") == "method":
                    method = (_string_value(pair.child_by_field_name("value")) or "UNKNOWN").upper()
        op = "read" if method.lower() in _HTTP_READ else ("unknown" if method == "UNKNOWN" else "write")
        return effect("http_out", op, first or "")
    if root in _HTTP_PACKAGES:
        op = "read" if last in _HTTP_READ else ("write" if last in HTTP_METHODS else "unknown")
        return effect("http_out", op, first or "")

    # Prisma: prisma.<model>.<op>(...)
    if "prisma" in segs or any("PrismaClient" in s for s in segs):
        if last in _PRISMA_READ or last in _PRISMA_WRITE:
            model = segs[-2] if len(segs) >= 2 else ""
            return effect("db", "read" if last in _PRISMA_READ else "write", model)
        if last in {"$queryRaw", "$executeRaw", "$queryRawUnsafe", "$executeRawUnsafe"}:
            return effect("db", "read" if "query" in last else "write")

    # Raw SQL: pool.query("SELECT ..."), db.execute(`UPDATE ...`)
    if last in {"query", "execute", "run", "all", "get", "prepare"} and _DB_RECEIVER_RE.search(receiver or ""):
        parsed = sql_effect(first) if first else None
        if parsed:
            return effect("db", *parsed)
        if last in {"query", "execute"}:
            return effect("db", "unknown")

    # Knex: knex("orders").insert(...)
    if root in {"knex", "db"} and len(segs) >= 2 and segs[0] and "()" in qualified.split(".")[0]:
        if last in {"insert", "update", "del", "delete", "truncate"}:
            return effect("db", "write")
        if last in {"select", "where", "first", "pluck"}:
            return effect("db", "read")

    # Mongoose / Sequelize / TypeORM: Order.find(...), Order.create(...) -- judged
    # on the name as written, since `Order` usually resolves to a module path.
    if len(orig) >= 2 and orig[-2][:1].isupper() and orig[-2] not in _JS_BUILTINS \
            and (last in _ORM_READ or last in _ORM_WRITE):
        return effect("db", "read" if last in _ORM_READ else "write", orig[-2])
    if last == "save" and len(orig) >= 2 and not args:
        model = next((s for s in orig[:-1] if s[:1].isupper() and s not in _JS_BUILTINS), orig[-2])
        return effect("db", "write", model)

    # Filesystem writes
    if root in {"fs", "fs/promises", "node:fs", "node:fs/promises", "fs-extra"} and last in _FS_WRITE:
        return effect("file", "write", first or "")

    return None


class _FunctionScanner:
    """Collects calls, side effects, request params and req.method checks for
    one function body, skipping nested functions registered as their own
    route handlers (so they aren't counted twice)."""

    def __init__(self, imports: _Imports, skip: set[tuple[int, int]], req_names: set[str]):
        self.imports = imports
        self.skip = skip
        self.req_names = req_names | _REQ_NAMES
        self.calls: set[str] = set()
        self.effects: list[SideEffect] = []
        self.params: dict[str, Param] = {}
        self.eq_methods: set[str] = set()
        self.neq_methods: set[str] = set()
        self.switch_methods: set[str] = set()
        self._chained: set[tuple[int, int]] = set()

    def scan(self, node) -> None:
        stack = [node]
        while stack:
            n = stack.pop()
            key = (n.start_byte, n.end_byte)
            if n is not node and n.type in _FUNCTION_TYPES and key in self.skip:
                continue
            if n.type == "call_expression":
                self._call(n)
            elif n.type == "member_expression":
                self._member(n)
            elif n.type == "variable_declarator":
                self._destructure(n)
            elif n.type == "binary_expression":
                self._method_check(n)
            elif n.type == "switch_statement":
                self._switch(n)
            stack.extend(reversed(n.children))

    def _call(self, n) -> None:
        name = _dotted(n.child_by_field_name("function"))
        if not name:
            return
        qualified = self.imports.qualify(name)
        self.calls.add(qualified)
        key = (n.start_byte, n.end_byte)
        eff = None
        if key not in self._chained:
            eff = classify_js_call(n, name, qualified)
            if eff:
                self.effects.append(eff)
        # Suppress inner calls of a chain only once some outer call matched a rule.
        inner = n.child_by_field_name("function") if (eff is not None or key in self._chained) else None
        while inner is not None:
            if inner.type == "call_expression":
                self._chained.add((inner.start_byte, inner.end_byte))
                inner = inner.child_by_field_name("function")
            elif inner.type == "member_expression":
                inner = inner.child_by_field_name("object")
            elif inner.type == "await_expression" and inner.named_children:
                inner = inner.named_children[0]
            else:
                break
        # searchParams.get("q") / request.nextUrl.searchParams.get("q")
        if name.endswith("searchParams.get"):
            args = _args(n)
            key_name = _string_value(args[0]) if args else None
            if key_name:
                self.params.setdefault(key_name, Param(name=key_name, required=False, location="query"))

    def _member(self, n) -> None:
        # req.body.x / req.query.x / req.params.x
        obj = n.child_by_field_name("object")
        prop = n.child_by_field_name("property")
        if obj is None or prop is None or obj.type != "member_expression":
            return
        base = _dotted(obj) or ""
        root, _, container = base.partition(".")
        location = {"body": "body", "query": "query", "params": "path"}.get(container)
        if root in self.req_names and location:
            name = _text(prop)
            self.params.setdefault(name, Param(name=name, required=location == "path", location=location))

    def _destructure(self, n) -> None:
        # const { a, b } = req.body   /   const { q } = await request.json()
        pattern, value = n.child_by_field_name("name"), n.child_by_field_name("value")
        if pattern is None or value is None or pattern.type != "object_pattern":
            return
        source = _dotted(value) or ""
        root, _, container = source.partition(".")
        location = None
        if root in self.req_names:
            location = {"body": "body", "query": "query", "params": "path", "json()": "body"}.get(container)
        if location is None:
            return
        for prop in pattern.named_children:
            if prop.type == "shorthand_property_identifier_pattern":
                name = _text(prop)
            elif prop.type == "pair_pattern":
                name = _text(prop.child_by_field_name("key"))
            elif prop.type == "object_assignment_pattern":
                left = prop.child_by_field_name("left")
                name = _text(left)
            else:
                continue
            self.params.setdefault(name, Param(name=name, required=location == "path", location=location))

    def _method_check(self, n) -> None:
        left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
        op = _text(n.child_by_field_name("operator"))
        for a, b in ((left, right), (right, left)):
            d = _dotted(a) or ""
            if not (d.endswith(".method") and d.split(".")[0] in self.req_names):
                continue
            value = _string_value(b)
            if not value:
                continue
            if op in ("===", "=="):
                self.eq_methods.add(value.upper())
            elif op in ("!==", "!="):
                self.neq_methods.add(value.upper())

    def _switch(self, n) -> None:
        value = n.child_by_field_name("value")
        if value is None or not (_dotted(value.named_children[0] if value.type == "parenthesized_expression" and value.named_children else value) or "").endswith(".method"):
            return
        body = n.child_by_field_name("body")
        for case in (body.named_children if body is not None else []):
            if case.type == "switch_case":
                v = _string_value(case.child_by_field_name("value"))
                if v:
                    self.switch_methods.add(v.upper())

    def http_methods(self) -> list[str]:
        methods = self.switch_methods | self.eq_methods | self.neq_methods
        if self.eq_methods and not self.neq_methods and not self.switch_methods:
            methods.add("GET")
        return sorted(methods)


def _params(fn) -> list[Param]:
    params_node = fn.child_by_field_name("parameters")
    if params_node is None:
        single = fn.child_by_field_name("parameter")  # `x => ...`
        return [Param(name=_text(single))] if single is not None else []
    out = []
    for p in params_node.named_children:
        type_ann = None
        required = True
        node = p
        if p.type in ("required_parameter", "optional_parameter"):
            node = p.child_by_field_name("pattern")
            ann = p.child_by_field_name("type")
            type_ann = _text(ann).lstrip(":").strip() if ann is not None else None
            required = p.type == "required_parameter" and p.child_by_field_name("value") is None
        if node is None:
            continue
        if node.type == "assignment_pattern":
            node = node.child_by_field_name("left")
            required = False
        if node is None:
            continue
        if node.type == "identifier":
            out.append(Param(name=_text(node), type=type_ann, required=required))
        elif node.type == "rest_pattern":
            out.append(Param(name="..." + _text(node).lstrip("."), type=type_ann, required=False))
        else:  # destructured: ({ params }) -> keep as one opaque param
            out.append(Param(name=_text(node), type=type_ann, required=required))
    return out


def _is_async(fn) -> bool:
    return any(c.type == "async" for c in fn.children)


def _express_path(raw: str) -> str:
    path = _EXPRESS_PATH_PARAM_RE.sub(r"{\1}", raw)
    return path if path.startswith("/") else "/" + path


def next_route_path(rel: str) -> tuple[str, str] | None:
    """('pages'|'app', '/api/orders/{id}') for a Next.js route file, else None."""
    parts = rel.split("/")
    if parts and parts[0] == "src":
        parts = parts[1:]
    if len(parts) < 2:
        return None
    stem = module_name_for(parts[-1])
    if parts[0] == "pages" and len(parts) >= 2 and parts[1] == "api":
        segs = parts[:-1][1:] + ([] if stem == "index" else [stem])
        kind = "pages"
    elif parts[0] == "app" and stem == "route":
        segs = [s for s in parts[1:-1] if not (s.startswith("(") and s.endswith(")")) and not s.startswith("@")]
        kind = "app"
    else:
        return None
    out = []
    for s in segs:
        m = _NEXT_SEGMENT_RE.match(s)
        out.append("{" + (m.group(1) or m.group(2)) + "}" if m else s)
    return kind, "/" + "/".join(out)


class JavaScriptAnalyzer:
    languages = ("javascript", "typescript")

    def __init__(self) -> None:
        if tree_sitter is None:
            raise RuntimeError("tree-sitter is not installed")
        self._parsers = {
            "js": tree_sitter.Parser(tree_sitter.Language(tree_sitter_javascript.language())),
            "ts": tree_sitter.Parser(tree_sitter.Language(tree_sitter_typescript.language_typescript())),
            "tsx": tree_sitter.Parser(tree_sitter.Language(tree_sitter_typescript.language_tsx())),
        }

    def _parser_for(self, rel: str):
        if rel.endswith(".tsx"):
            return self._parsers["tsx"]
        if rel.endswith(".ts") or rel.endswith(".mts") or rel.endswith(".cts"):
            return self._parsers["ts"]
        return self._parsers["js"]

    def analyze(self, source: SourceFile, text: str) -> FileResult:
        rel = source.rel
        module = Module(path=rel, language=source.language, name=module_name_for(rel),
                        lines=text.count("\n") + (0 if text.endswith("\n") or not text else 1))
        result = FileResult(module=module)
        tree = self._parser_for(rel).parse(text.encode("utf-8"))
        root = tree.root_node
        if root.has_error:
            # tree-sitter recovers from errors; keep going but say so.
            module.parse_error = "syntax errors (partial parse)"

        imports = _Imports(rel)
        apps: set[str] = set()  # identifiers holding an express app/router
        self._collect_imports(root, imports)
        module.imports = dict(sorted(imports.map.items()))
        # Variables bound to a constructed client (`const stripe = new Stripe(key)`,
        # `const mailer = nodemailer.createTransport()`) resolve like their source
        # for side-effect classification, but aren't imports.
        self._collect_bindings(root, imports, apps)
        result.frameworks |= self._frameworks(imports)

        # Pass 1: entry points declared by calls (express routes, cron, workers...).
        handlers: dict[tuple[int, int], str] = {}  # inline handler fn node -> synthetic qualname
        inline_nodes: dict[tuple[int, int], object] = {}
        eps, mounts = self._call_entry_points(root, rel, imports, apps, handlers, inline_nodes)
        result.mounts.extend(mounts)

        # Pass 2: functions.
        skip = set(handlers)
        exports: dict[str, str] = {}
        for qualname, fn, decl in self._top_level_functions(root, exports):
            module.functions.append(self._function(qualname, fn, decl, imports, skip))
        for key, qualname in handlers.items():
            fn = inline_nodes[key]
            module.functions.append(self._function(qualname, fn, fn, imports, skip - {key}))
        for cls_name, cls_node, methods in self._classes(root, exports):
            module.classes.append(Class(name=cls_name, line=_line(cls_node), methods=[m for m, _ in methods],
                                        bases=self._class_bases(cls_node, imports), docstring=_first_line_of_jsdoc(cls_node)))
            for mname, mnode in methods:
                module.functions.append(self._function(f"{cls_name}.{mname}", mnode, mnode, imports, skip))
        module.exports = dict(sorted(exports.items()))
        module.functions.sort(key=lambda f: (f.line, f.qualname))

        result.entry_points.extend(eps)
        result.entry_points.extend(self._next_entry_points(rel, module))
        if any(e.framework == "nextjs" for e in result.entry_points):
            result.frameworks.add("nextjs")
        if any(e.framework == "express" for e in result.entry_points) or mounts:
            result.frameworks.add("express")
        return result

    # -- bindings -----------------------------------------------------------

    @staticmethod
    def _walk(root):
        stack = [root]
        while stack:
            n = stack.pop()
            yield n
            stack.extend(reversed(n.children))

    def _collect_imports(self, root, imports: _Imports) -> None:
        for n in self._walk(root):
            if n.type == "import_statement":
                imports.add_es(n)
            elif n.type == "variable_declarator":
                pattern, value = n.child_by_field_name("name"), n.child_by_field_name("value")
                if pattern is not None and value is not None and value.type == "call_expression":
                    imports.add_require(pattern, value)

    def _collect_bindings(self, root, imports: _Imports, apps: set[str]) -> None:
        for n in self._walk(root):
            if n.type == "variable_declarator":
                pattern, value = n.child_by_field_name("name"), n.child_by_field_name("value")
                if pattern is None or value is None or pattern.type != "identifier" or _text(pattern) in imports.map:
                    continue
                if value.type == "await_expression" and value.named_children:
                    value = value.named_children[0]
                self._binding(_text(pattern), value, imports, apps)
            elif n.type == "assignment_expression":
                left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
                if left is not None and right is not None and left.type == "identifier" and _text(left) not in imports.map:
                    self._binding(_text(left), right, imports, apps)

    def _binding(self, name: str, value, imports: _Imports, apps: set[str]) -> None:
        if value.type == "call_expression":
            callee = imports.qualify(_dotted(value.child_by_field_name("function")) or "")
            if callee in {"express", "express.Router", "express.express", "express.default"} or callee.endswith("express.Router") \
                    or callee in {"Router", "express.Router()"}:
                apps.add(name)
            elif callee.startswith("express") and callee.split(".")[-1] == "Router":
                apps.add(name)
            elif imports.is_imported(_dotted(value.child_by_field_name("function")) or ""):
                # Only calls into an imported module: `nodemailer.createTransport()`.
                imports.instances[name] = callee + "()"
        elif value.type == "new_expression":
            raw = _dotted(value.child_by_field_name("constructor")) or ""
            if imports.is_imported(raw):
                imports.instances[name] = imports.qualify(raw) + "()"

    @staticmethod
    def _frameworks(imports: _Imports) -> set[str]:
        found = set()
        targets = set(imports.map.values())
        if any(t == "express" or t.startswith("express.") for t in targets):
            found.add("express")
        if any(t == "next" or t.startswith(("next/", "next.")) for t in targets):
            found.add("nextjs")
        return found

    # -- functions ----------------------------------------------------------

    def _top_level_functions(self, root, exports: dict[str, str]):
        """Yield (qualname, function node, declaration node for JSDoc)."""
        anon_default = 0
        for stmt in root.named_children:
            node = stmt
            exported = node.type == "export_statement"
            is_default = exported and any(c.type == "default" for c in node.children)
            if exported:
                decl = node.child_by_field_name("declaration")
                value = node.child_by_field_name("value")
                if decl is None and value is not None:
                    # export default <expr>
                    if value.type in _FUNCTION_TYPES:
                        anon_default += 1
                        exports["default"] = "default"
                        yield "default", value, stmt
                    elif value.type == "identifier":
                        exports["default"] = _text(value)
                    continue
                if decl is None:
                    # export { a, b as c }
                    clause = next((c for c in node.named_children if c.type == "export_clause"), None)
                    for spec in (clause.named_children if clause is not None else []):
                        name, alias = spec.child_by_field_name("name"), spec.child_by_field_name("alias")
                        if name is not None:
                            exports[_text(alias or name)] = _text(name)
                    continue
                node = decl
            if node.type in ("function_declaration", "generator_function_declaration"):
                name = _text(node.child_by_field_name("name"))
                if exported:
                    exports["default" if is_default else name] = name
                yield name, node, stmt
            elif node.type in ("lexical_declaration", "variable_declaration"):
                for d in node.named_children:
                    if d.type != "variable_declarator":
                        continue
                    pattern, value = d.child_by_field_name("name"), d.child_by_field_name("value")
                    if pattern is None or value is None or pattern.type != "identifier":
                        continue
                    if value.type in _FUNCTION_TYPES:
                        name = _text(pattern)
                        if exported:
                            exports[name] = name
                        yield name, value, stmt
            elif node.type == "expression_statement":
                yield from self._commonjs_exports(node, exports)

    def _commonjs_exports(self, stmt, exports: dict[str, str]):
        expr = stmt.named_children[0] if stmt.named_children else None
        if expr is None or expr.type != "assignment_expression":
            return
        left = _dotted(expr.child_by_field_name("left")) or ""
        right = expr.child_by_field_name("right")
        if left == "module.exports":
            if right.type == "identifier":
                exports["default"] = _text(right)
            elif right.type == "object":
                for prop in right.named_children:
                    if prop.type == "shorthand_property_identifier":
                        exports[_text(prop)] = _text(prop)
                    elif prop.type == "pair":
                        key, value = _text(prop.child_by_field_name("key")).strip("'\""), prop.child_by_field_name("value")
                        if value.type == "identifier":
                            exports[key] = _text(value)
                        elif value.type in _FUNCTION_TYPES:
                            exports[key] = key
                            yield key, value, stmt
                    elif prop.type == "method_definition":
                        key = _text(prop.child_by_field_name("name"))
                        exports[key] = key
                        yield key, prop, stmt
            elif right.type in _FUNCTION_TYPES:
                exports["default"] = "default"
                yield "default", right, stmt
        elif left.startswith(("module.exports.", "exports.")):
            key = left.rsplit(".", 1)[-1]
            if right.type == "identifier":
                exports[key] = _text(right)
            elif right.type in _FUNCTION_TYPES:
                exports[key] = key
                yield key, right, stmt

    def _classes(self, root, exports: dict[str, str]):
        for stmt in root.named_children:
            node = stmt
            if node.type == "export_statement":
                decl = node.child_by_field_name("declaration")
                if decl is None:
                    continue
                if decl.type == "class_declaration":
                    name = _text(decl.child_by_field_name("name"))
                    exports["default" if any(c.type == "default" for c in node.children) else name] = name
                node = decl
            if node.type not in ("class_declaration", "abstract_class_declaration"):
                continue
            body = node.child_by_field_name("body")
            methods = []
            for m in (body.named_children if body is not None else []):
                if m.type == "method_definition":
                    methods.append((_text(m.child_by_field_name("name")), m))
            yield _text(node.child_by_field_name("name")), node, methods

    @staticmethod
    def _class_bases(cls_node, imports: _Imports) -> list[str]:
        heritage = next((c for c in cls_node.named_children if c.type == "class_heritage"), None)
        if heritage is None:
            return []
        out = []
        for c in heritage.named_children:
            target = c.named_children[0] if c.type == "extends_clause" and c.named_children else c
            name = _dotted(target)
            if name:
                out.append(imports.qualify(name))
        return out

    def _function(self, qualname: str, fn, decl, imports: _Imports, skip: set) -> Function:
        params = _params(fn)
        req_names = {p.name for p in params[:1] if p.name in _REQ_NAMES}
        scanner = _FunctionScanner(imports, skip, req_names)
        body = fn.child_by_field_name("body")
        if body is not None:
            scanner.scan(body)
        signature_names = {p.name for p in params}
        extra = [p for n, p in sorted(scanner.params.items()) if n not in signature_names]
        return Function(
            qualname=qualname,
            line=_line(fn),
            end_line=fn.end_point[0] + 1,
            params=params + extra,
            calls=sorted(scanner.calls),
            side_effects=sorted(scanner.effects, key=lambda e: (e.line, e.kind, e.call)),
            docstring=_first_line_of_jsdoc(decl),
            is_async=_is_async(fn),
            http_methods=scanner.http_methods(),
        )

    # -- entry points -------------------------------------------------------

    def _handler_ref(self, node, rel: str, imports: _Imports, label: str, handlers, inline_nodes) -> str | None:
        if node is None:
            return None
        if node.type in _FUNCTION_TYPES:
            key = (node.start_byte, node.end_byte)
            handlers.setdefault(key, label)
            inline_nodes[key] = node
            return f"{rel}::{handlers[key]}"
        name = _dotted(node)
        if not name or "()" in name:
            return None
        qualified = imports.qualify(name)
        if qualified == name and "." not in name:
            return f"{rel}::{name}"
        return qualified

    @staticmethod
    def _looks_like_handler(node, imports: _Imports) -> bool:
        """For a receiver matched only by its name: is the last argument really a
        request handler (a 2+-param function, or a function from a local module)?"""
        if node.type in _FUNCTION_TYPES:
            return len(_params(node)) >= 2
        name = _dotted(node)
        if not name or "()" in name:
            return False
        qualified = imports.qualify(name)
        return any(qualified.startswith(m + ".") or qualified == m for m in imports.local_modules)

    def _call_entry_points(self, root, rel, imports, apps, handlers, inline_nodes):
        eps: list[EntryPoint] = []
        mounts: list[Mount] = []
        stack = [root]
        while stack:
            n = stack.pop()
            stack.extend(n.children)
            if n.type == "new_expression":
                ctor = imports.qualify(_dotted(n.child_by_field_name("constructor")) or "")
                args = _args(n)
                if ctor.endswith("Worker") and ctor.startswith("bullmq") and len(args) >= 2:
                    queue = _string_value(args[0]) or "queue"
                    ref = self._handler_ref(args[1], rel, imports, f"<worker {queue}>", handlers, inline_nodes)
                    if ref:
                        eps.append(EntryPoint(kind="message_handler", framework="bullmq", handler=ref, file=rel,
                                              line=_line(n), name=f"worker {queue}"))
                continue
            if n.type != "call_expression":
                continue
            fn = n.child_by_field_name("function")
            if fn is None or fn.type != "member_expression":
                continue
            obj = fn.child_by_field_name("object")
            prop = _text(fn.child_by_field_name("property"))
            args = _args(n)
            obj_name = _dotted(obj) or ""
            qualified_obj = imports.qualify(obj_name)

            # express: app.get("/p", mw, handler) / router.route("/p").get(handler)
            if prop in _ROUTER_METHODS and args:
                raw = _string_value(args[0])
                route_obj = obj.type == "identifier" and (obj_name in apps or _ROUTER_NAME_RE.match(obj_name))
                if raw is None and obj.type == "call_expression":
                    # router.route("/p").get(h).post(h): walk back to the .route() call.
                    handler_args = []
                    inner = obj
                    while inner is not None and inner.type == "call_expression":
                        inner_fn = inner.child_by_field_name("function")
                        if inner_fn is None or inner_fn.type != "member_expression":
                            break
                        inner_prop = _text(inner_fn.child_by_field_name("property"))
                        if inner_prop == "route":
                            inner_args = _args(inner)
                            raw = _string_value(inner_args[0]) if inner_args else None
                            handler_args = args
                            route_obj = raw is not None
                            break
                        inner = inner_fn.child_by_field_name("object") if inner_prop in _ROUTER_METHODS else None
                else:
                    handler_args = args[1:]
                if route_obj and obj_name not in apps and handler_args and not self._looks_like_handler(handler_args[-1], imports):
                    route_obj = False  # e.g. an axios instance named `api`: api.post("/x", data)
                if route_obj and raw is not None and handler_args and (raw.startswith("/") or raw == "*"):
                    method = prop.upper() if prop != "all" else "ANY"
                    path = _express_path(raw)
                    ref = self._handler_ref(handler_args[-1], rel, imports, f"<{method} {path}>", handlers, inline_nodes)
                    middleware = [imports.qualify(d) for d in (_dotted(a) for a in handler_args[:-1]) if d]
                    if ref:
                        eps.append(EntryPoint(kind="http_route", framework="express", handler=ref, file=rel,
                                              line=_line(n), name=f"{method} {path}", method=method, path=path,
                                              middleware=middleware))
                    continue

            # express mount: app.use("/api", router) / app.use("/api", require("./r"))
            if prop == "use" and len(args) >= 2 and (obj_name in apps or _ROUTER_NAME_RE.match(obj_name or "")):
                prefix = _string_value(args[0])
                if prefix is not None:
                    for target_node in args[1:]:
                        target = None
                        if target_node.type == "identifier":
                            target = imports.map.get(_text(target_node))
                        elif target_node.type == "call_expression" and _text(target_node.child_by_field_name("function")) == "require":
                            spec = _string_value(_args(target_node)[0]) if _args(target_node) else None
                            target = resolve_specifier(spec, rel) if spec and spec.startswith(".") else None
                        if target and target.endswith(".default"):
                            target = target[: -len(".default")]
                        if target and (target in imports.local_modules):
                            mounts.append(Mount(framework="express", prefix=prefix, target=target, file=rel, line=_line(n)))
                continue

            # node-cron: cron.schedule("0 9 * * *", fn) / node-schedule: schedule.scheduleJob(spec, fn)
            if (prop == "schedule" and qualified_obj.startswith("node-cron")) or (prop == "scheduleJob" and qualified_obj.startswith("node-schedule")):
                if len(args) >= 2:
                    spec = _string_value(args[0]) or ""
                    ref = self._handler_ref(args[-1], rel, imports, f"<cron {spec}>", handlers, inline_nodes)
                    if ref:
                        eps.append(EntryPoint(kind="scheduled_job", framework=qualified_obj.split(".")[0], handler=ref,
                                              file=rel, line=_line(n), name=f"job {spec}".strip()))
                continue

            # commander: program.command("name").action(fn)
            if prop == "action" and obj is not None and obj.type == "call_expression" and args:
                cmd_fn = obj.child_by_field_name("function")
                while cmd_fn is not None and cmd_fn.type == "member_expression" and _text(cmd_fn.child_by_field_name("property")) != "command":
                    inner = cmd_fn.child_by_field_name("object")
                    cmd_fn = inner.child_by_field_name("function") if inner is not None and inner.type == "call_expression" else None
                if cmd_fn is not None and cmd_fn.type == "member_expression":
                    call = cmd_fn.parent
                    cmd_args = _args(call)
                    cmd = (_string_value(cmd_args[0]) if cmd_args else None) or "command"
                    cmd = cmd.split()[0]
                    ref = self._handler_ref(args[0], rel, imports, f"<cli {cmd}>", handlers, inline_nodes)
                    if ref:
                        eps.append(EntryPoint(kind="cli_command", framework="commander", handler=ref, file=rel,
                                              line=_line(n), name=f"cli {cmd}"))
                continue

            # amqplib: channel.consume("queue", fn)
            if prop == "consume" and len(args) >= 2 and re.search(r"channel|ch$", obj_name, re.I):
                queue = _string_value(args[0]) or "queue"
                ref = self._handler_ref(args[1], rel, imports, f"<consumer {queue}>", handlers, inline_nodes)
                if ref:
                    eps.append(EntryPoint(kind="message_handler", framework="amqplib", handler=ref, file=rel,
                                          line=_line(n), name=f"consumer {queue}"))
                continue

            # kafkajs: consumer.run({ eachMessage: fn })
            if prop == "run" and args and args[0].type == "object" and re.search(r"consumer", obj_name, re.I):
                for pair in args[0].named_children:
                    if pair.type == "pair" and _text(pair.child_by_field_name("key")) in {"eachMessage", "eachBatch"}:
                        ref = self._handler_ref(pair.child_by_field_name("value"), rel, imports, "<kafka consumer>", handlers, inline_nodes)
                        if ref:
                            eps.append(EntryPoint(kind="message_handler", framework="kafkajs", handler=ref, file=rel,
                                                  line=_line(n), name="consumer kafka"))
        return eps, mounts

    def _next_entry_points(self, rel: str, module: Module) -> list[EntryPoint]:
        route = next_route_path(rel)
        if route is None:
            return []
        kind, path = route
        funcs = {f.qualname: f for f in module.functions}
        eps = []
        if kind == "pages":
            target = module.exports.get("default")
            func = funcs.get(target or "")
            if func is None:
                return []
            for method in func.http_methods or ["ANY"]:
                eps.append(EntryPoint(kind="http_route", framework="nextjs", handler=f"{rel}::{func.qualname}", file=rel,
                                      line=func.line, name=f"{method} {path}", method=method, path=path))
        else:
            for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                local = module.exports.get(method)
                func = funcs.get(local or "")
                if func is None:
                    continue
                eps.append(EntryPoint(kind="http_route", framework="nextjs", handler=f"{rel}::{func.qualname}", file=rel,
                                      line=func.line, name=f"{method} {path}", method=method, path=path))
        return eps
