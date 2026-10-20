"""Job lifecycle, version tracking, rollback, drafts, clarification and cancellation."""

from __future__ import annotations

import pytest

from conftest import Harness, customizer_plan, customizer_proposal, make_settings, pottery_site
from webblock_harness.domain import ALLOWED_TRANSITIONS, TERMINAL_STATES, JobState
from webblock_harness.models import ConfigRevision, Job, Snapshot
from webblock_harness.services.jobs import InvalidTransition, JobService


def test_state_machine_is_explicit() -> None:
    for state in TERMINAL_STATES - {JobState.FAILED}:
        assert ALLOWED_TRANSITIONS[state] == frozenset()
    assert ALLOWED_TRANSITIONS[JobState.FAILED] == {JobState.QUEUED}
    # Nothing can jump straight from validating to succeeded, or from building back to applying.
    assert JobState.SUCCEEDED not in ALLOWED_TRANSITIONS[JobState.VALIDATING]
    assert JobState.APPLYING not in ALLOWED_TRANSITIONS[JobState.BUILDING]


def test_invalid_transition_raises(harness: Harness) -> None:
    job = harness.submit({"request": "Change a pottery product price from $12 to $15."})
    with harness.container.db.session() as s:
        row = s.get(Job, job["id"])
        assert row is not None
        with pytest.raises(InvalidTransition):
            JobService(harness.container).transition(s, row, JobState.DEPLOYING, "x", "y")


def test_every_transition_is_recorded(harness: Harness) -> None:
    job = harness.submit({"request": "Change a pottery product price from $12 to $15."})
    transitions = [(e["from_state"], e["to_state"]) for e in harness.events(job["id"]) if e["to_state"]]
    assert transitions == [
        (None, "queued"),
        ("queued", "analyzing"),
        ("analyzing", "planned"),
        ("planned", "applying"),
        ("applying", "validating"),
        ("validating", "deploying"),
        ("deploying", "verifying"),
        ("verifying", "succeeded"),
    ]


def test_snapshots_and_revisions_are_immutable(harness: Harness) -> None:
    job = harness.submit({"request": "Change a pottery product price from $12 to $15."})
    with harness.container.db.session() as s:
        snap = s.get(Snapshot, job["snapshot_id"])
        assert snap is not None
        snap.commit_sha = "tampered"
        with pytest.raises(RuntimeError, match="immutable"):
            s.flush()
        s.rollback()
    with harness.container.db.session() as s:
        rev = s.get(ConfigRevision, job["proposed_revision_id"])
        assert rev is not None
        rev.content_hash = "0" * 64
        with pytest.raises(RuntimeError, match="immutable"):
            s.flush()
        s.rollback()


def test_version_pointers_are_distinct(harness: Harness) -> None:
    pid = harness.project_id
    draft = harness.client.get(f"/v1/projects/{pid}/draft", headers=harness.headers()).json()
    cfg = draft["config"]
    cfg["pages"]["/"]["content"][1]["props"]["heading"] = "Draft heading (not live)"
    saved = harness.client.put(
        f"/v1/projects/{pid}/draft", json={"config": cfg, "expected_draft_revision_id": draft["id"]}, headers=harness.headers()
    ).json()
    pointers = harness.project()["pointers"]
    assert pointers["draft_revision_id"] == saved["id"] != pointers["published_revision_id"]
    assert harness.public_config()["config"]["pages"]["/"]["content"][1]["props"]["heading"] != "Draft heading (not live)"

    job = harness.submit({"use_draft": True})
    assert job["state"] == "succeeded", job["error"]
    pointers = harness.project()["pointers"]
    assert pointers["published_revision_id"] == job["proposed_revision_id"]
    assert pointers["last_validated_revision_id"] == job["proposed_revision_id"]
    assert pointers["last_successful_snapshot_id"] == job["snapshot_id"]
    assert pointers["draft_revision_id"] == pointers["published_revision_id"]
    assert harness.public_config()["config"]["pages"]["/"]["content"][1]["props"]["heading"] == "Draft heading (not live)"


def test_open_draft_is_rebased_not_discarded(harness: Harness) -> None:
    pid = harness.project_id
    draft = harness.client.get(f"/v1/projects/{pid}/draft", headers=harness.headers()).json()
    cfg = draft["config"]
    cfg["pages"]["/shop"]["content"][0]["props"]["text"] = "Everything we make"
    harness.client.put(
        f"/v1/projects/{pid}/draft", json={"config": cfg, "expected_draft_revision_id": draft["id"]}, headers=harness.headers()
    )
    assert harness.submit({"request": "Change a pottery product price from $12 to $15."})["state"] == "succeeded"
    new_draft = harness.client.get(f"/v1/projects/{pid}/draft", headers=harness.headers()).json()["config"]
    assert new_draft["pages"]["/shop"]["content"][0]["props"]["text"] == "Everything we make"
    assert new_draft["pages"]["/"]["content"][3]["props"]["products"][0]["price"] == 15


def test_clarification_round_trip(harness: Harness) -> None:
    job = harness.submit({"request": "Change the price to $10"})
    assert job["state"] == "needs_clarification"
    assert job["plan"]["clarifications"][0]["options"]
    assert harness.project()["pointers"]["active_job_id"] is None
    # Without an LLM the answer is folded into the request text for the rules to re-run.
    job = harness.client.post(
        f"/v1/jobs/{job['id']}/clarify", json={"answers": ["the Rice Plate"]}, headers=harness.headers()
    ).json()
    assert job["state"] in ("needs_clarification", "succeeded")
    assert job["clarification_answers"] == ["the Rice Plate"]


def test_cancel_waiting_job(harness: Harness) -> None:
    job = harness.submit({"request": "Change the price to $10"})
    cancelled = harness.client.post(f"/v1/jobs/{job['id']}/cancel", headers=harness.headers()).json()
    assert cancelled["state"] == "cancelled"
    again = harness.client.post(f"/v1/jobs/{job['id']}/cancel", headers=harness.headers())
    assert again.status_code == 409


def test_rollback_restores_known_good_snapshot(harness: Harness) -> None:
    first = harness.submit({"request": "Change a pottery product price from $12 to $15."})
    good_snapshot = first["snapshot_id"]
    good_config = harness.public_config()
    harness.planner.source_plans.append(customizer_plan())
    harness.planner.proposals.append(customizer_proposal())
    second = harness.submit({"request": "Add a custom interactive pottery customization tool"})
    assert second["state"] == "succeeded"
    assert "src/components/generated/PotteryCustomizer.tsx" in harness.main_files()

    resp = harness.client.post(
        f"/v1/projects/{harness.project_id}/rollback",
        json={"snapshot_id": good_snapshot},
        headers=harness.headers(roles=("owner",)),
    )
    rb = resp.json()
    assert rb["state"] == "succeeded", rb["error"]
    assert rb["outcome"] == "rolled_back"
    assert harness.public_config()["config_hash"] == good_config["config_hash"]
    assert "src/components/generated/PotteryCustomizer.tsx" not in harness.main_files()  # tree restored via a new commit
    snaps = harness.snapshots()
    assert snaps[0]["rollback_of_snapshot_id"] == good_snapshot and snaps[0]["is_current"]
    assert len(snaps) == 3  # history is preserved, never rewritten


def test_rollback_requires_owner(harness: Harness) -> None:
    first = harness.submit({"request": "Change a pottery product price from $12 to $15."})
    resp = harness.client.post(
        f"/v1/projects/{harness.project_id}/rollback",
        json={"snapshot_id": first["snapshot_id"]},
        headers=harness.headers(roles=("editor",)),
    )
    assert resp.status_code == 403


def test_large_removal_requires_approval(harness: Harness) -> None:
    proposed = pottery_site()
    proposed["pages"]["/"]["content"] = proposed["pages"]["/"]["content"][:2]
    job = harness.submit({"proposed_config": proposed})
    assert job["state"] == "awaiting_approval"
    assert job["plan"]["approval_reasons"] == ["large_removal"]


def test_source_path_without_llm_fails_clearly(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from conftest import ORG, REPO, SECRET, repo_files
    from webblock_harness.analyzer.llm import UnavailablePlanner
    from webblock_harness.api.app import create_app
    from webblock_harness.db import Database
    from webblock_harness.integrations.deploy import FakeDeployProvider
    from webblock_harness.integrations.git import InMemoryGitProvider
    from webblock_harness.integrations.storage import LocalArtifactStore
    from webblock_harness.registry.registry import load_base_registry
    from webblock_harness.services.container import Container

    settings = make_settings(tmp_path)
    git = InMemoryGitProvider()
    git.create_repo(ORG, REPO, repo_files())
    db = Database(settings.database_url)
    db.create_all()
    c = Container(
        settings=settings,
        db=db,
        store=LocalArtifactStore(settings.artifact_dir),
        git=git,
        deploy=FakeDeployProvider(),
        planner=UnavailablePlanner(),
        registry=load_base_registry(),
        sleep=lambda _: None,
    )
    h = Harness(client=None, container=c, git=git, deploy=c.deploy, planner=None)  # type: ignore[arg-type]
    with TestClient(create_app(c)) as client:
        hdr = {"Authorization": f"Bearer {h.token()}"}
        assert SECRET
        pid = client.post("/v1/projects", json={"name": "x", "repo_name": REPO}, headers=hdr).json()["id"]
        job = client.post(
            f"/v1/projects/{pid}/jobs", json={"request": "Add a custom interactive pottery customization tool"}, headers=hdr
        ).json()
    assert job["state"] == "failed"
    assert job["error"]["code"] == "llm_unavailable" and job["error"]["retryable"] is True
    assert git.write_count == 0


def test_commit_delivery_mode_ships_content_through_a_build(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """When the client app bundles its config at build time, content changes are committed and built — still no codegen."""
    import json

    from conftest import ORG, REPO, build_harness
    from webblock_harness.integrations.git import RepoRef

    with build_harness(tmp_path, CONFIG_DELIVERY_MODE="commit") as h:
        head_before = h.main_head()
        job = h.submit({"request": "Change a pottery product price from $12 to $15."})
        assert job["state"] == "succeeded", job["error"]
        assert job["path"] == "runtime_config" and h.planner.calls == []
        committed = json.loads(h.main_files()["webblock/site-config.json"])
        assert committed["pages"]["/"]["content"][3]["props"]["products"][0]["price"] == 15
        assert h.main_head() == job["merged_commit_sha"] != head_before
        assert [d["provider"] for d in h.deployments()] == ["fake"]
        assert h.snapshots()[0]["commit_sha"] == job["merged_commit_sha"]
        # Only the config file changed in the commit.
        assert h.git.changed_files(RepoRef(ORG, REPO), head_before, h.main_head()) == ["webblock/site-config.json"]


def test_background_worker_runs_jobs_asynchronously(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import time

    from conftest import build_harness

    with build_harness(tmp_path, JOB_EXECUTION="background", DATABASE_URL=f"sqlite:///{tmp_path}/bg.db") as h:
        job = h.submit({"request": "Change a pottery product price from $12 to $15."})
        assert job["state"] in ("queued", "analyzing", "planned", "applying", "validating", "deploying", "verifying", "succeeded")
        deadline = time.time() + 10
        while time.time() < deadline and h.job(job["id"])["state"] != "succeeded":
            time.sleep(0.05)
        assert h.job(job["id"])["state"] == "succeeded"
