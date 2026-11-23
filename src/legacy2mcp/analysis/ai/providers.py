"""
LLM providers for `legacy2mcp analyze`, behind one small interface:
`complete_json(system, user, schema, max_tokens) -> (dict, Usage)`.

Bring your own key -- nothing here ever ships with credentials:
- anthropic: the official `anthropic` SDK (install `legacy2mcp[ai]`). It
  resolves credentials itself (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an
  `ant auth login` profile), or reads the env var named by --api-key-env.
- openai: any OpenAI-compatible Chat Completions endpoint over `requests`,
  the same approach `legacy2mcp init` already uses (OPENAI_API_KEY or
  --api-key-env; --base-url for gateways).
- mock: deterministic and offline, for tests and demos.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol

from legacy2mcp.discovery.llm import DEFAULT_BASE_URL as OPENAI_BASE_URL
from legacy2mcp.discovery.llm import DEFAULT_MODEL as OPENAI_DEFAULT_MODEL

ANTHROPIC_DEFAULT_MODEL = "claude-opus-5-5"
# Models that accept server-side refusal fallbacks (`fallbacks: "default"`).
_FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ProviderError(Exception):
    """A provider call failed; callers keep the rule-based result."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


class Provider(Protocol):
    name: str
    model: str

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int) -> tuple[dict, Usage]: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None, api_key_env: str | None = None):
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError('the anthropic provider needs the AI extra: pip install "legacy2mcp[ai]"') from exc
        self._anthropic = anthropic
        self.model = model or ANTHROPIC_DEFAULT_MODEL
        key = None
        if api_key_env:
            key = os.environ.get(api_key_env)
            if not key:
                raise ProviderError(f"environment variable {api_key_env} is not set")
        self.client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int) -> tuple[dict, Usage]:
        a = self._anthropic
        kwargs: dict = {}
        if self.model in _FALLBACK_MODELS:
            # A safety-classifier decline is re-run server-side on Anthropic's
            # recommended fallback model instead of failing the step.
            kwargs = {"betas": [_FALLBACK_BETA], "fallbacks": "default"}
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
                **kwargs,
            )
        except a.AuthenticationError as exc:
            raise ProviderError("Anthropic rejected the credentials (set ANTHROPIC_API_KEY or run `ant auth login`)") from exc
        except a.PermissionDeniedError as exc:
            raise ProviderError(f"Anthropic denied the request: {exc.message}") from exc
        except a.NotFoundError as exc:
            raise ProviderError(f"unknown Anthropic model '{self.model}'") from exc
        except a.RateLimitError as exc:
            raise ProviderError("Anthropic rate limit reached (the SDK already retried); try again later") from exc
        except a.BadRequestError as exc:
            raise ProviderError(f"Anthropic rejected the request: {exc.message}") from exc
        except a.APIStatusError as exc:
            raise ProviderError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise ProviderError("could not reach the Anthropic API (network error)") from exc
        usage = Usage(response.usage.input_tokens or 0, response.usage.output_tokens or 0)
        if response.stop_reason == "refusal":
            raise ProviderError("the model declined this request; keeping rule-based results")
        if response.stop_reason == "max_tokens":
            raise ProviderError("response hit max_tokens before finishing; keeping rule-based results")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text), usage
        except ValueError as exc:
            raise ProviderError("model returned invalid JSON") from exc


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str | None = None, api_key_env: str | None = None, base_url: str | None = None):
        env = api_key_env or "OPENAI_API_KEY"
        key = os.environ.get(env)
        if not key:
            raise ProviderError(f"environment variable {env} is not set")
        self.api_key = key
        self.model = model or OPENAI_DEFAULT_MODEL
        self.base_url = (base_url or OPENAI_BASE_URL).rstrip("/")

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int) -> tuple[dict, Usage]:
        import requests

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}},
        }
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=120,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"could not reach {self.base_url}: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise ProviderError(f"OpenAI-compatible API returned HTTP {response.status_code}")
        try:
            body = response.json()
            choice = body["choices"][0]
            usage = Usage(body.get("usage", {}).get("prompt_tokens", 0), body.get("usage", {}).get("completion_tokens", 0))
            if choice.get("finish_reason") == "length":
                raise ProviderError("response hit max_tokens before finishing; keeping rule-based results")
            return json.loads(choice["message"]["content"]), usage
        except ProviderError:
            raise
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("OpenAI-compatible API returned an unexpected response") from exc


class MockProvider:
    """Deterministic offline provider: echoes structure back as plain names.
    Used by the test suite (no network, no tokens) and handy for demos."""

    name = "mock"

    def __init__(self, model: str | None = None, **_: object):
        self.model = model or "mock-1"
        self.calls: list[dict] = []

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int) -> tuple[dict, Usage]:
        request = json.loads(user)
        self.calls.append(request)
        usage = Usage(input_tokens=len(system + user) // 4, output_tokens=50)
        if request.get("task") == "name_flows":
            flows = []
            for f in request["flows"]:
                words = (f.get("path") or f["entry"]).strip("/").replace("/", " ").replace("{", "").replace("}", "")
                flows.append({
                    "id": f["id"],
                    "name": f"Mock {f.get('method') or f['kind']} {words}".strip(),
                    "description": f"Mock description for {f['entry']}.",
                    "risk_notes": "Mock note." if f["risk"] == "high_risk" else "",
                    "tool_name": f"mock_{f['id'].replace('-', '_')}" if f["kind"] == "http_route" else "",
                    "tool_description": f"Mock tool for {f['entry']}." if f["kind"] == "http_route" else "",
                })
            return {"flows": flows}, usage
        if request.get("task") == "orchestration":
            return {
                "agents": [{"id": a["id"], "name": f"Mock {a['id']}", "description": "Mock agent."} for a in request["agents"]],
                "multimodal": [],
                "notes": "Mock orchestration notes.",
            }, usage
        return {}, usage


def available_provider() -> str | None:
    """The provider to use when --provider isn't given: whichever key is set."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return None


def get_provider(name: str, model: str | None = None, api_key_env: str | None = None,
                 base_url: str | None = None) -> Provider:
    if name == "anthropic":
        return AnthropicProvider(model=model, api_key_env=api_key_env)
    if name == "openai":
        return OpenAIProvider(model=model, api_key_env=api_key_env, base_url=base_url)
    if name == "mock":
        return MockProvider(model=model)
    raise ProviderError(f"unknown provider '{name}' (use anthropic or openai)")


# Approximate list prices, USD per 1M tokens (input, output), for the
# pre-run estimate only. Unknown models show tokens without a dollar figure.
PRICES = {
    "claude-fable-5-2": (8.0, 40.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    return input_tokens / 1e6 * price[0] + output_tokens / 1e6 * price[1]
