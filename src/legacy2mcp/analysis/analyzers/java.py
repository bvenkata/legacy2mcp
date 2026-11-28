"""
Java analyzer for `legacy2mcp analyze`, built on tree-sitter. Nothing from the
scanned repo is executed or compiled.

Per file it extracts package declarations, imports, classes (interfaces, superclasses),
methods, calls and side effects (db, outbound HTTP, email, payment, queue, file, auth),
and entry points:

- Spring Boot: `@RestController`, `@Controller`, `@RequestMapping`, `@GetMapping`,
  `@PostMapping`, `@PutMapping`, `@DeleteMapping`, `@PatchMapping`.
- Java Servlets: classes extending `HttpServlet` (or annotated `@WebServlet`) with
  `doGet`, `doPost`, `doPut`, `doDelete` methods, extracting query/body parameters
  from `req.getParameter(...)` calls.
- JAX-RS: `@Path` at class/method level with `@GET`, `@POST`, `@PUT`, `@DELETE`,
  `@PATCH`, and `@PathParam`, `@QueryParam`, `@FormParam`.
"""

from __future__ import annotations

import re
from typing import Any

from legacy2mcp.analysis.analyzers.base import FileResult
from legacy2mcp.analysis.analyzers.common import sql_effect
from legacy2mcp.analysis.model import Class, EntryPoint, Function, Module, Param, SideEffect
from legacy2mcp.analysis.walk import SourceFile

try:
    import tree_sitter
    import tree_sitter_java
except ImportError:  # pragma: no cover
    tree_sitter = None

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

_SPRING_MAPPINGS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}

_JAXRS_METHODS = {
    "GET": "GET",
    "POST": "POST",
    "PUT": "PUT",
    "DELETE": "DELETE",
    "PATCH": "PATCH",
    "HEAD": "HEAD",
    "OPTIONS": "OPTIONS",
}

_SERVLET_METHODS = {
    "doGet": "GET",
    "doPost": "POST",
    "doPut": "PUT",
    "doDelete": "DELETE",
    "doHead": "HEAD",
    "doOptions": "OPTIONS",
}

_PARAM_LOCATIONS = {
    "PathVariable": "path",
    "PathParam": "path",
    "RequestParam": "query",
    "QueryParam": "query",
    "RequestBody": "body",
    "FormParam": "form",
    "RequestHeader": "header",
    "HeaderParam": "header",
    "CookieValue": "cookie",
}

_SPRING_AUTH_ANNOTATIONS = {
    "PreAuthorize", "PostAuthorize", "Secured", "RolesAllowed",
}

_DB_READ_METHODS = {
    "query", "queryForObject", "queryForList", "queryForRowSet", "queryForMap",
    "find", "findById", "findAll", "findOne", "findAllById", "count", "existsById",
    "createQuery", "createNativeQuery", "getById", "getBy", "getOne",
}

_DB_WRITE_METHODS = {
    "update", "batchUpdate", "execute", "save", "saveAll", "saveAndFlush",
    "delete", "deleteById", "deleteAll", "deleteAllById", "remove", "persist",
    "merge", "flush", "executeUpdate",
}

_HTTP_CLIENT_RECEIVERS = re.compile(r"(restTemplate|webClient|httpClient|client|http)", re.I)
_DB_RECEIVER_RE = re.compile(
    r"(repo|repository|dao|mapper|jdbctemplate|entitymanager|\bem\b|session|db|database|conn|connection|datasource)",
    re.I,
)
_PAYMENT_RECEIVER_RE = re.compile(
    r"(stripe|paypal|braintree|square|razorpay|adyen|payment|charge|customer|refund|paymentintent)",
    re.I,
)
_EMAIL_RECEIVER_RE = re.compile(r"(mail|email|mailer|ses|sendgrid)", re.I)
_QUEUE_RECEIVER_RE = re.compile(r"(jms|kafka|rabbit|queue|producer|publisher|topic|amqp)", re.I)


def available() -> bool:
    return tree_sitter is not None


def _text(node: Any) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


def _line(node: Any) -> int:
    return node.start_point[0] + 1


def _end_line(node: Any) -> int:
    return node.end_point[0] + 1


def _child_of_type(node: Any, type_name: str) -> Any | None:
    if node is None:
        return None
    for c in node.children:
        if c.type == type_name:
            return c
    return None


def _children_of_type(node: Any, type_name: str) -> list[Any]:
    if node is None:
        return []
    return [c for c in node.children if c.type == type_name]


def _string_value(node: Any) -> str | None:
    if node is None:
        return None
    if node.type == "string_literal":
        frags = [c for c in node.named_children if c.type in ("string_fragment", "escape_sequence")]
        if frags:
            return "".join(_text(c) for c in frags)
        raw = _text(node)
        if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
            return raw[1:-1]
        return raw
    return None


def _extract_strings_from_node(node: Any) -> list[str]:
    if node is None:
        return []
    if node.type == "string_literal":
        val = _string_value(node)
        return [val] if val is not None else []
    if node.type == "element_value_array_initializer":
        out: list[str] = []
        for child in node.named_children:
            s = _string_value(child)
            if s is not None:
                out.append(s)
        return out
    return []


def _parse_annotation(node: Any) -> tuple[str, dict[str, Any], list[str]]:
    """Returns (annotation_name, keyword_args, positional_args)."""
    name = ""
    kwargs: dict[str, Any] = {}
    args: list[str] = []

    for c in node.children:
        if c.type in ("identifier", "scoped_identifier"):
            name = _text(c).split(".")[-1]
            break

    arg_list = _child_of_type(node, "annotation_argument_list")
    if arg_list is not None:
        for c in arg_list.named_children:
            if c.type == "element_value_pair":
                key_node = c.child_by_field_name("key") or _child_of_type(c, "identifier")
                val_node = c.child_by_field_name("value")
                key_name = _text(key_node) if key_node else ""
                strings = _extract_strings_from_node(val_node)
                if strings:
                    kwargs[key_name] = strings[0] if len(strings) == 1 else strings
                else:
                    kwargs[key_name] = _text(val_node)
            else:
                strings = _extract_strings_from_node(c)
                if strings:
                    args.extend(strings)
                else:
                    args.append(_text(c))

    return name, kwargs, args


def _clean_docstring(text: str) -> str:
    """Extract first line of docstring from a Javadoc block comment."""
    cleaned: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("/**"):
            line = line[3:].strip()
        elif line.startswith("/*"):
            line = line[2:].strip()
        if line.endswith("*/"):
            line = line[:-2].strip()
        if line.startswith("*"):
            line = line[1:].strip()
        if line and not line.startswith("@"):
            cleaned.append(line)
    return cleaned[0] if cleaned else ""


def join_route(prefix: str, route: str) -> str:
    if not prefix and not route:
        return "/"
    p = prefix.strip()
    r = route.strip()
    if p and not p.startswith("/"):
        p = "/" + p
    if p.endswith("/"):
        p = p[:-1]
    if r and not r.startswith("/"):
        r = "/" + r
    joined = (p + r) if (p or r) else "/"
    return joined or "/"


class JavaAnalyzer:
    languages = ("java",)

    def __init__(self) -> None:
        if tree_sitter is None:
            raise RuntimeError("tree-sitter is not installed")
        self._parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_java.language()))

    def analyze(self, source: SourceFile, text: str) -> FileResult:
        rel = source.rel
        mod_name = rel[:-5] if rel.endswith(".java") else rel
        module = Module(
            path=rel,
            language="java",
            name=mod_name,
            lines=text.count("\n") + (0 if text.endswith("\n") or not text else 1),
        )
        result = FileResult(module=module)

        tree = self._parser.parse(text.encode("utf-8"))
        root = tree.root_node
        if root.has_error:
            module.parse_error = "tree-sitter reported syntax errors; parsed partially"

        # Pass 1: imports
        for child in root.named_children:
            if child.type == "import_declaration":
                for gc in child.named_children:
                    if gc.type in ("scoped_identifier", "identifier"):
                        full_import = _text(gc)
                        short_name = full_import.split(".")[-1]
                        module.imports[short_name] = full_import

        # Pass 2: classes and interfaces
        classes: list[Class] = []
        functions: list[Function] = []
        entry_points: list[EntryPoint] = []
        frameworks: set[str] = set()

        prev_sibling = None
        for child in root.named_children:
            docstring = ""
            if prev_sibling is not None and prev_sibling.type == "block_comment":
                docstring = _clean_docstring(_text(prev_sibling))

            if child.type in ("class_declaration", "interface_declaration"):
                self._analyze_class(
                    child,
                    source=source,
                    docstring=docstring,
                    classes=classes,
                    functions=functions,
                    entry_points=entry_points,
                    frameworks=frameworks,
                    module_imports=module.imports,
                )
            prev_sibling = child

        module.classes = classes
        module.functions = functions
        result.entry_points = entry_points
        result.frameworks = frameworks
        return result

    def _analyze_class(
        self,
        class_node: Any,
        source: SourceFile,
        docstring: str,
        classes: list[Class],
        functions: list[Function],
        entry_points: list[EntryPoint],
        frameworks: set[str],
        module_imports: dict[str, str],
    ) -> None:
        name_node = _child_of_type(class_node, "identifier")
        class_name = _text(name_node) if name_node else ""
        if not class_name:
            return

        bases: list[str] = []
        superclass_node = _child_of_type(class_node, "superclass")
        if superclass_node is not None:
            for c in superclass_node.named_children:
                if c.type in ("type_identifier", "generic_type"):
                    bases.append(_text(c))

        interfaces_node = _child_of_type(class_node, "super_interfaces")
        if interfaces_node is not None:
            type_list = _child_of_type(interfaces_node, "type_list")
            source_node = type_list if type_list is not None else interfaces_node
            for c in source_node.named_children:
                if c.type in ("type_identifier", "generic_type"):
                    bases.append(_text(c))

        # Modifiers / Annotations on the class
        class_annotations: dict[str, tuple[dict[str, Any], list[str]]] = {}
        modifiers_node = _child_of_type(class_node, "modifiers")
        if modifiers_node is not None:
            for mod in modifiers_node.children:
                if mod.type in ("annotation", "marker_annotation"):
                    ann_name, ann_kwargs, ann_args = _parse_annotation(mod)
                    class_annotations[ann_name] = (ann_kwargs, ann_args)

        is_spring_controller = any(a in class_annotations for a in ("RestController", "Controller"))
        is_servlet = "HttpServlet" in bases or "GenericServlet" in bases or "WebServlet" in class_annotations
        is_jaxrs = "Path" in class_annotations

        if is_spring_controller or "RequestMapping" in class_annotations:
            frameworks.add("spring")
        if is_servlet:
            frameworks.add("servlet")
        if is_jaxrs:
            frameworks.add("jax-rs")

        # Class-level route prefix
        class_prefix = ""
        if "RequestMapping" in class_annotations:
            kwargs, args = class_annotations["RequestMapping"]
            path_val = kwargs.get("value") or kwargs.get("path") or (args[0] if args else "")
            if isinstance(path_val, list):
                class_prefix = path_val[0] if path_val else ""
            else:
                class_prefix = str(path_val or "")
        elif "Path" in class_annotations:
            kwargs, args = class_annotations["Path"]
            path_val = kwargs.get("value") or (args[0] if args else "")
            class_prefix = str(path_val or "")

        # WebServlet paths
        servlet_paths: list[str] = []
        if "WebServlet" in class_annotations:
            kwargs, args = class_annotations["WebServlet"]
            patterns = kwargs.get("urlPatterns") or kwargs.get("value") or (args[0] if args else "")
            if isinstance(patterns, list):
                servlet_paths.extend(patterns)
            elif patterns:
                servlet_paths.append(str(patterns))

        class_methods: list[str] = []
        body_node = _child_of_type(class_node, "class_body")
        if body_node is not None:
            prev_body_child = None
            for member in body_node.named_children:
                method_doc = ""
                if prev_body_child is not None and prev_body_child.type == "block_comment":
                    method_doc = _clean_docstring(_text(prev_body_child))

                if member.type == "method_declaration":
                    m_name_node = _child_of_type(member, "identifier")
                    qualname = f"{class_name}.{_text(m_name_node)}"
                    class_methods.append(qualname)
                    self._analyze_method(
                        member,
                        source=source,
                        class_name=class_name,
                        class_prefix=class_prefix,
                        servlet_paths=servlet_paths,
                        is_servlet=is_servlet,
                        docstring=method_doc,
                        functions=functions,
                        entry_points=entry_points,
                        frameworks=frameworks,
                        module_imports=module_imports,
                    )
                prev_body_child = member

        classes.append(
            Class(
                name=class_name,
                line=_line(class_node),
                bases=bases,
                methods=class_methods,
                docstring=docstring,
            )
        )

    def _analyze_method(
        self,
        method_node: Any,
        source: SourceFile,
        class_name: str,
        class_prefix: str,
        servlet_paths: list[str],
        is_servlet: bool,
        docstring: str,
        functions: list[Function],
        entry_points: list[EntryPoint],
        frameworks: set[str],
        module_imports: dict[str, str],
    ) -> None:
        name_node = _child_of_type(method_node, "identifier")
        method_name = _text(name_node) if name_node else ""
        if not method_name:
            return

        qualname = f"{class_name}.{method_name}"
        line = _line(method_node)
        end_line = _end_line(method_node)

        method_annotations: dict[str, tuple[dict[str, Any], list[str]]] = {}
        decorators: list[str] = []
        is_async = False
        middleware: list[str] = []

        modifiers_node = _child_of_type(method_node, "modifiers")
        if modifiers_node is not None:
            for mod in modifiers_node.children:
                if mod.type in ("annotation", "marker_annotation"):
                    decorators.append(_text(mod))
                    ann_name, ann_kwargs, ann_args = _parse_annotation(mod)
                    method_annotations[ann_name] = (ann_kwargs, ann_args)
                    if ann_name == "Async":
                        is_async = True
                    if ann_name in _SPRING_AUTH_ANNOTATIONS:
                        middleware.append(f"spring_security.{ann_name}")

        params: list[Param] = []
        params_node = _child_of_type(method_node, "formal_parameters")
        if params_node is not None:
            for param_child in params_node.named_children:
                if param_child.type == "formal_parameter":
                    param_name_node = _child_of_type(param_child, "identifier")
                    param_name = _text(param_name_node)
                    type_node = next(
                        (c for c in param_child.children if c.type.endswith("_type") or c.type in ("type_identifier", "generic_type")),
                        None,
                    )
                    param_type = _text(type_node) if type_node else None
                    location = None
                    param_mods = _child_of_type(param_child, "modifiers")
                    param_ann_name = None
                    if param_mods is not None:
                        for pmod in param_mods.children:
                            if pmod.type in ("annotation", "marker_annotation"):
                                pann_name, pkwargs, pargs = _parse_annotation(pmod)
                                if pann_name in _PARAM_LOCATIONS:
                                    location = _PARAM_LOCATIONS[pann_name]
                                    param_ann_name = pkwargs.get("value") or pkwargs.get("name") or (pargs[0] if pargs else None)
                                    break
                    final_name = param_ann_name if param_ann_name else param_name
                    if final_name:
                        params.append(
                            Param(
                                name=final_name,
                                type=param_type,
                                required=True,
                                location=location,
                            )
                        )

        calls: list[str] = []
        call_sites: dict[str, int] = {}
        side_effects: list[SideEffect] = []
        servlet_query_params: list[str] = []

        body_node = _child_of_type(method_node, "block")
        if body_node is not None:
            self._walk_body(
                body_node,
                calls=calls,
                call_sites=call_sites,
                side_effects=side_effects,
                servlet_query_params=servlet_query_params,
                module_imports=module_imports,
            )

        for sq_param in servlet_query_params:
            if not any(p.name == sq_param for p in params):
                params.append(Param(name=sq_param, type="String", location="query"))

        http_methods: list[str] = []

        # 1. Spring Boot Mapping detection
        for ann_name, (ann_kwargs, ann_args) in method_annotations.items():
            if ann_name in _SPRING_MAPPINGS:
                frameworks.add("spring")
                http_method = _SPRING_MAPPINGS[ann_name]
                http_methods.append(http_method)
                route_sub = ann_kwargs.get("path") or ann_kwargs.get("value") or (ann_args[0] if ann_args else "")
                if isinstance(route_sub, list):
                    route_sub = route_sub[0] if route_sub else ""
                full_path = join_route(class_prefix, str(route_sub or ""))
                entry_points.append(
                    EntryPoint(
                        kind="http_route",
                        framework="spring",
                        handler=f"{source.rel}::{qualname}",
                        file=source.rel,
                        line=line,
                        name=f"{http_method} {full_path}",
                        method=http_method,
                        path=full_path,
                        params=list(params),
                        middleware=list(middleware),
                    )
                )
            elif ann_name == "RequestMapping":
                frameworks.add("spring")
                route_sub = ann_kwargs.get("path") or ann_kwargs.get("value") or (ann_args[0] if ann_args else "")
                method_spec = ann_kwargs.get("method")
                req_methods = ["GET"]
                if method_spec:
                    m = re.findall(r"RequestMethod\.([A-Z]+)", str(method_spec))
                    if m:
                        req_methods = m
                for req_m in req_methods:
                    http_methods.append(req_m)
                    full_path = join_route(class_prefix, str(route_sub or ""))
                    entry_points.append(
                        EntryPoint(
                            kind="http_route",
                            framework="spring",
                            handler=f"{source.rel}::{qualname}",
                            file=source.rel,
                            line=line,
                            name=f"{req_m} {full_path}",
                            method=req_m,
                            path=full_path,
                            params=list(params),
                            middleware=list(middleware),
                        )
                    )

        # 2. JAX-RS detection
        jaxrs_method = next((_JAXRS_METHODS[a] for a in method_annotations if a in _JAXRS_METHODS), None)
        if jaxrs_method is not None or "Path" in method_annotations:
            frameworks.add("jax-rs")
            m_verb = jaxrs_method or "GET"
            http_methods.append(m_verb)
            sub_path = ""
            if "Path" in method_annotations:
                p_kwargs, p_args = method_annotations["Path"]
                sub_path = str(p_kwargs.get("value") or (p_args[0] if p_args else ""))
            full_path = join_route(class_prefix, sub_path)
            entry_points.append(
                EntryPoint(
                    kind="http_route",
                    framework="jax-rs",
                    handler=f"{source.rel}::{qualname}",
                    file=source.rel,
                    line=line,
                    name=f"{m_verb} {full_path}",
                    method=m_verb,
                    path=full_path,
                    params=list(params),
                    middleware=list(middleware),
                )
            )

        # 3. Servlet detection (doGet, doPost, etc.)
        if is_servlet and method_name in _SERVLET_METHODS:
            frameworks.add("servlet")
            s_verb = _SERVLET_METHODS[method_name]
            http_methods.append(s_verb)
            paths_to_expose = servlet_paths if servlet_paths else ["/servlet"]
            for sp in paths_to_expose:
                entry_points.append(
                    EntryPoint(
                        kind="http_route",
                        framework="servlet",
                        handler=f"{source.rel}::{qualname}",
                        file=source.rel,
                        line=line,
                        name=f"{s_verb} {sp}",
                        method=s_verb,
                        path=sp,
                        params=list(params),
                        middleware=list(middleware),
                    )
                )

        functions.append(
            Function(
                qualname=qualname,
                line=line,
                end_line=end_line,
                params=params,
                decorators=decorators,
                calls=sorted(set(calls)),
                side_effects=side_effects,
                docstring=docstring,
                is_async=is_async,
                http_methods=sorted(set(http_methods)),
                call_sites=call_sites,
            )
        )

    def _walk_body(
        self,
        node: Any,
        calls: list[str],
        call_sites: dict[str, int],
        side_effects: list[SideEffect],
        servlet_query_params: list[str],
        module_imports: dict[str, str],
    ) -> None:
        stack = [node]
        while stack:
            curr = stack.pop()
            if curr.type == "method_invocation":
                self._handle_invocation(
                    curr,
                    calls=calls,
                    call_sites=call_sites,
                    side_effects=side_effects,
                    servlet_query_params=servlet_query_params,
                    module_imports=module_imports,
                )
            for child in reversed(curr.named_children):
                stack.append(child)

    def _handle_invocation(
        self,
        inv_node: Any,
        calls: list[str],
        call_sites: dict[str, int],
        side_effects: list[SideEffect],
        servlet_query_params: list[str],
        module_imports: dict[str, str],
    ) -> None:
        name_node = inv_node.child_by_field_name("name")
        method_name = _text(name_node) if name_node else ""
        if not method_name:
            return

        obj_node = inv_node.child_by_field_name("object")
        obj_text = _text(obj_node) if obj_node else ""
        full_call = f"{obj_text}.{method_name}" if obj_text else method_name
        line = _line(inv_node)

        calls.append(full_call)
        if full_call not in call_sites:
            call_sites[full_call] = line

        # Servlet getParameter
        if method_name in ("getParameter", "getParameterValues") and obj_text in ("req", "request"):
            args_node = _child_of_type(inv_node, "argument_list")
            if args_node is not None and args_node.named_children:
                param_arg = _string_value(args_node.named_children[0])
                if param_arg:
                    servlet_query_params.append(param_arg)

        effect = self._classify_call(inv_node, method_name, obj_text, full_call, line, module_imports)
        if effect is not None:
            side_effects.append(effect)

    def _classify_call(
        self,
        inv_node: Any,
        method_name: str,
        obj_text: str,
        full_call: str,
        line: int,
        module_imports: dict[str, str],
    ) -> SideEffect | None:
        args_node = _child_of_type(inv_node, "argument_list")
        first_arg = None
        if args_node is not None and args_node.named_children:
            first_arg = _string_value(args_node.named_children[0])

        # 1. SQL / Database queries
        if first_arg:
            parsed_sql = sql_effect(first_arg)
            if parsed_sql is not None:
                op, detail, verb = parsed_sql
                return SideEffect(
                    kind="db",
                    op=op,
                    call=full_call,
                    line=line,
                    detail=detail,
                    verb=verb,
                )

        if _DB_RECEIVER_RE.search(obj_text):
            if method_name in _DB_READ_METHODS or method_name.startswith(("find", "get", "count")):
                return SideEffect(
                    kind="db",
                    op="read",
                    call=full_call,
                    line=line,
                    detail=obj_text,
                    verb=method_name,
                )
            if method_name in _DB_WRITE_METHODS or method_name.startswith(("save", "update", "delete", "remove", "insert")):
                return SideEffect(
                    kind="db",
                    op="write",
                    call=full_call,
                    line=line,
                    detail=obj_text,
                    verb=method_name,
                )

        # 2. Outbound HTTP calls
        if _HTTP_CLIENT_RECEIVERS.search(obj_text) or "HttpClient" in full_call:
            if method_name in ("getForObject", "getForEntity", "get"):
                return SideEffect(
                    kind="http_out",
                    op="read",
                    call=full_call,
                    line=line,
                    detail=first_arg or "",
                    verb="GET",
                )
            if method_name in ("postForObject", "postForEntity", "post", "put", "delete", "patchForObject"):
                verb = method_name.replace("ForObject", "").replace("ForEntity", "").upper()
                return SideEffect(
                    kind="http_out",
                    op="write",
                    call=full_call,
                    line=line,
                    detail=first_arg or "",
                    verb=verb,
                )
            if method_name == "exchange":
                return SideEffect(
                    kind="http_out",
                    op="unknown",
                    call=full_call,
                    line=line,
                    detail=first_arg or "",
                    verb="exchange",
                )

        # 3. Email
        if _EMAIL_RECEIVER_RE.search(obj_text) or "mail" in method_name.lower():
            if method_name in ("send", "sendMail", "sendEmail"):
                return SideEffect(
                    kind="email",
                    op="write",
                    call=full_call,
                    line=line,
                    verb="send",
                )

        # 4. Payment
        is_payment_import = module_imports.get(obj_text, "").startswith(("com.stripe", "com.paypal"))
        if is_payment_import or _PAYMENT_RECEIVER_RE.search(obj_text) or _PAYMENT_RECEIVER_RE.search(full_call):
            if method_name in ("create", "refund", "capture", "authorize", "execute"):
                return SideEffect(
                    kind="payment",
                    op="write",
                    call=full_call,
                    line=line,
                    detail=obj_text,
                    verb=method_name,
                )
            if method_name in ("retrieve", "get", "list", "search"):
                return SideEffect(
                    kind="payment",
                    op="read",
                    call=full_call,
                    line=line,
                    detail=obj_text,
                    verb=method_name,
                )

        # 5. Message Queues (JMS, Kafka, RabbitMQ)
        if _QUEUE_RECEIVER_RE.search(obj_text):
            if method_name in ("send", "convertAndSend", "publish"):
                return SideEffect(
                    kind="queue",
                    op="write",
                    call=full_call,
                    line=line,
                    detail=first_arg or obj_text,
                    verb=method_name,
                )

        # 6. File writes
        if "Files." in full_call or obj_text in ("FileOutputStream", "FileWriter"):
            if method_name in ("write", "delete", "deleteIfExists", "copy", "move", "createFile", "createDirectory"):
                return SideEffect(
                    kind="file",
                    op="write",
                    call=full_call,
                    line=line,
                    verb=method_name,
                )

        # 7. Auth writes/reads
        if "passwordEncoder" in obj_text or "bcrypt" in obj_text.lower():
            if method_name in ("encode", "hash"):
                return SideEffect(kind="auth", op="write", call=full_call, line=line, verb="hash")
            if method_name in ("matches", "verify"):
                return SideEffect(kind="auth", op="read", call=full_call, line=line, verb="verify")

        return None
