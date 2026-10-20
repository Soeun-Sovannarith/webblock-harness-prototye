"""End-to-end scenarios A–I from the harness specification, driven through the HTTP API."""

from __future__ import annotations

import copy
from typing import Any

from conftest import (
    ORG,
    REPO,
    Harness,
    booking_plan,
    booking_proposal,
    customizer_plan,
    customizer_proposal,
    pottery_site,
)
from webblock_harness.domain import FileAction, FileChange
from webblock_harness.integrations.git import CheckRun
from webblock_harness.puck.diff import diff_sites
from webblock_harness.puck.normalize import normalize_site
from webblock_harness.registry.registry import load_base_registry


def _content(config: dict[str, Any], page: str = "/") -> list[dict[str, Any]]:
    return config["pages"][page]["content"]  # type: ignore[no-any-return]


def _by_id(config: dict[str, Any], cid: str, page: str = "/") -> dict[str, Any]:
    return next(c for c in _content(config, page) if c["props"]["id"] == cid)


# --------------------------------------------------------------------------
# Scenario A — content-only update
# --------------------------------------------------------------------------


def test_scenario_a_content_only_price_update(harness: Harness) -> None:
    before = harness.public_config()
    writes_before = harness.git.write_count

    job = harness.submit({"request": "Change a pottery product price from $12 to $15."})

    assert job["state"] == "succeeded", job["error"]
    assert job["path"] == "runtime_config"
    assert job["outcome"] == "deployed"
    plan = job["plan"]
    assert [c["kind"] for c in plan["changes"]] == ["content_update"]
    assert plan["changes"][0]["component_id"] == "gallery-1"
    assert plan["changes"][0]["prop_path"] == ["products", 0, "price"]
    assert plan["decided_by"] == "rules"

    # Only the relevant JSON changed; unrelated content is untouched.
    after = harness.public_config()
    diff = diff_sites(normalize_site(before["config"]), normalize_site(after["config"]), load_base_registry())
    assert len(diff.changes) == 1 and diff.changes[0].after == 15
    assert (
        _by_id(after["config"], "gallery-1")["props"]["products"][1]
        == _by_id(before["config"], "gallery-1")["props"]["products"][1]
    )
    assert after["config"]["pages"]["/shop"] == before["config"]["pages"]["/shop"]

    # No source-code generation, commit or build.
    assert harness.planner.calls == []
    assert harness.git.write_count == writes_before
    assert harness.deploy.started == []

    # The new published version is recorded correctly.
    project = harness.project()
    assert project["pointers"]["published_revision_id"] == job["proposed_revision_id"]
    snaps = harness.snapshots()
    assert len(snaps) == 1 and snaps[0]["is_current"]
    assert snaps[0]["config_hash"] == after["config_hash"]
    assert snaps[0]["job_id"] == job["id"]
    deps = harness.deployments()
    assert [(d["provider"], d["status"]) for d in deps] == [("runtime_config", "verified")]


def test_scenario_a_named_product(harness: Harness) -> None:
    job = harness.submit({"request": "Set the price of the Mekong Serving Bowl to $42"})
    assert job["state"] == "succeeded"
    assert _by_id(harness.public_config()["config"], "gallery-1")["props"]["products"][1]["price"] == 42


# --------------------------------------------------------------------------
# Scenario B — reorder existing components
# --------------------------------------------------------------------------


def test_scenario_b_move_gallery_above_description(harness: Harness) -> None:
    before = _content(harness.public_config()["config"])
    job = harness.submit({"request": "Move the product gallery above the product description"})

    assert job["state"] == "succeeded", job["error"]
    assert job["path"] == "runtime_config"
    reordered = [c for c in job["plan"]["changes"] if c["kind"] == "component_reordered"]
    assert [c["component_id"] for c in reordered] == ["gallery-1"]  # reported as the component the client moved
    assert job["plan"]["operations"][0]["op"] == "move_component"

    after = _content(harness.public_config()["config"])
    ids = [c["props"]["id"] for c in after]
    assert ids.index("gallery-1") == ids.index("desc-1") - 1
    # Nothing but the order changed.
    assert sorted(map(str, before)) == sorted(map(str, after))
    assert harness.planner.calls == []


# --------------------------------------------------------------------------
# Scenario C — unsupported component → source path
# --------------------------------------------------------------------------


def test_scenario_c_new_interactive_component(harness: Harness) -> None:
    harness.planner.source_plans.append(customizer_plan())
    harness.planner.proposals.append(customizer_proposal())
    files_before = harness.main_files()

    job = harness.submit({"request": "Add a custom interactive pottery customization tool"})

    assert job["state"] == "succeeded", job["error"]
    assert job["path"] == "source_code"
    kinds = {c["kind"] for c in job["plan"]["changes"]}
    assert {"new_component", "new_interactive_behavior"} <= kinds
    assert job["plan"]["scopes"] == ["frontend"]
    assert "ci:typecheck" in job["plan"]["required_validations"]
    assert harness.planner.calls == ["plan_source_change", "generate_patch"]
    # Secrets / protected files are never sent to the model as context.
    assert not any(p.startswith(".env") for p in harness.planner.last_context_files)

    # Isolated branch → CI → squash merge → deploy → verify
    assert job["branch"] == f"webblock/job-{job['id']}"
    assert job["merged_commit_sha"] == harness.main_head()
    files_after = harness.main_files()
    assert "src/components/generated/PotteryCustomizer.tsx" in files_after
    for path, content in files_before.items():  # existing application code remains intact
        assert files_after[path] == content

    # The generated component is now a trusted, active registry entry and is placed on the page.
    registry = harness.client.get(f"/v1/projects/{harness.project_id}/registry", headers=harness.headers()).json()
    assert "PotteryCustomizer" in {c["name"] for c in registry["components"]}
    ids = [c["props"]["id"] for c in _content(harness.public_config()["config"])]
    assert ids[ids.index("gallery-1") + 1] == "customizer-1"
    snap = harness.snapshots()[0]
    assert snap["commit_sha"] == job["merged_commit_sha"]
    diff_text = harness.client.get(f"/v1/jobs/{job['id']}/diff", headers=harness.headers()).text
    assert "+export function PotteryCustomizer" in diff_text


def test_scenario_c_editor_uses_unregistered_component(harness: Harness) -> None:
    proposed = pottery_site()
    _content(proposed).append({"type": "PotteryCustomizer", "props": {"id": "cust-x"}})
    harness.planner.source_plans.append(customizer_plan())
    plan = harness.client.post(
        f"/v1/projects/{harness.project_id}/analyze", json={"proposed_config": proposed}, headers=harness.headers()
    ).json()
    assert plan["path"] == "source_code"
    assert "unsupported_component" in {c["kind"] for c in plan["changes"]}
    assert plan["operations"][0]["op"] == "add_component"  # placed on the page once the component exists
    assert harness.git.write_count == 0  # analysis is a dry run


# --------------------------------------------------------------------------
# Scenario D — new backend feature with database persistence
# --------------------------------------------------------------------------


def test_scenario_d_booking_requires_approval_and_integration_tests(harness: Harness) -> None:
    harness.planner.source_plans.append(booking_plan())
    harness.planner.proposals.append(booking_proposal())

    job = harness.submit({"request": "Add a booking feature that requires new API endpoints and database persistence"})

    assert job["state"] == "awaiting_approval"
    plan = job["plan"]
    assert plan["path"] == "source_code"
    assert plan["scopes"] == ["frontend", "backend", "database"]
    assert {"api_contract_change", "database_schema_change"} <= {c["kind"] for c in plan["changes"]}
    assert "database_schema" in plan["approval_reasons"]
    assert {"ci:integration-tests", "ci:migration-test"} <= set(plan["required_validations"])
    assert plan["source_plan"]["migrations"][0]["path"] == "drizzle/0001_bookings.sql"
    assert harness.git.write_count == 0  # nothing applied before approval
    assert harness.project()["pointers"]["active_job_id"] is None  # lock released while waiting

    # Editors cannot approve; owners can, bound to the reviewed plan hash.
    denied = harness.client.post(
        f"/v1/jobs/{job['id']}/approve", json={"plan_hash": job["plan_hash"]}, headers=harness.headers(roles=("editor",))
    )
    assert denied.status_code == 403
    stale = harness.client.post(
        f"/v1/jobs/{job['id']}/approve", json={"plan_hash": "0" * 64}, headers=harness.headers(roles=("owner",))
    )
    assert stale.status_code == 409
    approved = harness.client.post(
        f"/v1/jobs/{job['id']}/approve", json={"plan_hash": job["plan_hash"]}, headers=harness.headers(roles=("owner",))
    ).json()

    assert approved["state"] == "succeeded", approved["error"]
    ci = {c["name"]: c["status"] for c in approved["validation"]["checks"] if c["name"].startswith("ci:")}
    assert ci["ci:integration-tests"] == "passed" and ci["ci:migration-test"] == "passed"
    assert "drizzle/0001_bookings.sql" in harness.main_files()


def test_scenario_d_missing_integration_tests_blocks_deploy(harness: Harness) -> None:
    harness.git.default_check_conclusions = {n: "success" for n in ["format", "typecheck", "lint", "test", "build"]}
    harness.planner.source_plans.append(booking_plan())
    harness.planner.proposals.append(booking_proposal())
    job = harness.submit({"request": "Add a booking feature that requires new API endpoints and database persistence"})
    head_before = harness.main_head()
    job = harness.client.post(
        f"/v1/jobs/{job['id']}/approve", json={"plan_hash": job["plan_hash"]}, headers=harness.headers(roles=("owner",))
    ).json()
    assert job["state"] == "failed"
    assert job["error"]["code"] == "ci_timeout"
    assert set(job["error"]["details"]["pending"]) == {"integration-tests", "migration-test"}
    assert harness.main_head() == head_before
    assert harness.deploy.started == []


def test_scenario_d_destructive_migration_is_rejected_without_approval(harness: Harness) -> None:
    harness.planner.source_plans.append(booking_plan())
    harness.planner.proposals.append(booking_proposal(migration_sql="DROP TABLE orders;\nCREATE TABLE bookings (id serial);\n"))
    job = harness.submit({"request": "Add a booking feature that requires new API endpoints and database persistence"})
    job = harness.client.post(
        f"/v1/jobs/{job['id']}/approve", json={"plan_hash": job["plan_hash"]}, headers=harness.headers(roles=("owner",))
    ).json()
    assert job["state"] == "failed"
    assert job["error"]["code"] == "patch_validation_failed"
    assert "destructive_migration" in {i["code"] for i in job["error"]["details"]["issues"]}
    assert harness.git.write_count == 0


# --------------------------------------------------------------------------
# Scenario E — invalid generated code
# --------------------------------------------------------------------------


def test_scenario_e_invalid_generated_code_never_reaches_production(harness: Harness) -> None:
    # Establish a successful deployment first.
    first = harness.submit({"request": "Change a pottery product price from $12 to $15."})
    assert first["state"] == "succeeded"
    live_before = harness.public_config()
    snap_before = harness.project()["pointers"]["last_successful_snapshot_id"]
    writes_before = harness.git.write_count

    bad = customizer_proposal(
        content='"use client";\nexport function PotteryCustomizer() {\n  const t = process.env.GITHUB_TOKEN;\n  return <div>{eval("1+1")}</div>;\n}\n',
        extra_files=[
            FileChange(path=".env", action=FileAction.MODIFY, content="LEAK=1\n"),
            FileChange(path="../../etc/passwd", action=FileAction.CREATE, content="x"),
        ],
    )
    harness.planner.source_plans.append(customizer_plan())
    harness.planner.proposals.append(bad)
    job = harness.submit({"request": "Add a custom interactive pottery customization tool"})

    assert job["state"] == "failed"
    err = job["error"]
    assert err["code"] == "patch_validation_failed" and err["retryable"] is False
    codes = {i["code"] for i in err["details"]["issues"]}
    assert {"eval", "env_not_allowed", "protected_file", "invalid_path"} <= codes
    assert all(i.get("location") for i in err["details"]["issues"] if i["code"] in ("eval", "protected_file"))
    # No invalid change reached production; the previous deployment remains.
    assert harness.git.write_count == writes_before
    assert harness.public_config() == live_before
    assert harness.project()["pointers"]["last_successful_snapshot_id"] == snap_before
    assert any(e["code"] == "patch_validation_failed" for e in harness.events(job["id"]))


def test_scenario_e_ci_failure_blocks_merge(harness: Harness) -> None:
    harness.git.default_check_conclusions = {
        "format": "success",
        "typecheck": "failure",
        "lint": "success",
        "test": "success",
        "build": "failure",
    }
    harness.planner.source_plans.append(customizer_plan())
    harness.planner.proposals.append(customizer_proposal())
    head_before = harness.main_head()
    job = harness.submit({"request": "Add a custom interactive pottery customization tool"})
    assert job["state"] == "failed"
    assert job["error"]["code"] == "ci_failed"
    assert set(job["error"]["details"]["checks"]) == {"typecheck", "build"}
    assert harness.main_head() == head_before  # never merged
    assert job["commit_sha"] is not None  # the failed commit SHA is recorded
    assert harness.deploy.started == []


# --------------------------------------------------------------------------
# Scenario F — concurrent changes
# --------------------------------------------------------------------------


def test_scenario_f_project_lock_serialises_jobs(harness: Harness) -> None:
    from webblock_harness.models import Job, Project

    with harness.container.db.session() as s:
        project = s.get(Project, harness.project_id)
        assert project is not None
        project.lock_job_id = "job_someone_else"
        from datetime import UTC, datetime, timedelta

        project.lock_expires_at = datetime.now(UTC) + timedelta(hours=1)
    job = harness.submit({"request": "Change a pottery product price from $12 to $15."})
    assert job["state"] == "queued"
    assert any(e["code"] == "waiting_for_lock" for e in harness.events(job["id"]))
    with harness.container.db.session() as s:
        project = s.get(Project, harness.project_id)
        assert project is not None
        project.lock_job_id = None
    from webblock_harness.services.pipeline import Pipeline

    assert Pipeline(harness.container).run(job["id"]) == "succeeded"
    with harness.container.db.session() as s:
        assert s.get(Job, job["id"]).state == "succeeded"  # type: ignore[union-attr]


def test_scenario_f_stale_operations_are_rejected(harness: Harness) -> None:
    assert harness.submit({"request": "Change a pottery product price from $12 to $15."})["state"] == "succeeded"
    # A second client still holding the old page sends a patch whose precondition is stale.
    stale_ops = [
        {
            "op": "set_prop",
            "page": "/",
            "component_id": "gallery-1",
            "path": ["products", 0, "price"],
            "value": 13,
            "expect": 12,
            "has_expect": True,
        }
    ]
    job = harness.submit({"operations": stale_ops})
    assert job["state"] == "failed"
    assert job["error"]["code"] == "plan_rejected"
    assert "Expected 12" in job["error"]["message"]
    assert _by_id(harness.public_config()["config"], "gallery-1")["props"]["products"][0]["price"] == 15


def test_scenario_f_concurrent_editor_save_conflict(harness: Harness) -> None:
    draft = harness.client.get(f"/v1/projects/{harness.project_id}/draft", headers=harness.headers()).json()
    cfg = draft["config"]
    ok = harness.client.put(
        f"/v1/projects/{harness.project_id}/draft",
        json={"config": cfg, "expected_draft_revision_id": draft["id"]},
        headers=harness.headers(),
    )
    assert ok.status_code == 200
    stale = harness.client.put(
        f"/v1/projects/{harness.project_id}/draft",
        json={"config": cfg, "expected_draft_revision_id": draft["id"]},
        headers=harness.headers(),
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_draft"


class _PushDuringCI:
    """Simulates a developer pushing to main while the job's CI is running."""

    def __init__(self, harness: Harness, writes: dict[str, str | None]) -> None:
        self.h, self.writes, self.done = harness, writes, False
        self._orig = harness.git.check_runs
        harness.git.check_runs = self  # type: ignore[method-assign]

    def __call__(self, repo: Any, sha: str) -> list[CheckRun]:
        if not self.done:
            self.done = True
            self.h.git.push_user_commit(ORG, REPO, "main", self.writes, "manual edit by developer")
        return self._orig(repo, sha)


def test_scenario_f_conflicting_manual_edit_is_not_overwritten(harness: Harness) -> None:
    harness.planner.source_plans.append(customizer_plan())
    harness.planner.proposals.append(customizer_proposal())
    _PushDuringCI(harness, {"src/components/generated/PotteryCustomizer.tsx": "// hand-written by the client\n"})
    job = harness.submit({"request": "Add a custom interactive pottery customization tool"})
    assert job["state"] == "failed"
    assert job["error"]["code"] == "source_conflict"
    assert job["error"]["details"]["files"] == ["src/components/generated/PotteryCustomizer.tsx"]
    assert harness.main_files()["src/components/generated/PotteryCustomizer.tsx"] == "// hand-written by the client\n"
    assert harness.deploy.started == []


def test_scenario_f_disjoint_manual_edit_is_reconciled(harness: Harness) -> None:
    harness.planner.source_plans.append(customizer_plan())
    harness.planner.proposals.append(customizer_proposal())
    _PushDuringCI(harness, {"src/app/about/page.tsx": "export default function About() { return null; }\n"})
    job = harness.submit({"request": "Add a custom interactive pottery customization tool"})
    assert job["state"] == "succeeded", job["error"]
    codes = [e["code"] for e in harness.events(job["id"])]
    assert "rebased" in codes and codes.count("ci_passed") == 2  # CI re-ran on the rebased commit
    files = harness.main_files()
    assert "src/app/about/page.tsx" in files and "src/components/generated/PotteryCustomizer.tsx" in files


# --------------------------------------------------------------------------
# Scenario G — tenant isolation
# --------------------------------------------------------------------------


def test_scenario_g_tenant_isolation(harness: Harness) -> None:
    job = harness.submit({"request": "Change a pottery product price from $12 to $15."})
    other = harness.headers(tenant="tenant-b", roles=("admin",))
    pid = harness.project_id
    for url in [
        f"/v1/projects/{pid}",
        f"/v1/projects/{pid}/context",
        f"/v1/projects/{pid}/draft",
        f"/v1/projects/{pid}/revisions",
        f"/v1/projects/{pid}/revisions/{job['proposed_revision_id']}",
        f"/v1/projects/{pid}/jobs",
        f"/v1/projects/{pid}/snapshots",
        f"/v1/projects/{pid}/deployments",
        f"/v1/projects/{pid}/registry",
        f"/v1/jobs/{job['id']}",
        f"/v1/jobs/{job['id']}/events",
    ]:
        resp = harness.client.get(url, headers=other)
        assert resp.status_code == 404, (url, resp.status_code)
    assert harness.client.get("/v1/projects", headers=other).json() == []
    assert (
        harness.client.post(
            f"/v1/projects/{pid}/jobs", json={"request": "Change a pottery product price from $15 to $1."}, headers=other
        ).status_code
        == 404
    )
    assert harness.client.post(f"/v1/jobs/{job['id']}/retry", headers=other).status_code == 404
    snap_id = harness.snapshots()[0]["id"]
    assert harness.client.post(f"/v1/projects/{pid}/rollback", json={"snapshot_id": snap_id}, headers=other).status_code == 404
    # A client-supplied tenant header is ignored: identity comes only from the signed token.
    spoof = {**other, "X-Tenant-Id": "tenant-a"}
    assert harness.client.get(f"/v1/projects/{pid}", headers=spoof).status_code == 404


def test_scenario_g_repository_mapping_rules(harness: Harness) -> None:
    other = harness.headers(tenant="tenant-b", roles=("admin",))
    taken = harness.client.post("/v1/projects", json={"name": "x", "repo_name": REPO}, headers=other)
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "repository_already_mapped"
    outside = harness.client.post("/v1/projects", json={"name": "x", "repo_name": "../someone-else/evil-repo"}, headers=other)
    assert outside.status_code in (422,)
    missing = harness.client.post("/v1/projects", json={"name": "x", "repo_name": "evil-repo"}, headers=other)
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "repository_not_found"
    editor = harness.client.post(
        "/v1/projects",
        json={"name": "x", "repo_name": "other-client-site"},
        headers=harness.headers(tenant="tenant-b", roles=("editor",)),
    )
    assert editor.status_code == 403
    ok = harness.client.post("/v1/projects", json={"name": "x", "repo_name": "other-client-site"}, headers=other)
    assert ok.status_code == 201


def test_scenario_g_authentication_required(harness: Harness) -> None:
    pid = harness.project_id
    assert harness.client.get(f"/v1/projects/{pid}").status_code == 401
    forged = {"Authorization": f"Bearer {harness.token(secret='not-the-real-secret-0123456789abcdef')}"}
    assert harness.client.get(f"/v1/projects/{pid}", headers=forged).status_code == 401
    expired = {"Authorization": f"Bearer {harness.token(exp=1)}"}
    assert harness.client.get(f"/v1/projects/{pid}", headers=expired).status_code == 401
    viewer = harness.headers(roles=("viewer",))
    assert harness.client.post(f"/v1/projects/{pid}/jobs", json={"request": "x"}, headers=viewer).status_code == 403


def test_scenario_g_artifacts_are_tenant_scoped(harness: Harness) -> None:
    import pytest

    store = harness.container.store
    key, _ = store.put_json("tenant-a", harness.project_id, "configs", {"a": 1})
    with pytest.raises(PermissionError):
        store.get_json("tenant-b", key)
    with pytest.raises(PermissionError):
        store.get_json("tenant-a", "tenants/tenant-a/../tenant-b/x.json")


# --------------------------------------------------------------------------
# Scenario H — deployment failure
# --------------------------------------------------------------------------


def test_scenario_h_deployment_failure_and_safe_retry(harness: Harness) -> None:
    first = harness.submit({"request": "Change a pottery product price from $12 to $15."})
    snap_before = harness.project()["pointers"]["last_successful_snapshot_id"]
    live_before = harness.public_config()

    harness.deploy.outcomes = ["fail"]
    harness.planner.source_plans.append(customizer_plan())
    harness.planner.proposals.append(customizer_proposal())
    job = harness.submit({"request": "Add a custom interactive pottery customization tool"})

    assert job["state"] == "failed"
    assert job["error"]["code"] == "build_failed" and job["error"]["retryable"] is True
    assert harness.project()["pointers"]["last_successful_snapshot_id"] == snap_before
    assert harness.public_config() == live_before  # config referencing the new component was not published
    failed = [d for d in harness.deployments() if d["job_id"] == job["id"]]
    assert [d["status"] for d in failed] == ["failed"]
    writes_after_failure = harness.git.write_count
    merged_sha = job["merged_commit_sha"]

    retried = harness.client.post(f"/v1/jobs/{job['id']}/retry", headers=harness.headers()).json()
    assert retried["state"] == "succeeded", retried["error"]
    assert retried["merged_commit_sha"] == merged_sha  # same commit; nothing re-generated or re-merged
    assert harness.git.write_count == writes_after_failure
    assert harness.planner.calls.count("generate_patch") == 1
    attempts = [(d["attempt"], d["status"]) for d in harness.deployments() if d["job_id"] == job["id"]]
    assert sorted(attempts) == [(1, "failed"), (2, "verified")]
    assert harness.project()["pointers"]["last_successful_snapshot_id"] not in (None, snap_before)
    assert first["snapshot_id"] == snap_before


def test_scenario_h_unverified_deployment_does_not_advance_snapshot(harness: Harness) -> None:
    harness.deploy.outcomes = ["verify_fail"]
    harness.planner.source_plans.append(customizer_plan())
    harness.planner.proposals.append(customizer_proposal())
    job = harness.submit({"request": "Add a custom interactive pottery customization tool"})
    assert job["state"] == "failed" and job["error"]["code"] == "verification_failed"
    assert harness.project()["pointers"]["last_successful_snapshot_id"] is None
    # The configuration that referenced the unverified component was rolled back to the previous revision.
    assert "customizer-1" not in [c["props"]["id"] for c in _content(harness.public_config()["config"])]


def test_scenario_h_idempotent_submission(harness: Harness) -> None:
    a = harness.submit({"request": "Change a pottery product price from $12 to $15."}, key="req-123")
    b = harness.submit({"request": "Change a pottery product price from $12 to $15."}, key="req-123")
    assert a["id"] == b["id"]
    assert len(harness.client.get(f"/v1/projects/{harness.project_id}/jobs", headers=harness.headers()).json()) == 1
    assert len(harness.deployments()) == 1


def test_scenario_h_non_retryable_failure_cannot_be_retried(harness: Harness) -> None:
    job = harness.submit({"operations": [{"op": "remove_component", "page": "/", "component_id": "does-not-exist"}]})
    assert job["state"] == "failed"
    resp = harness.client.post(f"/v1/jobs/{job['id']}/retry", headers=harness.headers())
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "not_retryable"


# --------------------------------------------------------------------------
# Scenario I — no-op
# --------------------------------------------------------------------------


def test_scenario_i_noop_editor_submission(harness: Harness) -> None:
    live = harness.public_config()["config"]
    reordered_keys = copy.deepcopy(live)
    # Same content, different key order and float formatting: still a no-op.
    reordered_keys["pages"]["/"]["content"][3]["props"]["products"][0]["price"] = 12.0
    reordered_keys["pages"]["/"]["content"][0]["props"] = dict(
        reversed(list(reordered_keys["pages"]["/"]["content"][0]["props"].items()))
    )
    revisions_before = len(harness.client.get(f"/v1/projects/{harness.project_id}/revisions", headers=harness.headers()).json())

    job = harness.submit({"proposed_config": reordered_keys})

    assert job["state"] == "succeeded" and job["outcome"] == "no_changes" and job["path"] == "noop"
    assert harness.deployments() == [] and harness.snapshots() == []
    assert harness.git.write_count == 0 and harness.planner.calls == []
    revisions_after = len(harness.client.get(f"/v1/projects/{harness.project_id}/revisions", headers=harness.headers()).json())
    assert revisions_after == revisions_before


def test_scenario_i_noop_request(harness: Harness) -> None:
    job = harness.submit({"request": "Change the Lotus Tea Cup price from $12 to $12"})
    assert job["state"] == "succeeded" and job["outcome"] == "no_changes"
    assert harness.deployments() == []
