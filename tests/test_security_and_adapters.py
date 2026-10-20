"""Path policy, patch validation details and the GitHub / Anthropic adapters (HTTP mocked)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from conftest import customizer_plan, customizer_proposal, make_settings
from webblock_harness.codegen.validator import validate_patch
from webblock_harness.domain import ApprovalReason, FileAction, FileChange
from webblock_harness.integrations.deploy import DeployRequest, GitHubActionsDeployProvider
from webblock_harness.integrations.git import FileWrite, GitHubProvider, RepoRef, StaleRefError
from webblock_harness.registry.registry import load_base_registry
from webblock_harness.security.paths import (
    PathPolicyError,
    allowed_for_scopes,
    high_impact_reason,
    is_protected,
    normalize_repo_path,
)


@pytest.mark.parametrize("bad", ["../x", "a/../../b", "/etc/passwd", "C:/x", "a//b", "a/./b", "a\\b", "", "a/b/"])
def test_path_traversal_is_rejected(bad: str) -> None:
    with pytest.raises(PathPolicyError):
        normalize_repo_path(bad)


def test_protected_and_high_impact_paths() -> None:
    for p in [
        ".env",
        ".env.production",
        "apps/web/.env.local",
        ".github/workflows/deploy.yml",
        "package-lock.json",
        "certs/server.pem",
        "src/middleware.ts",
        "webblock/site-config.json",
    ]:
        assert is_protected(p), p
    assert not is_protected("src/components/generated/X.tsx")
    assert high_impact_reason("src/lib/auth/session.ts") == "authentication"
    assert high_impact_reason("src/app/checkout/page.tsx") == "payments"
    assert high_impact_reason("drizzle/0002.sql") == "database_schema"
    from webblock_harness.domain import Scope

    assert allowed_for_scopes("src/components/generated/X.tsx", {Scope.FRONTEND})
    assert allowed_for_scopes("src/app/api/x/route.ts", {Scope.BACKEND})
    assert not allowed_for_scopes("src/app/api/x/route.ts", {Scope.FRONTEND})  # frontend patches can't add endpoints
    assert not allowed_for_scopes("drizzle/0001.sql", {Scope.FRONTEND})
    assert allowed_for_scopes("drizzle/0001.sql", {Scope.DATABASE})


def _review(proposal, approved=frozenset(), base=None, user_authored=frozenset()):  # type: ignore[no-untyped-def]
    return validate_patch(
        proposal,
        customizer_plan(),
        base or {},
        load_base_registry(),
        make_settings(Path("/tmp")),
        set(approved),
        set(user_authored),
    )


def test_valid_patch_passes_static_validation() -> None:
    review = _review(customizer_proposal())
    assert review.report.passed, [i.message for i in review.report.issues]
    assert review.stats["files"] == 2 and review.stats["lines_removed"] == 0
    assert "+++ b/src/components/generated/PotteryCustomizer.tsx" in review.diff


@pytest.mark.parametrize(
    "snippet,code",
    [
        ("const x = eval(input);", "eval"),
        ("const f = new Function('return 1');", "new_function"),
        ("import cp from 'child_process';", "dangerous_module"),
        ("<div dangerouslySetInnerHTML={{__html: x}} />", "raw_html"),
        ("fetch('http://169.254.169.254/latest/meta-data');", "internal_host"),
        ("const k = process.env.ANTHROPIC_API_KEY;", "env_not_allowed"),
        ("const k = process.env[name];", "dynamic_env"),
        ("const db = process.env.DATABASE_URL;", "server_env_in_client"),
        ("const token = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789';", "hardcoded_secret"),
    ],
)
def test_static_scan_catches_dangerous_code(snippet: str, code: str) -> None:
    content = f'"use client";\nexport function PotteryCustomizer() {{\n  {snippet}\n  return null;\n}}\n'
    review = _review(customizer_proposal(content=content))
    assert code in {i.code for i in review.report.issues}
    assert not review.report.passed


def test_patch_must_match_plan_and_preserve_existing_code() -> None:
    from webblock_harness.integrations.git import FileBlob, blob_sha

    hero = "export function Hero() { return null; }\n"
    extra = [
        FileChange(
            path="src/components/webblock/Hero.tsx", action=FileAction.MODIFY, content="export function Hero() { return 1; }\n"
        )
    ]
    review = _review(
        customizer_proposal(extra_files=extra),
        base={"src/components/webblock/Hero.tsx": FileBlob(hero, blob_sha(hero))},
        user_authored={"src/components/webblock/Hero.tsx"},
    )
    codes = {i.code for i in review.report.issues}
    assert "unplanned_file" in codes
    assert ApprovalReason.MODIFIES_USER_CODE in review.approval_reasons
    assert "approval_required" in codes
    # Creating a file that already exists would overwrite it.
    review = _review(customizer_proposal(), base={"src/components/generated/PotteryCustomizer.tsx": FileBlob("x", blob_sha("x"))})
    assert "file_exists" in {i.code for i in review.report.issues}


def test_component_spec_and_export_are_checked() -> None:
    proposal = customizer_proposal(content='"use client";\nexport function SomethingElse() { return null; }\n')
    assert "component_export_missing" in {i.code for i in _review(proposal).report.issues}


# --------------------------------------------------------------------------
# GitHub adapter (mocked transport): CAS ref updates, merges, checks
# --------------------------------------------------------------------------


def _github(handler: Any) -> GitHubProvider:
    settings = make_settings(Path("/tmp"), GITHUB_TOKEN="ghp_test_token_value_0123456789abcd")
    client = httpx.Client(base_url="https://api.github.test", transport=httpx.MockTransport(handler))
    return GitHubProvider(settings, client=client)


def test_github_commit_is_compare_and_swap() -> None:
    calls: list[tuple[str, str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, req.url.path, body))
        path = req.url.path
        if path.endswith("/git/commits/base"):
            return httpx.Response(200, json={"sha": "base", "tree": {"sha": "tree0"}})
        if path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "blob1"})
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "tree1"})
        if path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "c1"})
        if req.method == "PATCH" and "/git/refs/heads/main" in path:
            return httpx.Response(422, json={"message": "Update is not a fast forward"})
        return httpx.Response(404, json={"message": "nope"})

    gh = _github(handler)
    with pytest.raises(StaleRefError):
        gh.commit_files(RepoRef("org", "site"), "main", "base", [FileWrite("a.txt", "hi")], "msg", create_branch=False)
    patch = next(c for c in calls if c[0] == "PATCH")
    assert patch[2] == {"sha": "c1", "force": False}
    # The token is sent only as an Authorization header to GitHub; never in a URL or body.
    assert all("ghp_" not in json.dumps(c[2] or {}) and "ghp_" not in c[1] for c in calls)


def test_github_force_only_on_harness_branches() -> None:
    gh = _github(lambda req: httpx.Response(500))
    from webblock_harness.integrations.git import GitError

    with pytest.raises(GitError) as exc:
        gh._update_ref(RepoRef("o", "r"), "main", "sha", create=False, force=True)
    assert exc.value.code == "forbidden_force_push"


def test_github_merge_conflict_and_stale_head() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/pulls/1/merge"):
            return httpx.Response(409, json={"message": "Head branch was modified"})
        return httpx.Response(405, json={"message": "Pull Request is not mergeable"})

    with pytest.raises(StaleRefError):
        _github(handler).merge_pull_request(RepoRef("o", "r"), 1, "abc", "t")


def test_github_checks_merge_check_runs_and_statuses() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/check-runs"):
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": "build",
                            "status": "completed",
                            "conclusion": "success",
                            "html_url": "u",
                            "output": {"summary": "ok"},
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"statuses": [{"context": "integration-tests", "state": "pending"}]})

    runs = {r.name: r for r in _github(handler).check_runs(RepoRef("o", "r"), "sha")}
    assert runs["build"].conclusion == "success"
    assert runs["integration-tests"].status == "in_progress"


def test_actions_deploy_finds_existing_run_instead_of_redispatching() -> None:
    dispatched: list[Any] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/dispatches"):
            dispatched.append(req)
            return httpx.Response(204)
        if req.url.path.endswith("/runs"):
            return httpx.Response(
                200,
                json={
                    "workflow_runs": [
                        {
                            "id": 77,
                            "display_title": "Deploy dep_123",
                            "status": "in_progress",
                            "conclusion": None,
                            "html_url": "https://gh/run/77",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    settings = make_settings(Path("/tmp"), GITHUB_TOKEN="ghp_test_token_value_0123456789abcd")
    provider = GitHubActionsDeployProvider(
        settings, client=httpx.Client(base_url="https://api.github.test", transport=httpx.MockTransport(handler))
    )
    req = DeployRequest("dep_123", "job_1", RepoRef("o", "r"), "main", "abc", None, datetime.now(UTC))
    state = provider.start(req, None)
    assert dispatched == [] and state.external_id == "77" and state.status.value == "building"


def test_actions_verify_requires_configured_endpoint() -> None:
    settings = make_settings(Path("/tmp"), GITHUB_TOKEN="ghp_test_token_value_0123456789abcd")
    provider = GitHubActionsDeployProvider(
        settings, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    )
    ok, details = provider.verify(DeployRequest("d", "j", RepoRef("o", "r"), "main", "abc", None, datetime.now(UTC)))
    assert ok is False and "not configured" in details["reason"]

    verify = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"commit_sha": "abc"})))
    settings = make_settings(
        Path("/tmp"),
        GITHUB_TOKEN="ghp_test_token_value_0123456789abcd",
        DEPLOY_VERIFY_URL_TEMPLATE="https://{repo_name}.sites.test/api/webblock/version",
    )
    provider = GitHubActionsDeployProvider(
        settings, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))), verify_client=verify
    )
    ok, details = provider.verify(DeployRequest("d", "j", RepoRef("o", "r"), "main", "abc", None, datetime.now(UTC)))
    assert ok is True and details["url"] == "https://r.sites.test/api/webblock/version"


# --------------------------------------------------------------------------
# Anthropic planner (fake SDK client): structured output parsing and stop reasons
# --------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, message: Any) -> None:
        self.message = message

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def get_final_message(self) -> Any:
        return self.message


class _FakeClient:
    def __init__(self, message: Any) -> None:
        self.kwargs: dict[str, Any] = {}
        outer = self

        class _Messages:
            def stream(self, **kwargs: Any) -> _FakeStream:
                outer.kwargs = kwargs
                return _FakeStream(message)

        self.beta = SimpleNamespace(messages=_Messages())


def _message(text: str, stop: str = "end_turn") -> Any:
    usage = SimpleNamespace(to_dict=lambda: {"input_tokens": 1, "output_tokens": 1})
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)], usage=usage)


def test_anthropic_planner_uses_structured_outputs_and_validates() -> None:
    from webblock_harness.analyzer.llm import AnthropicPlanner

    payload = {
        "path": "runtime_config",
        "questions": [],
        "capability_needed": None,
        "confidence": 0.9,
        "reasoning": "r",
        "operations": [
            {
                "op": "set_prop",
                "page": "/",
                "component_id": "hero-1",
                "path": ["heading"],
                "value_json": '"Hello"',
                "expect_json": None,
                "before_id": None,
                "after_id": None,
                "container": None,
                "index": None,
                "component_json": None,
            }
        ],
    }
    client = _FakeClient(_message(json.dumps(payload)))
    planner = AnthropicPlanner(make_settings(Path("/tmp")), client=client)
    sugg = planner.suggest_config_change("make hero say hello <script>", {"pages": {}}, [], [])
    assert sugg.operations[0].value == "Hello"  # type: ignore[union-attr]
    kw = client.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert '<untrusted source="client_request">' in kw["messages"][0]["content"]


def test_anthropic_planner_handles_refusal_and_truncation() -> None:
    from webblock_harness.analyzer.llm import AnthropicPlanner, PlannerError

    settings = make_settings(Path("/tmp"))
    with pytest.raises(PlannerError) as exc:
        AnthropicPlanner(settings, client=_FakeClient(_message("", "refusal"))).suggest_config_change("x", {}, [], [])
    assert exc.value.code == "llm_refusal" and not exc.value.retryable
    with pytest.raises(PlannerError) as exc:
        AnthropicPlanner(settings, client=_FakeClient(_message("{", "max_tokens"))).suggest_config_change("x", {}, [], [])
    assert exc.value.code == "llm_truncated"
    with pytest.raises(PlannerError) as exc:
        AnthropicPlanner(settings, client=_FakeClient(_message('{"path": "teleport"}'))).suggest_config_change("x", {}, [], [])
    assert exc.value.code == "invalid_llm_output"


def test_auth_settings_reject_algorithm_confusion_and_weak_secrets() -> None:
    from pydantic import ValidationError

    from webblock_harness.config import Settings

    base = {"DATABASE_URL": "sqlite:///:memory:"}
    with pytest.raises(ValidationError, match="asymmetric"):
        Settings(_env_file=None, AUTH_JWT_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----x", **base)  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="32 characters"):
        Settings(_env_file=None, AUTH_JWT_SECRET="short", **base)  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="subset"):
        Settings(_env_file=None, AUTH_JWT_SECRET="x" * 40, AUTH_JWT_ALGORITHMS=["none"], **base)  # type: ignore[call-arg]


def test_invalid_initial_template_is_rejected(harness) -> None:  # type: ignore[no-untyped-def]
    bad = {"pages": {"/": {"root": {"props": {"title": "x"}}, "content": [{"type": "Marquee", "props": {"id": "m"}}]}}}
    resp = harness.client.post(
        "/v1/projects",
        json={"name": "x", "repo_name": "other-client-site", "initial_config": bad},
        headers=harness.headers(tenant="tenant-b"),
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "invalid_config"


def test_github_create_repository_and_contains_commit() -> None:
    seen: list[tuple[str, str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        if req.method == "POST" and req.url.path == "/orgs/WebBlock-Organization/repos":
            body = json.loads(req.content)
            if body["name"] == "taken-site":
                return httpx.Response(422, json={"message": "name already exists on this account"})
            return httpx.Response(
                201, json={"name": body["name"], "owner": {"login": "WebBlock-Organization"}, "default_branch": "main"}
            )
        if "/compare/" in req.url.path:
            return httpx.Response(200, json={"status": "ahead" if "good" in req.url.path else "diverged"})
        return httpx.Response(404, json={"message": "nope"})

    gh = _github(handler)
    info = gh.create_repository(RepoRef("WebBlock-Organization", "kampot-site"), "Kampot", private=True)
    assert info.name == "kampot-site" and info.default_branch == "main"
    assert seen[0][2] == {
        "name": "kampot-site",
        "description": "Kampot",
        "private": True,
        "auto_init": True,
        "has_wiki": False,
        "has_projects": False,
    }
    from webblock_harness.integrations.git import GitError

    with pytest.raises(GitError) as exc:
        gh.create_repository(RepoRef("WebBlock-Organization", "taken-site"), "x", private=True)
    assert exc.value.code == "repo_exists"
    assert gh.contains_commit(RepoRef("o", "r"), "main", "good") is True
    assert gh.contains_commit(RepoRef("o", "r"), "main", "bad") is False


def test_github_repo_creation_permission_error_is_actionable_and_retryable() -> None:
    gh = _github(
        lambda req: httpx.Response(
            403, json={"message": "You need admin access to the organization before adding a repository to it."}
        )
    )
    from webblock_harness.integrations.git import GitError

    with pytest.raises(GitError) as exc:
        gh.create_repository(RepoRef("WebBlock-Organization", "kampot-site"), "x", private=True)
    assert exc.value.code == "github_permission_denied" and exc.value.retryable
    assert "admin access" in exc.value.message
