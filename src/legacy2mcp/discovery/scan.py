"""
Static (AST-only) scan of a repo's Python source for the three shapes
legacy2mcp already knows how to wrap: a SOAP client, a database/ORM layer,
or a plain HTTP route table (Flask/FastAPI). No network access, no
dependency on the scanned repo's own dependencies being importable --
everything here works directly off `ast.parse()`.

This is deliberately a heuristic, not a type checker: it resolves the easy,
common cases (literal paths, simple type-hinted params, same-file models)
and flags anything it can't pin down via `RouteParam.needs_review` /
`DbFinding.needs_review` rather than guessing silently. `discovery/llm.py`
can optionally improve on the flagged bits; `discovery/generate.py` always
surfaces them as `# TODO` comments either way.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIR_NAMES = {
    ".git", "venv", ".venv", "env", ".env", "node_modules", "__pycache__",
    "site-packages", "dist", "build", "migrations", "tests", "test",
}

_HTTP_METHOD_ATTRS = {"get", "post", "put", "patch", "delete"}
_SKIP_PARAM_NAMES = {"self", "request", "req", "response", "res", "db", "session"}
_FLASK_TYPED_PATH_RE = re.compile(r"<(?:\w+:)?(\w+)>")
_BRACE_PATH_RE = re.compile(r"\{(\w+)\}")

_SIMPLE_TYPE_MAP = {"int": "integer", "float": "number", "bool": "boolean", "str": "string"}

_DB_DRIVER_DIALECT = {
    "psycopg2": "postgres",
    "psycopg": "postgres",
    "pyodbc": "mssql",
    "oracledb": "oracle",
    "cx_Oracle": "oracle",
}


@dataclass
class RouteParam:
    name: str
    location: str = "query"  # "path" | "query" | "body"
    type: str = "string"
    required: bool = True
    needs_review: bool = False


@dataclass
class RouteFinding:
    name: str
    method: str
    path: str  # normalized to "{param}" placeholders
    description: str
    params: list[RouteParam] = field(default_factory=list)
    file: str = ""


@dataclass
class SoapFinding:
    wsdl_url: str | None
    file: str


@dataclass
class DbTableFinding:
    table: str
    columns: list[str]
    file: str


@dataclass
class DbFinding:
    dialect: str | None
    tables: list[DbTableFinding] = field(default_factory=list)
    needs_review: bool = False
    # Set when the tables came from a live database (`init --db-dialect`): the
    # environment variable that holds its DSN, and the schema that was read.
    dsn_env: str | None = None
    db_schema: str | None = None


@dataclass
class ScanResult:
    routes: list[RouteFinding] = field(default_factory=list)
    soap: SoapFinding | None = None
    db: DbFinding | None = None


def _iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIR_NAMES for part in path.relative_to(root).parts[:-1]):
            continue
        yield path


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
    except (SyntaxError, ValueError):
        return None


def _str_const(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_name(node: ast.AST) -> str | None:
    """Best-effort dotted name of a Call's func, e.g. 'zeep.Client' or 'Client'."""
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _normalize_path(raw: str) -> tuple[str, list[str]]:
    """Flask '<int:id>' / '<id>' and FastAPI '{id}' -> uniform '{id}' placeholders."""
    names = _FLASK_TYPED_PATH_RE.findall(raw) or _BRACE_PATH_RE.findall(raw)
    normalized = _FLASK_TYPED_PATH_RE.sub(lambda m: "{" + m.group(1) + "}", raw)
    return normalized, names


def _path_param_type(raw_path: str, name: str) -> str:
    match = re.search(rf"<(\w+):{re.escape(name)}>", raw_path)
    if match:
        return _SIMPLE_TYPE_MAP.get(match.group(1), "string")
    return "string"


def _annotation_simple_type(annotation: ast.AST | None) -> str | None:
    if isinstance(annotation, ast.Name) and annotation.id in _SIMPLE_TYPE_MAP:
        return _SIMPLE_TYPE_MAP[annotation.id]
    return None


def _is_depends_default(default: ast.AST | None) -> bool:
    return isinstance(default, ast.Call) and _call_name(default.func) in {"Depends", "fastapi.Depends"}


def _find_class(tree: ast.Module, name: str) -> ast.ClassDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _model_fields(class_def: ast.ClassDef) -> list[RouteParam]:
    """Dataclass/Pydantic-style body model: every annotated class attribute
    becomes a scalar body param. Non-simple-typed fields fall back to string
    and are flagged for manual review rather than silently mistyped."""
    params: list[RouteParam] = []
    for stmt in class_def.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            simple = _annotation_simple_type(stmt.annotation)
            params.append(
                RouteParam(
                    name=stmt.target.id,
                    location="body",
                    type=simple or "string",
                    required=stmt.value is None,
                    needs_review=simple is None,
                )
            )
    return params


def _request_args_params(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[RouteParam]:
    """Flask handlers typically don't take query params as function args at
    all -- they read `request.args` in the body (see the real-world test
    fixture in tests/mock_rest_service.py). Catch the common
    `request.args.get("x")` / `request.args["x"]` shapes so those params
    aren't silently dropped."""
    found: dict[str, RouteParam] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and _call_name(node.func) == "request.args.get" and node.args:
            # .get(...) never raises on a missing key, so this is always optional.
            name = _str_const(node.args[0])
            if name and name not in found:
                found[name] = RouteParam(name=name, location="query", type="string", required=False)
        elif isinstance(node, ast.Subscript) and _call_name(node.value) == "request.args":
            name = _str_const(node.slice)
            if name and name not in found:
                found[name] = RouteParam(name=name, location="query", type="string", required=True)
    return list(found.values())


def _route_params(tree: ast.Module, func: ast.FunctionDef | ast.AsyncFunctionDef, path_names: list[str], raw_path: str) -> list[RouteParam]:
    params: list[RouteParam] = []
    args = func.args
    positional = args.posonlyargs + args.args
    defaults_by_arg = dict(zip(positional[len(positional) - len(args.defaults):], args.defaults)) if args.defaults else {}

    for arg in positional:
        if arg.arg in _SKIP_PARAM_NAMES:
            continue
        if arg.arg in path_names:
            params.append(RouteParam(name=arg.arg, location="path", type=_path_param_type(raw_path, arg.arg), required=True))
            continue
        default = defaults_by_arg.get(arg)
        if _is_depends_default(default):
            continue

        simple = _annotation_simple_type(arg.annotation)
        if simple is not None:
            params.append(RouteParam(name=arg.arg, location="query", type=simple, required=default is None))
            continue

        # Non-simple annotation (e.g. a Pydantic model / dataclass) on a
        # body-carrying method is treated as the request body; resolve its
        # fields if the class is defined in this same file.
        model_name = arg.annotation.id if isinstance(arg.annotation, ast.Name) else None
        if model_name:
            model_class = _find_class(tree, model_name)
            if model_class is not None:
                params.extend(_model_fields(model_class))
                continue

        params.append(RouteParam(name=arg.arg, location="query", type="string", required=default is None, needs_review=True))

    existing_names = {p.name for p in params}
    params.extend(p for p in _request_args_params(func) if p.name not in existing_names)
    return params


def _decorator_route(dec: ast.AST) -> tuple[str, list[str]] | None:
    """Return (http_method, [raw_path]) for one matched route decorator, or None."""
    if not isinstance(dec, ast.Call):
        return None
    name = _call_name(dec.func)
    if not name:
        return None
    attr = name.rsplit(".", 1)[-1]

    if attr == "route":
        raw_path = _str_const(dec.args[0]) if dec.args else None
        if raw_path is None:
            return None
        methods = ["GET"]
        for kw in dec.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                methods = [v.value.upper() for v in kw.value.elts if isinstance(v, ast.Constant) and isinstance(v.value, str)] or ["GET"]
        return methods, raw_path  # type: ignore[return-value]

    if attr in _HTTP_METHOD_ATTRS:
        raw_path = _str_const(dec.args[0]) if dec.args else None
        if raw_path is None:
            return None
        return [attr.upper()], raw_path  # type: ignore[return-value]

    return None


def _scan_routes(tree: ast.Module, file_label: str) -> list[RouteFinding]:
    findings: list[RouteFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            matched = _decorator_route(dec)
            if matched is None:
                continue
            methods, raw_path = matched
            normalized_path, path_names = _normalize_path(raw_path)
            params = _route_params(tree, node, path_names, raw_path)
            description = (ast.get_docstring(node) or "").strip().splitlines()[0] if ast.get_docstring(node) else ""

            for method in methods:
                suffix = node.name if len(methods) == 1 else f"{node.name}_{method.lower()}"
                findings.append(
                    RouteFinding(
                        name=suffix,
                        method=method,
                        path=normalized_path,
                        description=description,
                        params=params,
                        file=file_label,
                    )
                )
    return findings


def _scan_soap(tree: ast.Module, file_label: str) -> SoapFinding | None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        if name in {"zeep.Client", "Client"} and node.args:
            wsdl_url = _str_const(node.args[0])
            if wsdl_url or name == "zeep.Client":
                return SoapFinding(wsdl_url=wsdl_url, file=file_label)
    return None


def _scan_db(tree: ast.Module, file_label: str) -> tuple[str | None, list[DbTableFinding]]:
    dialect: str | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _DB_DRIVER_DIALECT:
                    dialect = _DB_DRIVER_DIALECT[root]
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in _DB_DRIVER_DIALECT:
                dialect = _DB_DRIVER_DIALECT[root]

    tables: list[DbTableFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        looks_like_model = any(
            (isinstance(base, ast.Name) and base.id in {"Base", "Model"})
            or (isinstance(base, ast.Attribute) and base.attr == "Model")
            for base in node.bases
        )
        if not looks_like_model:
            continue

        table_name = node.name.lower()
        columns: list[str] = []
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                target_name = stmt.targets[0].id
                if target_name == "__tablename__":
                    value = _str_const(stmt.value)
                    if value:
                        table_name = value
                elif isinstance(stmt.value, ast.Call) and _call_name(stmt.value.func) in {"Column", "sqlalchemy.Column"}:
                    columns.append(target_name)

        if columns:
            tables.append(DbTableFinding(table=table_name, columns=columns, file=file_label))

    return dialect, tables


def scan_repo(root: Path) -> ScanResult:
    result = ScanResult()
    db_tables: list[DbTableFinding] = []
    db_dialect: str | None = None

    for path in _iter_python_files(root):
        tree = _parse(path)
        if tree is None:
            continue
        file_label = str(path.relative_to(root))

        result.routes.extend(_scan_routes(tree, file_label))

        if result.soap is None:
            result.soap = _scan_soap(tree, file_label)

        dialect, tables = _scan_db(tree, file_label)
        if dialect:
            db_dialect = dialect
        db_tables.extend(tables)

    if db_tables:
        result.db = DbFinding(dialect=db_dialect, tables=db_tables, needs_review=db_dialect is None)

    return result
