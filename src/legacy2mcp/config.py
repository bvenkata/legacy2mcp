"""
Configuration schema for legacy2mcp.

Config is a single YAML file. Anything secret (passwords, API keys)
is referenced by *_env fields and read from the process environment
at startup -- never written into the YAML directly. This keeps
config files safe to commit to a repo alongside the app.

v0.1 only implements the "soap" adapter type. "db" and "queue" are
accepted here (so config files can be written forward-compatibly and
the roadmap in the README is credible) but `build_adapter()` raises a
clear NotImplementedError for them today rather than pretending to
support something that doesn't exist yet.
"""

from __future__ import annotations

import os
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


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


AuthConfig = BasicAuthConfig | NoAuthConfig


class SoapAdapterSettings(BaseModel):
    wsdl_url: str
    auth: AuthConfig = Field(default_factory=NoAuthConfig)
    include_operations: list[str] | None = None
    exclude_operations: list[str] = Field(default_factory=list)
    # Safety default: SOAP operations that look like writes (Create/Update/
    # Delete/Cancel/...) require this flag before they're exposed at all.
    allow_write_operations: bool = False
    timeout_seconds: float = 15.0


class AdapterConfig(BaseModel):
    id: str
    type: Literal["soap", "db", "queue"]
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    def soap_settings(self) -> SoapAdapterSettings:
        if self.type != "soap":
            raise ValueError(f"Adapter '{self.id}' is type '{self.type}', not 'soap'.")
        return SoapAdapterSettings.model_validate(self.config)


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
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Legacy2McpConfig.model_validate(raw)
