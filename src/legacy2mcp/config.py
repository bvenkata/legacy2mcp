"""
Configuration schema for legacy2mcp.

Config is a single YAML file. Anything secret (passwords, API keys,
connection strings) is referenced by *_env fields and read from the
process environment at startup -- never written into the YAML
directly. This keeps config files safe to commit to a repo alongside
the app.

v0.1 implements the "soap" adapter type. v0.2 adds "db" (Postgres /
SQL Server / Oracle, table allowlist + named parameterized queries
only -- no arbitrary SQL from the LLM, ever). "queue" is accepted here
(so config files can be written forward-compatibly) but
`build_adapter()` raises a clear NotImplementedError for it today.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


class ConfigError(Exception):
    """Raised when a config file is missing, unreadable, not valid YAML, or
    fails schema validation -- one exception type the CLI can turn into a
    clean `error: ...` line, same idea as `ContractError` for contracts."""


class BasicAuthConfig(BaseModel):
    type: Literal["basic"] = "basic"
    username: str
    password_env: str

    def resolve_password(self) -> str:
        value = os.environ.get(self.password_env)
        if value is None:
            raise ValueError(
                f"Environment variable '{self.password_env}' is not set "
                f"(required for basic auth)."
            )
        return value


class NoAuthConfig(BaseModel):
    type: Literal["none"] = "none"


class BearerAuthConfig(BaseModel):
    type: Literal["bearer"] = "bearer"
    token_env: str

    def resolve_token(self) -> str:
        value = os.environ.get(self.token_env)
        if value is None:
            raise ValueError(
                f"Environment variable '{self.token_env}' is not set "
                f"(required for bearer auth)."
            )
        return value


class OAuth2AuthConfig(BaseModel):
    type: Literal["oauth2"] = "oauth2"
    file: str | None = None
    token_url: str | None = None
    client_id: str | None = None
    client_secret_env: str | None = None
    scope: str | None = None

    def resolve_credentials(self) -> tuple[str, str, str, str | None]:
        if self.file:
            path = Path(self.file)
            if not path.is_file():
                raise ValueError(f"Auth file not found: '{self.file}'")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as exc:
                raise ValueError(f"Failed to read auth file '{self.file}': {exc}") from exc

            token_url = data.get("token_url") or data.get("url") or self.token_url
            client_id = data.get("client_id") or self.client_id
            client_secret = data.get("client_secret") or data.get("password")
            scope = data.get("scope") or self.scope

            if not token_url:
                raise ValueError(f"Auth file '{self.file}' is missing 'url' or 'token_url'.")
            if not client_id:
                raise ValueError(f"Auth file '{self.file}' is missing 'client_id'.")
            if not client_secret:
                raise ValueError(f"Auth file '{self.file}' is missing 'password' or 'client_secret'.")
            return str(token_url), str(client_id), str(client_secret), scope

        if not self.token_url:
            raise ValueError("OAuth2 auth requires 'token_url' or 'file'.")
        if not self.client_id:
            raise ValueError("OAuth2 auth requires 'client_id' or 'file'.")
        if not self.client_secret_env:
            raise ValueError("OAuth2 auth requires 'client_secret_env' or 'file'.")

        secret = os.environ.get(self.client_secret_env)
        if not secret:
            raise ValueError(
                f"Environment variable '{self.client_secret_env}' is not set "
                f"(required for oauth2 auth)."
            )
        return self.token_url, self.client_id, secret, self.scope


AuthConfig = BasicAuthConfig | BearerAuthConfig | OAuth2AuthConfig | NoAuthConfig


class SoapAdapterSettings(BaseModel):
    wsdl_url: str
    auth: AuthConfig = Field(default_factory=NoAuthConfig)
    include_operations: list[str] | None = None
    exclude_operations: list[str] = Field(default_factory=list)
    # Safety default: SOAP operations that look like writes (Create/Update/
    # Delete/Cancel/...) require this flag before they're exposed at all.
    allow_write_operations: bool = False
    timeout_seconds: float = 15.0


class DbTableSettings(BaseModel):
    """One allowlisted table. Columns are introspected from the live DB schema
    at startup -- not hand-maintained here -- so tools can't drift from the
    database the way the SOAP adapter's tools can't drift from the WSDL."""

    name: str
    db_schema: str | None = Field(default=None, alias="schema")
    # Operations exposed for this table. "insert" additionally requires the
    # adapter-level allow_write_operations: true (defense in depth on top of
    # this already-explicit allowlist).
    operations: list[Literal["select", "insert"]] = Field(default_factory=lambda: ["select"])
    # Column allowlist. None = all introspected columns are exposed.
    columns: list[str] | None = None
    # Subset of `columns` usable as equality filters on select_<table>.
    filterable_columns: list[str] = Field(default_factory=list)
    max_rows: int = 200

    model_config = {"populate_by_name": True}


class DbQueryParamSettings(BaseModel):
    name: str
    type: Literal["string", "integer", "number", "boolean"] = "string"
    required: bool = True


class DbQuerySettings(BaseModel):
    """A named, parameterized query. `sql` is written by whoever owns the
    config file, with `:param_name` placeholders -- never by the LLM, which
    only ever supplies the placeholder values declared in `params`."""

    name: str
    sql: str
    description: str = ""
    params: list[DbQueryParamSettings] = Field(default_factory=list)
    # Write queries (INSERT/UPDATE/DELETE) also require allow_write_operations.
    write: bool = False
    max_rows: int = 200


class DbAdapterSettings(BaseModel):
    dialect: Literal["postgres", "mssql", "oracle"]
    dsn_env: str
    allow_write_operations: bool = False
    tables: list[DbTableSettings] = Field(default_factory=list)
    queries: list[DbQuerySettings] = Field(default_factory=list)
    timeout_seconds: float = 15.0

    def resolve_dsn(self) -> str:
        value = os.environ.get(self.dsn_env)
        if value is None:
            raise ValueError(
                f"Environment variable '{self.dsn_env}' is not set (required for the DB connection string)."
            )
        return value


class RestParamSettings(BaseModel):
    """One parameter of a REST endpoint, placed into the path, the query
    string, or a JSON request body at invoke time depending on `location`."""

    name: str
    location: Literal["path", "query", "body"] = "query"
    type: Literal["string", "integer", "number", "boolean"] = "string"
    required: bool = True
    # A closed set of values this param can take, when known (e.g. from an
    # OpenAPI spec's `enum` or a narrow set of observed values) -- enforced
    # by security/validation.py, not just descriptive. Comes from
    # `legacy2mcp discover` + `init --from-contract`; hand-written configs
    # leave it unset.
    enum: list[str] | None = None


class RestEndpointSettings(BaseModel):
    """One allowlisted REST endpoint. Unlike the SOAP/DB adapters there is no
    live schema to introspect a plain HTTP API from, so the shape here is
    whatever `legacy2mcp init` discovered from the source (or whatever you
    write by hand) -- there is nothing else to validate it against."""

    name: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    # e.g. "/orders/{id}" -- path params are matched against params[].location == "path"
    path: str
    description: str = ""
    params: list[RestParamSettings] = Field(default_factory=list)
    # Non-GET endpoints also require allow_write_operations, same posture as
    # the SOAP/DB adapters' write gating.
    write: bool = False
    # The following three are only ever populated by `legacy2mcp discover` +
    # `init --from-contract` (a Postman collection, a local OpenAPI file, a
    # HAR capture) -- how much to trust this finding, what error responses
    # are known, and whether a human should look at it before relying on it.
    # Left unset by the plain scan/OpenAPI-URL path, which has no concept of
    # "confidence" beyond its existing needs_review-per-param flagging.
    confidence: Literal["high", "medium", "low"] | None = None
    error_codes: list[str] = Field(default_factory=list)
    needs_review: bool = False


class RestAdapterSettings(BaseModel):
    base_url: str
    auth: AuthConfig = Field(default_factory=NoAuthConfig)
    allow_write_operations: bool = False
    timeout_seconds: float = 15.0
    endpoints: list[RestEndpointSettings] = Field(default_factory=list)


class AdapterConfig(BaseModel):
    id: str
    type: Literal["soap", "db", "rest", "queue"]
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    def soap_settings(self) -> SoapAdapterSettings:
        if self.type != "soap":
            raise ValueError(f"Adapter '{self.id}' is type '{self.type}', not 'soap'.")
        return SoapAdapterSettings.model_validate(self.config)

    def db_settings(self) -> DbAdapterSettings:
        if self.type != "db":
            raise ValueError(f"Adapter '{self.id}' is type '{self.type}', not 'db'.")
        return DbAdapterSettings.model_validate(self.config)

    def rest_settings(self) -> RestAdapterSettings:
        if self.type != "rest":
            raise ValueError(f"Adapter '{self.id}' is type '{self.type}', not 'rest'.")
        return RestAdapterSettings.model_validate(self.config)


class AuditConfig(BaseModel):
    enabled: bool = True
    path: str = "./legacy2mcp-audit.log"


class SecurityConfig(BaseModel):
    audit: AuditConfig = Field(default_factory=AuditConfig)


class ServerConfig(BaseModel):
    name: str = "legacy2mcp"
    transport: Literal["stdio", "http"] = "stdio"
    host: str = "0.0.0.0"
    port: int = 8000


class Legacy2McpConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    adapters: list[AdapterConfig] = Field(default_factory=list)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    @field_validator("adapters")
    @classmethod
    def _unique_adapter_ids(cls, adapters: list[AdapterConfig]) -> list[AdapterConfig]:
        seen = set()
        for a in adapters:
            if a.id in seen:
                raise ValueError(f"Duplicate adapter id: '{a.id}'")
            seen.add(a.id)
        return adapters


def load_config(path: str) -> Legacy2McpConfig:
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: '{path}'") from exc
    except OSError as exc:
        raise ConfigError(f"could not read config file '{path}': {exc.strerror or exc}") from exc

    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        where = ""
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            where = f" (line {mark.line + 1}, column {mark.column + 1})"
        problem = getattr(exc, "problem", None) or "parse error"
        raise ConfigError(f"config file '{path}' is not valid YAML{where}: {problem}") from exc

    try:
        return Legacy2McpConfig.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc']) or '(top level)'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"config file '{path}' is invalid: {problems}") from exc
