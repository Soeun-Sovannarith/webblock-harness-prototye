"""Shared fixtures: an in-memory GitHub, a scriptable deploy provider and a scripted LLM planner.

No test touches the network, a real repository or a real deployment.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from webblock_harness.analyzer.llm import ConfigSuggestion
from webblock_harness.api.app import create_app
from webblock_harness.config import Settings
from webblock_harness.db import Database
from webblock_harness.domain import (
    AddComponentOp,
    ApiEndpointProposal,
    CodeChangeProposal,
    ComponentSpec,
    FieldSpec,
    FileAction,
    FileChange,
    MigrationProposal,
    NewComponentProposal,
    PlannedFile,
    Scope,
    SourcePlan,
)
from webblock_harness.integrations.deploy import FakeDeployProvider, GitHubPushDeployProvider
from webblock_harness.integrations.git import InMemoryGitProvider
from webblock_harness.integrations.storage import LocalArtifactStore
from webblock_harness.registry.registry import load_base_registry
from webblock_harness.services.container import Container

SECRET = "test-secret-please-change-0123456789abcdef"
ORG = "webblock-test"
REPO = "kampot-clay"
FIXTURES = Path(__file__).parent / "fixtures"
ALL_CHECKS = ["format", "typecheck", "lint", "test", "build", "integration-tests", "migration-test"]


def pottery_site() -> dict[str, Any]:
    return json.loads((FIXTURES / "pottery_site.json").read_text())


def repo_files() -> dict[str, str]:
    files = {
        "package.json": json.dumps({"name": "kampot-clay", "dependencies": {"next": "15.5.4", "@measured/puck": "0.20.2"}}),
        "package-lock.json": "{}",
        ".env": "DATABASE_URL=postgres://client-db\n",
        ".github/workflows/deploy.yml": "name: deploy\n",
        "next.config.ts": "export default {};\n",
        "src/app/page.tsx": "export default function Page() { return null; }\n",
        "src/puck.config.tsx": "export const config = {};\n",
    }
    for spec in load_base_registry().components.values():
        assert spec.source_path
        files[spec.source_path] = f"export function {spec.name}() {{ return null; }}\n"
    return files


class FakePlanner:
    """Scripted stand-in for the LLM. Records every call so tests can assert it was (not) used."""

    available = True

    def __init__(self) -> None:
        self.config_suggestions: list[ConfigSuggestion] = []
        self.source_plans: list[SourcePlan] = []
        self.proposals: list[CodeChangeProposal | Callable[[dict[str, str]], CodeChangeProposal]] = []
        self.calls: list[str] = []
        self.last_context_files: dict[str, str] = {}

    def suggest_config_change(
        self, request: str, site_config: dict[str, Any], registry_summary: list[dict[str, Any]], answers: list[str]
    ) -> ConfigSuggestion:
        self.calls.append("suggest_config_change")
        return self.config_suggestions.pop(0)

    def plan_source_change(
        self, request: str, registry_summary: list[dict[str, Any]], repo_files: list[str], answers: list[str]
    ) -> SourcePlan:
        self.calls.append("plan_source_change")
        return self.source_plans.pop(0)

    def generate_patch(
        self, request: str, plan: SourcePlan, files: dict[str, str], registry_summary: list[dict[str, Any]]
    ) -> CodeChangeProposal:
        self.calls.append("generate_patch")
        self.last_context_files = files
        nxt = self.proposals.pop(0)
        return nxt(files) if callable(nxt) else nxt


# --------------------------------------------------------------------------
# Canned Path B plans/proposals
# --------------------------------------------------------------------------

CUSTOMIZER_TSX = """\
"use client";
import { useState } from "react";

type Glaze = { name: string; color: string };

export function PotteryCustomizer({ title, glazes }: { title: string; glazes: Glaze[] }) {
  const [glaze, setGlaze] = useState(glazes[0]?.name ?? "");
  const [size, setSize] = useState("medium");
  return (
    <section>
      <h2>{title}</h2>
      <select value={glaze} onChange={(e) => setGlaze(e.target.value)}>
        {glazes.map((g) => <option key={g.name}>{g.name}</option>)}
      </select>
      <select value={size} onChange={(e) => setSize(e.target.value)}>
        <option>small</option><option>medium</option><option>large</option>
      </select>
      <p>Your {size} piece in {glaze}.</p>
    </section>
  );
}
"""


def customizer_plan() -> SourcePlan:
    return SourcePlan(
        summary="Add an interactive PotteryCustomizer component",
        scopes=[Scope.FRONTEND],
        files_to_read=["src/puck.config.tsx", "src/components/webblock/ProductGallery.tsx"],
        files_to_change=[
            PlannedFile(path="src/components/generated/PotteryCustomizer.tsx", action="create", reason="new component"),
            PlannedFile(path="src/components/generated/PotteryCustomizer.test.tsx", action="create", reason="tests"),
        ],
        new_components=["PotteryCustomizer"],
        integration_tests=[],
    )


def customizer_spec() -> ComponentSpec:
    return ComponentSpec(
        name="PotteryCustomizer",
        label="Pottery customizer",
        description="Choose glaze and size",
        fields={
            "title": FieldSpec(type="text", required=True, max_length=120),
            "glazes": FieldSpec(
                type="array", item_fields={"name": FieldSpec(type="text", required=True), "color": FieldSpec(type="color")}
            ),
        },
        default_props={"title": "Design your piece", "glazes": [{"name": "Celadon", "color": "#9fc5a8"}]},
        source_path="src/components/generated/PotteryCustomizer.tsx",
        export_name="PotteryCustomizer",
    )


def customizer_proposal(content: str = CUSTOMIZER_TSX, extra_files: list[FileChange] | None = None) -> CodeChangeProposal:
    return CodeChangeProposal(
        summary="Add PotteryCustomizer",
        scopes=[Scope.FRONTEND],
        files=[
            FileChange(path="src/components/generated/PotteryCustomizer.tsx", action=FileAction.CREATE, content=content),
            FileChange(
                path="src/components/generated/PotteryCustomizer.test.tsx",
                action=FileAction.CREATE,
                content='import { PotteryCustomizer } from "./PotteryCustomizer";\ntest("renders", () => { expect(PotteryCustomizer).toBeDefined(); });\n',
            ),
            *(extra_files or []),
        ],
        new_components=[
            NewComponentProposal(spec=customizer_spec(), source_file="src/components/generated/PotteryCustomizer.tsx")
        ],
        page_operations=[
            AddComponentOp(
                page="/",
                component={
                    "type": "PotteryCustomizer",
                    "props": {
                        "id": "customizer-1",
                        "title": "Design your piece",
                        "glazes": [{"name": "Celadon", "color": "#9fc5a8"}],
                    },
                },
                after_id="gallery-1",
            )
        ],
        tests=["src/components/generated/PotteryCustomizer.test.tsx"],
    )


BOOKING_ROUTE = """\
import { z } from "zod";
import { db } from "@/lib/server/db";

const Booking = z.object({ name: z.string().min(1), email: z.string().email(), date: z.string(), partySize: z.number().int().min(1).max(12) });

export async function POST(req: Request) {
  const parsed = Booking.safeParse(await req.json());
  if (!parsed.success) return Response.json({ error: "invalid_booking" }, { status: 400 });
  const row = await db.insertBooking(parsed.data);
  return Response.json({ id: row.id }, { status: 201 });
}
"""


def booking_plan() -> SourcePlan:
    return SourcePlan(
        summary="Add pottery-class booking: form component, bookings API and bookings table",
        scopes=[Scope.FRONTEND, Scope.BACKEND, Scope.DATABASE],
        files_to_read=["src/app/page.tsx"],
        files_to_change=[
            PlannedFile(path="src/components/generated/BookingForm.tsx", action="create"),
            PlannedFile(path="src/app/api/bookings/route.ts", action="create"),
            PlannedFile(path="drizzle/0001_bookings.sql", action="create"),
            PlannedFile(path="tests/bookings.test.ts", action="create"),
        ],
        new_components=["BookingForm"],
        api_endpoints=[
            ApiEndpointProposal(
                method="POST", path="/api/bookings", purpose="Create a booking", auth="public, rate-limited, validated"
            )
        ],
        migrations=[MigrationProposal(path="drizzle/0001_bookings.sql", description="create bookings table")],
        integration_tests=["tests/bookings.test.ts"],
    )


def booking_proposal(
    migration_sql: str = "CREATE TABLE bookings (id serial primary key, name text not null, email text not null, date date not null, party_size int not null);\n",
) -> CodeChangeProposal:
    spec = ComponentSpec(
        name="BookingForm",
        label="Booking form",
        fields={"heading": FieldSpec(type="text", required=True), "submitLabel": FieldSpec(type="text")},
        default_props={"heading": "Book a pottery class", "submitLabel": "Book"},
        source_path="src/components/generated/BookingForm.tsx",
        export_name="BookingForm",
    )
    return CodeChangeProposal(
        summary="Add booking feature",
        scopes=[Scope.FRONTEND, Scope.BACKEND, Scope.DATABASE],
        files=[
            FileChange(
                path="src/components/generated/BookingForm.tsx",
                action=FileAction.CREATE,
                content='"use client";\nexport function BookingForm({ heading }: { heading: string }) {\n  return <form action="/api/bookings" method="post"><h2>{heading}</h2></form>;\n}\n',
            ),
            FileChange(path="src/app/api/bookings/route.ts", action=FileAction.CREATE, content=BOOKING_ROUTE),
            FileChange(path="drizzle/0001_bookings.sql", action=FileAction.CREATE, content=migration_sql),
            FileChange(
                path="tests/bookings.test.ts", action=FileAction.CREATE, content='test("creates booking", async () => {});\n'
            ),
        ],
        new_components=[NewComponentProposal(spec=spec, source_file="src/components/generated/BookingForm.tsx")],
        api_endpoints=booking_plan().api_endpoints,
        migrations=booking_plan().migrations,
        dependencies=[],
        tests=["tests/bookings.test.ts"],
        page_operations=[
            AddComponentOp(
                page="/",
                component={
                    "type": "BookingForm",
                    "props": {"id": "booking-1", "heading": "Book a pottery class", "submitLabel": "Book"},
                },
                before_id="footer-1",
            )
        ],
    )


# --------------------------------------------------------------------------
# Harness fixture
# --------------------------------------------------------------------------


@dataclass
class Harness:
    client: TestClient
    container: Container
    git: InMemoryGitProvider
    deploy: FakeDeployProvider
    planner: FakePlanner
    project_id: str = ""
    site_key: str = ""
    tokens: dict[str, str] = field(default_factory=dict)

    def token(
        self,
        tenant: str = "tenant-a",
        roles: tuple[str, ...] = ("admin",),
        user: str = "user-1",
        secret: str = SECRET,
        **extra: Any,
    ) -> str:
        claims = {"sub": user, "tenant_id": tenant, "roles": list(roles), "exp": int(time.time()) + 3600, **extra}
        return jwt.encode(claims, secret, algorithm="HS256")

    def headers(self, tenant: str = "tenant-a", roles: tuple[str, ...] = ("admin",), **kw: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(tenant, roles, **kw)}"}

    def submit(self, body: dict[str, Any], headers: dict[str, str] | None = None, key: str | None = None) -> dict[str, Any]:
        h = dict(headers or self.headers())
        if key:
            h["Idempotency-Key"] = key
        resp = self.client.post(f"/v1/projects/{self.project_id}/jobs", json=body, headers=h)
        assert resp.status_code == 202, resp.text
        return resp.json()  # type: ignore[no-any-return]

    def job(self, job_id: str) -> dict[str, Any]:
        resp = self.client.get(f"/v1/jobs/{job_id}", headers=self.headers())
        assert resp.status_code == 200, resp.text
        return resp.json()  # type: ignore[no-any-return]

    def events(self, job_id: str) -> list[dict[str, Any]]:
        return self.client.get(f"/v1/jobs/{job_id}/events", headers=self.headers()).json()  # type: ignore[no-any-return]

    def public_config(self) -> dict[str, Any]:
        resp = self.client.get(f"/v1/public/sites/{self.site_key}/config")
        assert resp.status_code == 200
        return resp.json()  # type: ignore[no-any-return]

    def project(self) -> dict[str, Any]:
        return self.client.get(f"/v1/projects/{self.project_id}", headers=self.headers()).json()  # type: ignore[no-any-return]

    def snapshots(self) -> list[dict[str, Any]]:
        return self.client.get(f"/v1/projects/{self.project_id}/snapshots", headers=self.headers()).json()  # type: ignore[no-any-return]

    def deployments(self) -> list[dict[str, Any]]:
        return self.client.get(f"/v1/projects/{self.project_id}/deployments", headers=self.headers()).json()  # type: ignore[no-any-return]

    def main_head(self) -> str:
        return self.git.repos[f"{ORG}/{REPO}"].branches["main"]

    def main_files(self) -> dict[str, str]:
        r = self.git.repos[f"{ORG}/{REPO}"]
        return dict(r.commits[r.branches["main"]].files)


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "WEBBLOCK_ENV": "test",
        "DATABASE_URL": "sqlite:///:memory:",
        "ARTIFACT_DIR": str(tmp_path / "artifacts"),
        "AUTH_JWT_SECRET": SECRET,
        "GIT_PROVIDER": "memory",
        "GITHUB_ORG": ORG,
        "DEPLOY_PROVIDER": "fake",
        "CONFIG_DELIVERY_MODE": "runtime",
        "PORTAL_KEY_FILE": str(tmp_path / "portal_signing_key.pem"),
        "LLM_PROVIDER": "none",
        "JOB_EXECUTION": "inline",
        "POLL_INTERVAL_SECONDS": 0,
        "CI_TIMEOUT_SECONDS": 0,
        "DEPLOY_TIMEOUT_SECONDS": 0,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@contextmanager
def build_harness(tmp_path: Path, register: bool = True, **overrides: Any) -> Iterator[Harness]:
    settings = make_settings(tmp_path, **overrides)
    git = InMemoryGitProvider()
    git.create_repo(ORG, REPO, repo_files())
    git.create_repo(ORG, "other-client-site", repo_files())
    git.create_repo("someone-else", "evil-repo", repo_files())
    git.default_check_conclusions = {name: "success" for name in ALL_CHECKS}
    deploy: Any = (
        GitHubPushDeployProvider(settings, git) if settings.deploy_provider.value == "github_push" else FakeDeployProvider()
    )
    planner = FakePlanner()
    db = Database(settings.database_url)
    db.create_all()
    container = Container(
        settings=settings,
        db=db,
        store=LocalArtifactStore(settings.artifact_dir),
        git=git,
        deploy=deploy,
        planner=planner,
        registry=load_base_registry(),
        sleep=lambda _: None,
    )
    app = create_app(container)
    with TestClient(app) as client:
        h = Harness(client=client, container=container, git=git, deploy=deploy, planner=planner)
        if not register:
            yield h
            return
        resp = client.post(
            "/v1/projects",
            json={"name": "Kampot Clay Studio", "repo_name": REPO, "initial_config": pottery_site()},
            headers=h.headers(),
        )
        assert resp.status_code == 201, resp.text
        h.project_id = resp.json()["id"]
        h.site_key = resp.json()["site_key"]
        yield h


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    with build_harness(tmp_path) as h:
        yield h
