"""LLM planning interfaces.

The harness talks to models only through the ``Planner`` protocol. Both
implementations (Anthropic, and a local model served by Ollama) constrain the
response to a JSON schema so every response parses into a typed object; the
result is then validated by deterministic code — the model never makes the
final decision.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from webblock_harness.analyzer import prompts
from webblock_harness.config import Settings
from webblock_harness.domain import (
    CodeChangeProposal,
    ComponentSpec,
    FieldSpec,
    PuckOp,
    SourcePlan,
)
from webblock_harness.logging_utils import get_logger
from webblock_harness.security.untrusted import fence

log = get_logger(__name__)
_OPS = TypeAdapter(list[PuckOp])


class PlannerError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class ConfigSuggestion(BaseModel):
    path: Literal["runtime_config", "source_code", "clarification"]
    operations: list[PuckOp] = []
    questions: list[str] = []
    capability_needed: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    reasoning: str = ""


class Planner(Protocol):
    @property
    def available(self) -> bool: ...

    def suggest_config_change(
        self, request: str, site_config: dict[str, Any], registry_summary: list[dict[str, Any]], answers: list[str]
    ) -> ConfigSuggestion: ...

    def plan_source_change(
        self, request: str, registry_summary: list[dict[str, Any]], repo_files: list[str], answers: list[str]
    ) -> SourcePlan: ...

    def generate_patch(
        self, request: str, plan: SourcePlan, files: dict[str, str], registry_summary: list[dict[str, Any]]
    ) -> CodeChangeProposal: ...


class UnavailablePlanner:
    """Used when no LLM is configured: rules-only analysis, Path B cannot generate code."""

    available = False

    def _fail(self) -> Any:
        raise PlannerError(
            "llm_unavailable",
            "No LLM provider is configured (set LLM_PROVIDER=ollama or LLM_PROVIDER=anthropic).",
            retryable=True,
        )

    def suggest_config_change(self, *args: Any, **kwargs: Any) -> ConfigSuggestion:
        return self._fail()  # type: ignore[no-any-return]

    def plan_source_change(self, *args: Any, **kwargs: Any) -> SourcePlan:
        return self._fail()  # type: ignore[no-any-return]

    def generate_patch(self, *args: Any, **kwargs: Any) -> CodeChangeProposal:
        return self._fail()  # type: ignore[no-any-return]


# --------------------------------------------------------------------------
# JSON schemas for structured outputs (hand-written: strict, no free-form "any")
# --------------------------------------------------------------------------

_NSTR = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_NINT = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
_STRS = {"type": "array", "items": {"type": "string"}}

_LLM_OP = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": ["set_prop", "set_root_prop", "move_component", "add_component", "remove_component"]},
        "page": {"type": "string"},
        "component_id": _NSTR,
        "path": {"anyOf": [_STRS, {"type": "null"}]},
        "value_json": _NSTR,
        "expect_json": _NSTR,
        "before_id": _NSTR,
        "after_id": _NSTR,
        "container": _NSTR,
        "index": _NINT,
        "component_json": _NSTR,
    },
    "required": [
        "op",
        "page",
        "component_id",
        "path",
        "value_json",
        "expect_json",
        "before_id",
        "after_id",
        "container",
        "index",
        "component_json",
    ],
    "additionalProperties": False,
}

CONFIG_SUGGESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "enum": ["runtime_config", "source_code", "clarification"]},
        "operations": {"type": "array", "items": _LLM_OP},
        "questions": _STRS,
        "capability_needed": _NSTR,
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["path", "operations", "questions", "capability_needed", "confidence", "reasoning"],
    "additionalProperties": False,
}

_ENDPOINT = {
    "type": "object",
    "properties": {
        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
        "path": {"type": "string"},
        "purpose": {"type": "string"},
        "auth": {"type": "string"},
        "reuses_existing": {"type": "boolean"},
    },
    "required": ["method", "path", "purpose", "auth", "reuses_existing"],
    "additionalProperties": False,
}
_MIGRATION = {
    "type": "object",
    "properties": {"path": {"type": "string"}, "description": {"type": "string"}, "destructive": {"type": "boolean"}},
    "required": ["path", "description", "destructive"],
    "additionalProperties": False,
}
_DEPENDENCY = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "version": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["name", "version", "reason"],
    "additionalProperties": False,
}
_SCOPES = {"type": "array", "items": {"type": "string", "enum": ["frontend", "backend", "database"]}}

SOURCE_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "scopes": _SCOPES,
        "files_to_read": _STRS,
        "files_to_change": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "action": {"type": "string", "enum": ["create", "modify", "delete"]},
                    "reason": {"type": "string"},
                },
                "required": ["path", "action", "reason"],
                "additionalProperties": False,
            },
        },
        "new_components": _STRS,
        "api_endpoints": {"type": "array", "items": _ENDPOINT},
        "migrations": {"type": "array", "items": _MIGRATION},
        "dependencies": {"type": "array", "items": _DEPENDENCY},
        "integration_tests": _STRS,
        "risks": _STRS,
        "questions": _STRS,
    },
    "required": [
        "summary",
        "scopes",
        "files_to_read",
        "files_to_change",
        "new_components",
        "api_endpoints",
        "migrations",
        "dependencies",
        "integration_tests",
        "risks",
        "questions",
    ],
    "additionalProperties": False,
}

CODE_PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "scopes": _SCOPES,
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "action": {"type": "string", "enum": ["create", "modify", "delete"]},
                    "content": _NSTR,
                    "purpose": {"type": "string"},
                },
                "required": ["path", "action", "content", "purpose"],
                "additionalProperties": False,
            },
        },
        "new_components": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "source_file": {"type": "string"},
                    "fields_json": {"type": "string"},
                    "default_props_json": {"type": "string"},
                },
                "required": ["name", "label", "description", "source_file", "fields_json", "default_props_json"],
                "additionalProperties": False,
            },
        },
        "api_endpoints": {"type": "array", "items": _ENDPOINT},
        "migrations": {"type": "array", "items": _MIGRATION},
        "dependencies": {"type": "array", "items": _DEPENDENCY},
        "tests": _STRS,
        "page_operations": {"type": "array", "items": _LLM_OP},
        "notes": _STRS,
    },
    "required": [
        "summary",
        "scopes",
        "files",
        "new_components",
        "api_endpoints",
        "migrations",
        "dependencies",
        "tests",
        "page_operations",
        "notes",
    ],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# Conversion of LLM wire format into domain types
# --------------------------------------------------------------------------


def _loads(raw: str | None, what: str) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlannerError("invalid_llm_output", f"{what} is not valid JSON: {exc}") from exc


def llm_ops_to_domain(raw_ops: list[dict[str, Any]]) -> list[PuckOp]:
    converted: list[dict[str, Any]] = []
    for raw in raw_ops:
        path = [int(p) if isinstance(p, str) and p.isdigit() else p for p in (raw.get("path") or [])]
        op = raw["op"]
        base: dict[str, Any] = {"op": op, "page": raw["page"]}
        if op == "set_prop":
            base |= {"component_id": raw["component_id"], "path": path, "value": _loads(raw["value_json"], "value_json")}
            if raw.get("expect_json") is not None:
                base |= {"expect": _loads(raw["expect_json"], "expect_json"), "has_expect": True}
        elif op == "set_root_prop":
            base |= {"path": path, "value": _loads(raw["value_json"], "value_json")}
            if raw.get("expect_json") is not None:
                base |= {"expect": _loads(raw["expect_json"], "expect_json"), "has_expect": True}
        elif op == "move_component":
            base |= {
                "component_id": raw["component_id"],
                "before_id": raw.get("before_id"),
                "after_id": raw.get("after_id"),
                "to_container": raw.get("container"),
                "to_index": raw.get("index"),
            }
        elif op == "add_component":
            base |= {
                "component": _loads(raw["component_json"], "component_json"),
                "container": raw.get("container") or "content",
                "before_id": raw.get("before_id"),
                "after_id": raw.get("after_id"),
                "index": raw.get("index"),
            }
        elif op == "remove_component":
            base |= {"component_id": raw["component_id"]}
        converted.append(base)
    try:
        return _OPS.validate_python(converted)
    except ValidationError as exc:
        raise PlannerError("invalid_llm_output", f"LLM operations failed schema validation: {exc}") from exc


def proposal_from_llm(data: dict[str, Any]) -> CodeChangeProposal:
    components = []
    for c in data.get("new_components", []):
        fields_raw = _loads(c["fields_json"], "fields_json") or {}
        try:
            spec = ComponentSpec(
                name=c["name"],
                label=c["label"],
                description=c["description"],
                fields={k: FieldSpec.model_validate(v) for k, v in fields_raw.items()},
                default_props=_loads(c["default_props_json"], "default_props_json") or {},
                source_path=c["source_file"],
                export_name=c["name"],
            )
        except ValidationError as exc:
            raise PlannerError("invalid_llm_output", f"Component spec for {c.get('name')!r} is invalid: {exc}") from exc
        components.append({"spec": spec, "source_file": c["source_file"]})
    payload = {k: v for k, v in data.items() if k not in ("new_components", "page_operations")}
    try:
        return CodeChangeProposal.model_validate(
            payload | {"new_components": components, "page_operations": llm_ops_to_domain(data.get("page_operations", []))}
        )
    except ValidationError as exc:
        raise PlannerError("invalid_llm_output", f"Code proposal failed schema validation: {exc}") from exc


def editable_values(site_config: dict[str, Any], limit: int = 400) -> list[str]:
    """Flat "page | component | path = value" lines for every scalar prop (nested slots and arrays included)."""
    lines: list[str] = []

    def walk(page: str, label: str, value: Any, path: list[str]) -> None:
        if len(lines) >= limit:
            return
        if isinstance(value, dict) and "type" in value and isinstance(value.get("props"), dict):
            props = value["props"]
            comp = f"{props.get('id', '?')} ({value['type']})"
            for key, v in props.items():
                if key != "id":
                    walk(page, comp, v, [key])
        elif isinstance(value, dict):
            for key, v in value.items():
                walk(page, label, v, [*path, key])
        elif isinstance(value, list):
            for i, v in enumerate(value):
                if isinstance(v, dict) and "type" in v and isinstance(v.get("props"), dict):
                    walk(page, label, v, [])
                else:
                    walk(page, label, v, [*path, str(i)])
        else:
            lines.append(f"{page} | {label} | {json.dumps(path)} = {json.dumps(value, ensure_ascii=False)[:200]}")

    for page, data in (site_config.get("pages") or {}).items():
        for key, v in ((data.get("root") or {}).get("props") or {}).items():
            walk(page, "root (page settings, use set_root_prop)", v, [key])
        for item in data.get("content") or []:
            walk(page, "", item, [])
        for zone in (data.get("zones") or {}).values():
            for item in zone or []:
                walk(page, "", item, [])
    return lines


# --------------------------------------------------------------------------
# Anthropic implementation
# --------------------------------------------------------------------------


class _SchemaPlanner:
    """Builds the prompts and validates the answers; subclasses only implement the schema-constrained model call."""

    available = True

    def _call(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def suggest_config_change(
        self, request: str, site_config: dict[str, Any], registry_summary: list[dict[str, Any]], answers: list[str]
    ) -> ConfigSuggestion:
        return self._suggest(request, site_config, registry_summary, answers, system=prompts.CONFIG_PLANNER_SYSTEM)

    def _suggest(
        self,
        request: str,
        site_config: dict[str, Any],
        registry_summary: list[dict[str, Any]],
        answers: list[str],
        *,
        system: str,
        extra: list[str] | None = None,
    ) -> ConfigSuggestion:
        user = "\n\n".join(
            [
                "Component registry:\n" + json.dumps(registry_summary, ensure_ascii=False),
                fence("site_configuration", json.dumps(site_config, ensure_ascii=False)),
                *(extra or []),
                fence("client_request", request),
                *([fence("client_answers", "\n".join(answers))] if answers else []),
            ]
        )
        data = self._call(system, user, CONFIG_SUGGESTION_SCHEMA)
        path = data.get("path")
        if path == "runtime_config" and not data.get("operations") and data.get("questions"):
            path = "clarification"  # "an edit" with nothing to edit but a question is really a question
        try:
            return ConfigSuggestion.model_validate(
                {
                    "path": path,
                    "operations": llm_ops_to_domain(data.get("operations", [])),
                    "questions": data.get("questions", []),
                    "capability_needed": data.get("capability_needed"),
                    "confidence": min(max(float(data.get("confidence", 0)), 0.0), 1.0),
                    "reasoning": data.get("reasoning", ""),
                }
            )
        except (ValidationError, TypeError, ValueError, KeyError) as exc:
            raise PlannerError("invalid_llm_output", f"Config suggestion failed validation: {exc}") from exc

    def plan_source_change(
        self, request: str, registry_summary: list[dict[str, Any]], repo_files: list[str], answers: list[str]
    ) -> SourcePlan:
        user = "\n\n".join(
            [
                "Existing component registry:\n" + json.dumps(registry_summary, ensure_ascii=False),
                "Repository files:\n" + "\n".join(repo_files[:3000]),
                fence("client_request", request),
                *([fence("client_answers", "\n".join(answers))] if answers else []),
            ]
        )
        data = self._call(prompts.SOURCE_PLANNER_SYSTEM, user, SOURCE_PLAN_SCHEMA)
        try:
            return SourcePlan.model_validate(data)
        except ValidationError as exc:
            raise PlannerError("invalid_llm_output", f"Source plan failed schema validation: {exc}") from exc

    def generate_patch(
        self, request: str, plan: SourcePlan, files: dict[str, str], registry_summary: list[dict[str, Any]]
    ) -> CodeChangeProposal:
        file_blocks = [fence(f"repository_file:{path}", content) for path, content in files.items()]
        user = "\n\n".join(
            [
                "Approved plan:\n" + plan.model_dump_json(),
                "Existing component registry:\n" + json.dumps(registry_summary, ensure_ascii=False),
                *file_blocks,
                fence("client_request", request),
            ]
        )
        return proposal_from_llm(self._call(prompts.PATCH_GENERATOR_SYSTEM, user, CODE_PROPOSAL_SCHEMA))


class AnthropicPlanner(_SchemaPlanner):
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        import anthropic

        self._anthropic = anthropic
        self._settings = settings
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            # Credentials resolve from the environment (ANTHROPIC_API_KEY or an `ant auth login` profile).
            self._client = self._anthropic.Anthropic(max_retries=3)
        return self._client

    def _call(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        a = self._anthropic
        try:
            with self.client.beta.messages.stream(
                model=self._settings.llm_model,
                max_tokens=self._settings.llm_max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                thinking={"type": "adaptive"},
                output_config={"effort": self._settings.llm_effort, "format": {"type": "json_schema", "schema": schema}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                message = stream.get_final_message()
        except a.RateLimitError as exc:
            raise PlannerError("llm_rate_limited", "The LLM provider rate-limited the request.", retryable=True) from exc
        except a.APIConnectionError as exc:
            raise PlannerError("llm_unreachable", "Could not reach the LLM provider.", retryable=True) from exc
        except a.APIStatusError as exc:
            retryable = exc.status_code >= 500
            raise PlannerError("llm_error", f"LLM provider error {exc.status_code}: {exc.message}", retryable) from exc
        except a.AnthropicError as exc:  # e.g. no credentials configured
            raise PlannerError("llm_error", f"LLM client error: {exc.__class__.__name__}", retryable=True) from exc

        log.info(
            "llm_call",
            extra={
                "request_id": getattr(message, "_request_id", None),
                "stop_reason": message.stop_reason,
                "usage": message.usage.to_dict(),
            },
        )
        if message.stop_reason == "refusal":
            raise PlannerError("llm_refusal", "The model declined this request.")
        if message.stop_reason == "max_tokens":
            raise PlannerError("llm_truncated", "The model response was truncated (raise LLM_MAX_TOKENS).", retryable=True)
        text = next((b.text for b in message.content if b.type == "text"), None)
        if text is None:
            raise PlannerError("invalid_llm_output", "The model returned no text block.")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PlannerError("invalid_llm_output", f"Model output is not JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise PlannerError("invalid_llm_output", "Model output is not a JSON object.")
        return data


class OllamaPlanner(_SchemaPlanner):
    """A local model served by Ollama (``/api/chat`` with a JSON-schema ``format``, so output is constrained)."""

    def suggest_config_change(
        self, request: str, site_config: dict[str, Any], registry_summary: list[dict[str, Any]], answers: list[str]
    ) -> ConfigSuggestion:
        # Small models struggle to find values inside nested Puck JSON: also give them a flat list of
        # "page | component | prop path = current value" lines and an explicit decision procedure.
        return self._suggest(
            request,
            site_config,
            registry_summary,
            answers,
            system=prompts.CONFIG_PLANNER_SYSTEM + prompts.SMALL_MODEL_CONFIG_GUIDE,
            extra=[fence("editable_values", "\n".join(editable_values(site_config)))],
        )

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.Client(base_url=settings.ollama_base_url, timeout=settings.ollama_timeout_seconds)

    def _call(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        model = self._settings.llm_model
        try:
            resp = self._client.post(
                "/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "format": schema,
                    "stream": False,
                    "options": {"temperature": 0, "num_ctx": self._settings.ollama_num_ctx},
                },
            )
        except httpx.TimeoutException as exc:
            raise PlannerError(
                "llm_timeout", "The local model did not answer in time (raise OLLAMA_TIMEOUT_SECONDS).", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise PlannerError(
                "llm_unreachable",
                f"Could not reach Ollama at {self._settings.ollama_base_url} (is `ollama serve` running?).",
                True,
            ) from exc
        if resp.status_code == 404:
            raise PlannerError("llm_model_missing", f"Ollama has no model {model!r}; run `ollama pull {model}`.", True)
        if resp.status_code >= 400:
            raise PlannerError("llm_error", f"Ollama error {resp.status_code}: {resp.text[:300]}", resp.status_code >= 500)
        body = resp.json()
        log.info(
            "llm_call",
            extra={
                "provider": "ollama",
                "model": model,
                "done_reason": body.get("done_reason"),
                "usage": {"input_tokens": body.get("prompt_eval_count"), "output_tokens": body.get("eval_count")},
            },
        )
        if body.get("done_reason") == "length":
            raise PlannerError("llm_truncated", "The local model's answer was cut off (raise OLLAMA_NUM_CTX).", True)
        text = (body.get("message") or {}).get("content") or ""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PlannerError("invalid_llm_output", f"Model output is not JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise PlannerError("invalid_llm_output", "Model output is not a JSON object.")
        return data


def make_planner(settings: Settings) -> Planner:
    if settings.llm_provider.value == "anthropic":
        return AnthropicPlanner(settings)
    if settings.llm_provider.value == "ollama":
        return OllamaPlanner(settings)
    return UnavailablePlanner()
