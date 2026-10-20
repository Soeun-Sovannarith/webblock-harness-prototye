"""Offline end-to-end demo: in-memory GitHub, fake deploys, no LLM, no network.

    uv run python scripts/demo.py

Registers a pottery site, then runs a content change (Path A), a reorder (Path A),
an ambiguous request (clarification) and a capability request (Path B, which stops
with `llm_unavailable` because the demo has no LLM configured).
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import jwt
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from conftest import ORG, REPO, SECRET, make_settings, pottery_site, repo_files  # noqa: E402
from webblock_harness.analyzer.llm import UnavailablePlanner  # noqa: E402
from webblock_harness.api.app import create_app  # noqa: E402
from webblock_harness.db import Database  # noqa: E402
from webblock_harness.integrations.deploy import FakeDeployProvider  # noqa: E402
from webblock_harness.integrations.git import InMemoryGitProvider  # noqa: E402
from webblock_harness.integrations.storage import LocalArtifactStore  # noqa: E402
from webblock_harness.registry.registry import load_base_registry  # noqa: E402
from webblock_harness.services.container import Container  # noqa: E402


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="webblock-demo-"))
    settings = make_settings(tmp)
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
    token = jwt.encode(
        {"sub": "demo", "tenant_id": "pottery-co", "roles": ["admin"], "exp": int(time.time()) + 600}, SECRET, "HS256"
    )
    hdr = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(c)) as client:
        project = client.post(
            "/v1/projects", json={"name": "Kampot Clay", "repo_name": REPO, "initial_config": pottery_site()}, headers=hdr
        ).json()
        print(f"project {project['id']} → {project['repository']}\n")
        for request in [
            "Change a pottery product price from $12 to $15.",
            "Move the product gallery above the product description",
            "Change the price to $10",
            "Add a custom interactive pottery customization tool",
        ]:
            job = client.post(f"/v1/projects/{project['id']}/jobs", json={"request": request}, headers=hdr).json()
            plan = job["plan"] or {}
            print(f"» {request}")
            print(f"  state={job['state']} path={job['path']} outcome={job['outcome']}")
            print(f"  plan: {plan.get('summary')}")
            for q in plan.get("clarifications", []):
                print(f"  question: {q['question']} options={q['options'][:3]}")
            if job["error"]:
                print(f"  error: {job['error']['code']} — {job['error']['message']}")
            print()
        live = client.get(f"/v1/public/sites/{project['site_key']}/config").json()
        ids = [x["props"]["id"] for x in live["config"]["pages"]["/"]["content"]]
        print("live home page order:", ids)
        print("live Lotus Tea Cup price:", live["config"]["pages"]["/"]["content"][2]["props"]["products"][0]["price"])
        print(
            "snapshots:", json.dumps([s["id"] for s in client.get(f"/v1/projects/{project['id']}/snapshots", headers=hdr).json()])
        )


if __name__ == "__main__":
    main()
