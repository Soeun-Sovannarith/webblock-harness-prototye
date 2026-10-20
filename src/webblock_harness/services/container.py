"""Dependency wiring. Tests build a Container with fakes; production builds it from Settings."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from webblock_harness.analyzer.llm import Planner, make_planner
from webblock_harness.config import Settings
from webblock_harness.db import Database
from webblock_harness.domain import ApprovalReason
from webblock_harness.integrations.deploy import DeployProvider, make_deploy_provider
from webblock_harness.integrations.git import GitProvider, make_git_provider
from webblock_harness.integrations.storage import ArtifactStore, LocalArtifactStore
from webblock_harness.registry.registry import ComponentRegistry, load_base_registry
from webblock_harness.security.portal_tokens import PortalSigner, load_signer


@dataclass
class Container:
    settings: Settings
    db: Database
    store: ArtifactStore
    git: GitProvider
    deploy: DeployProvider
    planner: Planner
    registry: ComponentRegistry
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    auto_approve: set[ApprovalReason] = field(default_factory=set)
    revalidate: Callable[[str, str], None] | None = None  # (site_key, config_hash) -> None
    portal_signer: PortalSigner | None = None

    def signer(self) -> PortalSigner:
        if self.portal_signer is None:
            self.portal_signer = load_signer(self.settings)
        return self.portal_signer

    @classmethod
    def from_settings(cls, settings: Settings) -> Container:
        db = Database(settings.database_url)
        db.create_all()
        git = make_git_provider(settings)
        return cls(
            settings=settings,
            db=db,
            store=LocalArtifactStore(settings.artifact_dir),
            git=git,
            deploy=make_deploy_provider(settings, git),
            planner=make_planner(settings),
            registry=load_base_registry(),
        )
