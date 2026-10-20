"""Build/deployment integration.

A deployment is only *successful* for the harness once ``verify`` confirms the
expected version is live; provider-reported success alone never advances the
last-successful snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

from webblock_harness.config import Settings
from webblock_harness.domain import DeploymentStatus
from webblock_harness.integrations.git import GitError, GitProvider, RepoRef


@dataclass(frozen=True)
class DeployRequest:
    deployment_id: str
    job_id: str
    repo: RepoRef
    ref: str  # branch to run the workflow on
    commit_sha: str
    config_hash: str | None
    created_at: datetime


@dataclass
class DeployState:
    status: DeploymentStatus
    external_id: str | None = None
    url: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class DeployProvider(Protocol):
    name: str

    def start(self, req: DeployRequest, existing_external_id: str | None) -> DeployState: ...
    def poll(self, req: DeployRequest, external_id: str | None) -> DeployState: ...
    def verify(self, req: DeployRequest) -> tuple[bool, dict[str, Any]]: ...


class GitHubActionsDeployProvider:
    """Runs the client repository's deploy workflow via ``workflow_dispatch``.

    Expected workflow contract (documented in the README):
    * ``on: workflow_dispatch`` with inputs ``commit_sha`` and ``deployment_id``;
    * ``run-name`` containing ``${{ inputs.deployment_id }}`` so the run can be found idempotently;
    * the deployed site exposes ``DEPLOY_VERIFY_URL_TEMPLATE`` returning ``{"commit_sha": "..."}``.
    """

    name = "github_actions"

    def __init__(self, settings: Settings, client: httpx.Client | None = None, verify_client: httpx.Client | None = None) -> None:
        if settings.github_token is None:
            raise GitError("github_not_configured", "GITHUB_TOKEN / GITHUB_ORGANIZATION_KEY is not set.")
        self._s = settings
        self._http = client or httpx.Client(
            base_url=settings.github_api_url,
            headers={
                "Authorization": f"Bearer {settings.github_token.get_secret_value()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "webblock-harness",
            },
            timeout=30.0,
        )
        self._verify_http = verify_client or httpx.Client(timeout=15.0, follow_redirects=False)

    def _wf(self, repo: RepoRef) -> str:
        return f"/repos/{repo.owner}/{repo.name}/actions/workflows/{self._s.deploy_workflow_file}"

    def _find_run(self, req: DeployRequest) -> dict[str, Any] | None:
        since = (req.created_at - timedelta(minutes=2)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = self._http.get(
            f"{self._wf(req.repo)}/runs",
            params={"event": "workflow_dispatch", "branch": req.ref, "created": f">={since}", "per_page": 50},
        )
        if resp.status_code != 200:
            raise GitError(f"github_{resp.status_code}", "Could not list workflow runs.", retryable=resp.status_code >= 500)
        for run in resp.json().get("workflow_runs", []):
            if req.deployment_id in (run.get("display_title") or "") or req.deployment_id in (run.get("name") or ""):
                return run  # type: ignore[no-any-return]
        return None

    def start(self, req: DeployRequest, existing_external_id: str | None) -> DeployState:
        if existing_external_id:
            return self.poll(req, existing_external_id)
        # Idempotency: a dispatch may have succeeded before a crash; look for the run first.
        run = self._find_run(req)
        if run is None:
            resp = self._http.post(
                f"{self._wf(req.repo)}/dispatches",
                json={
                    "ref": req.ref,
                    "inputs": {"commit_sha": req.commit_sha, "deployment_id": req.deployment_id},
                },
            )
            if resp.status_code not in (200, 204):
                raise GitError(
                    f"github_{resp.status_code}",
                    f"Workflow dispatch failed: {resp.text[:200]}",
                    retryable=resp.status_code >= 500,
                )
            return DeployState(status=DeploymentStatus.PENDING)
        return self._state(run)

    def _state(self, run: dict[str, Any]) -> DeployState:
        status, conclusion = run.get("status"), run.get("conclusion")
        if status != "completed":
            mapped = DeploymentStatus.BUILDING if status == "in_progress" else DeploymentStatus.PENDING
        else:
            mapped = DeploymentStatus.SUCCEEDED if conclusion == "success" else DeploymentStatus.FAILED
        return DeployState(
            status=mapped,
            external_id=str(run["id"]),
            url=run.get("html_url"),
            details={"status": status, "conclusion": conclusion, "head_sha": run.get("head_sha")},
        )

    def poll(self, req: DeployRequest, external_id: str | None) -> DeployState:
        if external_id is None:
            run = self._find_run(req)
            return self._state(run) if run else DeployState(status=DeploymentStatus.PENDING)
        resp = self._http.get(f"/repos/{req.repo.owner}/{req.repo.name}/actions/runs/{external_id}")
        if resp.status_code != 200:
            raise GitError(f"github_{resp.status_code}", "Could not read workflow run.", retryable=resp.status_code >= 500)
        return self._state(resp.json())

    def verify(self, req: DeployRequest) -> tuple[bool, dict[str, Any]]:
        template = self._s.deploy_verify_url_template
        if not template:
            return False, {"reason": "DEPLOY_VERIFY_URL_TEMPLATE is not configured; cannot confirm the live version."}
        url = template.format(owner=req.repo.owner, repo_name=req.repo.name)
        try:
            resp = self._verify_http.get(url, headers={"Cache-Control": "no-cache"})
            data = resp.json() if resp.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as exc:
            return False, {"url": url, "reason": f"verification request failed: {exc.__class__.__name__}"}
        live = str(data.get("commit_sha") or "")
        ok = bool(live) and (live == req.commit_sha or req.commit_sha.startswith(live) or live.startswith(req.commit_sha))
        return ok, {"url": url, "live_commit_sha": live, "expected_commit_sha": req.commit_sha, "http_status": resp.status_code}


class FakeDeployProvider:
    """Scriptable provider for tests and local runs. Never claims to touch real infrastructure."""

    name = "fake"

    def __init__(self) -> None:
        self.outcomes: list[str] = []  # queue of "success" | "fail" | "verify_fail"
        self.started: list[DeployRequest] = []
        self.live_commit: dict[str, str] = {}
        self._runs: dict[str, str] = {}

    def start(self, req: DeployRequest, existing_external_id: str | None) -> DeployState:
        if existing_external_id and existing_external_id in self._runs:
            return self.poll(req, existing_external_id)
        outcome = self.outcomes.pop(0) if self.outcomes else "success"
        external_id = f"fake-run-{len(self.started) + 1}"
        self._runs[external_id] = outcome
        self.started.append(req)
        return DeployState(status=DeploymentStatus.BUILDING, external_id=external_id)

    def poll(self, req: DeployRequest, external_id: str | None) -> DeployState:
        outcome = self._runs.get(external_id or "", "fail")
        if outcome == "fail":
            return DeployState(
                status=DeploymentStatus.FAILED,
                external_id=external_id,
                details={"conclusion": "failure", "log_excerpt": "next build: Type error in generated component"},
            )
        if outcome != "verify_fail":
            self.live_commit[req.repo.full_name] = req.commit_sha
        return DeployState(status=DeploymentStatus.SUCCEEDED, external_id=external_id, details={"conclusion": "success"})

    def verify(self, req: DeployRequest) -> tuple[bool, dict[str, Any]]:
        live = self.live_commit.get(req.repo.full_name)
        return live == req.commit_sha, {"live_commit_sha": live, "expected_commit_sha": req.commit_sha}


class GitHubPushDeployProvider:
    """Deployment = the commit is on the client repository's default branch in the WebBlock organisation.

    Used while there is no hosting/CI pipeline. Verification is real: it re-reads GitHub and checks that the
    branch contains the commit and that the committed site configuration matches the expected revision hash.
    """

    name = "github_push"

    def __init__(self, settings: Settings, git: GitProvider) -> None:
        self._s = settings
        self._git = git

    def _state(self, req: DeployRequest) -> DeployState:
        on_branch = self._git.contains_commit(req.repo, req.ref, req.commit_sha)
        url = f"https://github.com/{req.repo.owner}/{req.repo.name}/commit/{req.commit_sha}"
        if on_branch:
            return DeployState(
                status=DeploymentStatus.SUCCEEDED, external_id=req.commit_sha, url=url, details={"branch": req.ref}
            )
        return DeployState(
            status=DeploymentStatus.FAILED, external_id=req.commit_sha, url=url, details={"reason": f"commit is not on {req.ref}"}
        )

    def start(self, req: DeployRequest, existing_external_id: str | None) -> DeployState:
        return self._state(req)

    def poll(self, req: DeployRequest, external_id: str | None) -> DeployState:
        return self._state(req)

    def verify(self, req: DeployRequest) -> tuple[bool, dict[str, Any]]:
        from webblock_harness.puck.normalize import ConfigFormatError, config_hash, normalize_site

        details: dict[str, Any] = {
            "branch": req.ref,
            "commit_sha": req.commit_sha,
            "url": f"https://github.com/{req.repo.owner}/{req.repo.name}/commit/{req.commit_sha}",
        }
        if not self._git.contains_commit(req.repo, req.ref, req.commit_sha):
            return False, details | {"reason": "commit not found on branch"}
        if req.config_hash:
            blob = self._git.read_file(req.repo, self._s.config_repo_path, req.commit_sha)
            try:
                live = config_hash(normalize_site(blob.content)) if blob else None
            except ConfigFormatError:
                live = None
            details |= {
                "config_path": self._s.config_repo_path,
                "committed_config_hash": live,
                "expected_config_hash": req.config_hash,
            }
            if live != req.config_hash:
                return False, details | {"reason": "committed site configuration does not match the published revision"}
        return True, details


def make_deploy_provider(settings: Settings, git: GitProvider) -> DeployProvider:
    if settings.deploy_provider.value == "fake":
        return FakeDeployProvider()
    if settings.deploy_provider.value == "github_push":
        return GitHubPushDeployProvider(settings, git)
    return GitHubActionsDeployProvider(settings)
