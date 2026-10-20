"""Local model via Ollama: request shape, output validation and failure modes (HTTP mocked)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from conftest import make_settings
from webblock_harness.analyzer.llm import (
    CONFIG_SUGGESTION_SCHEMA,
    OllamaPlanner,
    PlannerError,
    editable_values,
    make_planner,
)

SITE = {
    "pages": {
        "/": {
            "root": {"props": {"title": "Sok Cafe"}},
            "content": [
                {"type": "Hero", "props": {"id": "hero-1", "heading": "Welcome"}},
                {
                    "type": "Columns",
                    "props": {"id": "c-1", "left": [{"type": "Text", "props": {"id": "t-1", "text": "Open 7-18"}}]},
                },
            ],
            "zones": {},
        }
    }
}
OP = {
    "op": "set_prop",
    "page": "/",
    "component_id": "hero-1",
    "path": ["heading"],
    "value_json": '"Hello"',
    "expect_json": '"Welcome"',
    "before_id": None,
    "after_id": None,
    "container": None,
    "index": None,
    "component_json": None,
}


def _planner(tmp_path: Path, handler: Any) -> OllamaPlanner:
    settings = make_settings(tmp_path, LLM_PROVIDER="ollama", LLM_MODEL="qwen2.5-coder:7b")
    return OllamaPlanner(settings, client=httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler)))


def _reply(content: dict[str, Any] | str, done_reason: str = "stop") -> httpx.Response:
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={"message": {"role": "assistant", "content": text}, "done_reason": done_reason})


def test_make_planner_selects_ollama(tmp_path: Path) -> None:
    assert isinstance(make_planner(make_settings(tmp_path, LLM_PROVIDER="ollama")), OllamaPlanner)


def test_request_is_schema_constrained_and_answer_validated(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["url"] = str(request.url)
        return _reply(
            {
                "path": "runtime_config",
                "operations": [OP],
                "questions": [],
                "capability_needed": None,
                "confidence": 0.9,
                "reasoning": "r",
            }
        )

    sugg = _planner(tmp_path, handler).suggest_config_change("Change the headline to Hello", SITE, [], [])
    assert seen["url"] == "http://ollama.test/api/chat"
    assert seen["model"] == "qwen2.5-coder:7b" and seen["stream"] is False
    assert seen["format"] == CONFIG_SUGGESTION_SCHEMA  # output constrained to the schema
    assert seen["options"]["temperature"] == 0 and seen["options"]["num_ctx"] == 32768
    user = seen["messages"][1]["content"]
    assert '/ | hero-1 (Hero) | ["heading"] = "Welcome"' in user  # flat view for small models
    assert sugg.path == "runtime_config" and sugg.operations[0].value == "Hello"  # type: ignore[union-attr]


def test_editable_values_reach_nested_slots() -> None:
    lines = editable_values(SITE)
    assert '/ | t-1 (Text) | ["text"] = "Open 7-18"' in lines
    assert any(line.startswith("/ | root") and '"title"' in line for line in lines)


def test_empty_edit_with_a_question_becomes_a_clarification(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _reply(
            {
                "path": "runtime_config",
                "operations": [],
                "questions": ["Which button?"],
                "capability_needed": None,
                "confidence": 0.8,
                "reasoning": "",
            }
        )

    sugg = _planner(tmp_path, handler).suggest_config_change("Change the button", SITE, [], [])
    assert sugg.path == "clarification" and sugg.questions == ["Which button?"]


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (httpx.Response(404, json={"error": "model not found"}), "llm_model_missing", True),
        (httpx.Response(500, text="boom"), "llm_error", True),
        (_reply("{not json"), "invalid_llm_output", False),
        (_reply('{"path": "runtime_config"', done_reason="length"), "llm_truncated", True),
        (_reply({"path": "teleport"}), "invalid_llm_output", False),
    ],
)
def test_failures_are_typed(tmp_path: Path, response: httpx.Response, code: str, retryable: bool) -> None:
    with pytest.raises(PlannerError) as exc:
        _planner(tmp_path, lambda _: response).suggest_config_change("x", SITE, [], [])
    assert exc.value.code == code and exc.value.retryable is retryable


def test_ollama_not_running_is_retryable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(PlannerError) as exc:
        _planner(tmp_path, handler).suggest_config_change("x", SITE, [], [])
    assert exc.value.code == "llm_unreachable" and exc.value.retryable
