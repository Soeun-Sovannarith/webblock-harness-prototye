"""Tenant-scoped project, configuration-revision and registry management.

Every lookup takes the authenticated ``Principal`` and filters by its tenant;
a project owned by another tenant is indistinguishable from a missing one.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from webblock_harness.domain import ComponentSpec, ValidationReport
from webblock_harness.integrations.git import GitError, RepoRef
from webblock_harness.models import ComponentDefinition, ConfigRevision, Project, Snapshot, Tenant
from webblock_harness.puck.diff import diff_sites
from webblock_harness.puck.normalize import ConfigFormatError, config_hash, empty_site, normalize_site
from webblock_harness.puck.ops import OpConflict, apply_ops
from webblock_harness.puck.tree import display_name, index_components
from webblock_harness.puck.validate import validate_site
from webblock_harness.registry.registry import ComponentRegistry
from webblock_harness.security.auth import Principal, Role
from webblock_harness.services.container import Container
from webblock_harness.services.errors import Conflict, Forbidden, NotFound, ServiceError, Unavailable, Unprocessable
from webblock_harness.templates.catalog import TEMPLATES, SiteProfile, slugify

_REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_TENANT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def require(principal: Principal, role: Role) -> None:
    if not principal.has(role):
        raise Forbidden("insufficient_role", f"This action requires the {role.value} role.")


class ProjectService:
    def __init__(self, c: Container) -> None:
        self.c = c

    # -- tenants ------------------------------------------------------------
    def ensure_tenant(self, session: Session, tenant_id: str, name: str | None = None) -> Tenant:
        if not _TENANT_ID.match(tenant_id):
            raise Unprocessable("invalid_tenant_id", "Tenant ids may only contain letters, digits, '-' and '_'.")
        tenant = session.get(Tenant, tenant_id)
        if tenant is None:
            tenant = Tenant(id=tenant_id, name=name or tenant_id)
            session.add(tenant)
            session.flush()
        return tenant

    # -- projects -----------------------------------------------------------
    def get(self, session: Session, principal: Principal, project_id: str) -> Project:
        project = session.scalar(select(Project).where(Project.id == project_id, Project.tenant_id == principal.tenant_id))
        if project is None:
            raise NotFound("project_not_found", "Project not found.")
        return project

    def list_projects(self, session: Session, principal: Principal) -> list[Project]:
        return list(session.scalars(select(Project).where(Project.tenant_id == principal.tenant_id).order_by(Project.created_at)))

    def register(
        self,
        session: Session,
        principal: Principal,
        *,
        name: str,
        repo_name: str,
        initial_config: dict[str, Any] | None,
    ) -> Project:
        require(principal, Role.ADMIN)
        org = self.c.settings.github_org
        if not org:
            raise Unavailable("github_org_not_configured", "GITHUB_ORG is not configured on the harness.")
        if not _REPO_NAME.match(repo_name) or repo_name.endswith(".git"):
            raise Unprocessable("invalid_repo_name", "Repository name is not valid.")
        repo = RepoRef(org, repo_name)
        try:
            info = self.c.git.get_repo(repo)
        except GitError as exc:
            raise Unavailable("git_unavailable", exc.message) from exc
        # Repositories are always resolved inside WebBlock's organisation; a client can never point the
        # harness at an arbitrary repository, and a repository can be mapped to only one project.
        if info is None or info.owner.lower() != org.lower():
            raise Unprocessable("repository_not_found", f"Repository {repo_name!r} was not found in the WebBlock organisation.")
        if info.archived:
            raise Unprocessable("repository_archived", "Repository is archived.")
        existing = session.scalar(
            select(Project).where(
                func.lower(Project.repo_owner) == org.lower(), func.lower(Project.repo_name) == repo_name.lower()
            )
        )
        if existing is not None:
            raise Conflict("repository_already_mapped", "This repository is already associated with a project.")
        self.ensure_tenant(session, principal.tenant_id)
        head = self.c.git.get_branch_head(repo, info.default_branch)
        try:
            config = normalize_site(initial_config) if initial_config is not None else empty_site()
        except ConfigFormatError as exc:
            raise Unprocessable("invalid_config", str(exc)) from exc
        validation = validate_site(config, self.c.registry)
        if validation.status.value != "passed":
            raise Unprocessable(
                "invalid_config",
                "The initial configuration does not match the component registry.",
                {"issues": [i.model_dump(mode="json") for i in validation.issues][:50]},
            )
        project = Project(
            tenant_id=principal.tenant_id,
            name=name,
            repo_owner=info.owner,
            repo_name=info.name,
            default_branch=info.default_branch,
            source_commit_sha=head,
        )
        session.add(project)
        try:
            session.flush()
        except IntegrityError as exc:
            raise Conflict("repository_already_mapped", "This repository is already associated with a project.") from exc
        rev = self.create_revision(
            session,
            project,
            config,
            status="published",
            created_by=principal.user_id,
            validation=ValidationReport(checks=[validation]),
        )
        project.published_revision_id = rev.id
        project.draft_revision_id = rev.id
        project.draft_base_revision_id = rev.id
        project.last_validated_revision_id = rev.id
        return project

    # -- onboarding: form → template → project (repository created on first deploy) --
    def create_site(self, session: Session, principal: Principal, profile: SiteProfile, template_id: str) -> Project:
        require(principal, Role.EDITOR)
        template = TEMPLATES.get(template_id)
        if template is None:
            raise Unprocessable("unknown_template", f"Template {template_id!r} does not exist.", {"templates": sorted(TEMPLATES)})
        org = self.c.settings.github_org
        if not org:
            raise Unavailable(
                "github_org_not_configured", "GITHUB_ORG / GITHUB_ORGANIZATION_NAME is not configured on the harness."
            )
        config = normalize_site(template.build(profile))
        validation = validate_site(config, self.c.registry)
        if validation.status.value != "passed":  # pragma: no cover - templates are tested against the registry
            raise ServiceError("template_invalid", f"Template {template_id} produced an invalid configuration.")
        self.ensure_tenant(session, principal.tenant_id)
        repo_name = self._reserve_repo_name(session, org, slugify(profile.business_name) + self.c.settings.new_repo_suffix)
        project = Project(
            tenant_id=principal.tenant_id,
            name=profile.business_name,
            repo_owner=org,
            repo_name=repo_name,
            default_branch="main",
            repo_provisioned=False,
            template_id=template_id,
            profile=profile.model_dump(mode="json"),
        )
        session.add(project)
        try:
            session.flush()
        except IntegrityError as exc:
            raise Conflict("repository_name_taken", "That site name was just taken; please try again.") from exc
        rev = self.create_revision(
            session,
            project,
            config,
            status="baseline",
            created_by=principal.user_id,
            validation=ValidationReport(checks=[validation]),
        )
        project.published_revision_id = rev.id
        project.draft_revision_id = rev.id
        project.draft_base_revision_id = rev.id
        project.last_validated_revision_id = rev.id
        return project

    def _reserve_repo_name(self, session: Session, org: str, base: str) -> str:
        base = base[:90].strip("-")
        for n in range(1, 50):
            name = base if n == 1 else f"{base}-{n}"
            taken = session.scalar(
                select(Project.id).where(
                    func.lower(Project.repo_owner) == org.lower(), func.lower(Project.repo_name) == name.lower()
                )
            )
            if taken:
                continue
            try:
                if self.c.git.get_repo(RepoRef(org, name)) is not None:
                    continue
            except GitError as exc:
                raise Unavailable("git_unavailable", exc.message) from exc
            return name
        raise Conflict("repository_name_taken", "Could not find a free repository name; choose a different business name.")

    # -- GitHub → harness: pick up edits made directly in the repository ------
    def sync_from_repository(self, session: Session, principal: Principal, project_id: str) -> dict[str, Any]:
        require(principal, Role.EDITOR)
        project = self.get(session, principal, project_id)
        if not project.repo_provisioned:
            return {"status": "not_deployed"}
        if project.lock_job_id:
            raise Conflict("project_busy", "A deployment is in progress; try again when it finishes.")
        repo = self.repo(project)
        path = self.c.settings.config_repo_path
        try:
            head = self.c.git.get_branch_head(repo, project.default_branch)
            blob = self.c.git.read_file(repo, path, head) if head else None
        except GitError as exc:
            raise Unavailable("git_unavailable", exc.message) from exc
        if blob is None:
            return {"status": "missing_config", "path": path, "commit_sha": head}
        try:
            repo_config = normalize_site(blob.content)
        except ConfigFormatError as exc:
            return {"status": "invalid_repo_config", "path": path, "commit_sha": head, "error": str(exc)}
        pub_rev, _ = self.published(session, project)
        project.source_commit_sha = head
        if config_hash(repo_config) == pub_rev.content_hash:
            return {"status": "in_sync", "commit_sha": head}
        registry = self.registry_for(session, project)
        validation = validate_site(repo_config, registry)
        if validation.status.value != "passed":
            return {
                "status": "invalid_repo_config",
                "path": path,
                "commit_sha": head,
                "issues": [i.model_dump(mode="json") for i in validation.issues][:50],
            }
        new_rev = self.create_revision(
            session,
            project,
            repo_config,
            status="published",
            created_by=f"github:{(head or '')[:12]}",
            parent_id=pub_rev.id,
            validation=ValidationReport(checks=[validation]),
        )
        draft_result = "replaced"
        if project.draft_revision_id not in (None, pub_rev.id):
            # Rebase the editor's unpublished edits onto the repository version.
            base_rev = self.revision(session, project, project.draft_base_revision_id or pub_rev.id)
            draft_rev = self.revision(session, project, project.draft_revision_id)
            ops = diff_sites(self.load(project, base_rev), self.load(project, draft_rev), registry).ops
            try:
                rebased = apply_ops(repo_config, ops, registry)
            except OpConflict:
                draft_result = "conflict"
            else:
                d = self.create_revision(
                    session, project, rebased, status="draft", created_by=principal.user_id, parent_id=draft_rev.id
                )
                project.draft_revision_id = d.id
                project.draft_base_revision_id = new_rev.id
                draft_result = "rebased"
        else:
            project.draft_revision_id = new_rev.id
            project.draft_base_revision_id = new_rev.id
        project.published_revision_id = new_rev.id
        project.last_validated_revision_id = new_rev.id
        return {"status": "imported", "revision_id": new_rev.id, "commit_sha": head, "draft": draft_result}

    def repo(self, project: Project) -> RepoRef:
        return RepoRef(project.repo_owner, project.repo_name)

    # -- registry -----------------------------------------------------------
    def registry_for(self, session: Session, project: Project, include_proposed_for_job: str | None = None) -> ComponentRegistry:
        rows = session.scalars(
            select(ComponentDefinition).where(
                ComponentDefinition.project_id == project.id, ComponentDefinition.tenant_id == project.tenant_id
            )
        )
        specs = [
            ComponentSpec.model_validate(r.spec)
            for r in rows
            if r.status == "active"
            or (include_proposed_for_job and r.job_id == include_proposed_for_job and r.status == "proposed")
        ]
        return self.c.registry.with_extensions(specs)

    # -- revisions ----------------------------------------------------------
    def create_revision(
        self,
        session: Session,
        project: Project,
        config: dict[str, Any],
        *,
        status: str,
        created_by: str,
        job_id: str | None = None,
        parent_id: str | None = None,
        validation: ValidationReport | None = None,
    ) -> ConfigRevision:
        key, digest = self.c.store.put_json(project.tenant_id, project.id, "configs", config)
        number = (session.scalar(select(func.max(ConfigRevision.number)).where(ConfigRevision.project_id == project.id)) or 0) + 1
        rev = ConfigRevision(
            tenant_id=project.tenant_id,
            project_id=project.id,
            number=number,
            status=status,
            content_hash=digest,
            storage_key=key,
            parent_revision_id=parent_id,
            job_id=job_id,
            created_by=created_by,
            validation=validation.model_dump(mode="json") if validation else None,
        )
        session.add(rev)
        session.flush()
        return rev

    def revision(self, session: Session, project: Project, revision_id: str | None) -> ConfigRevision:
        rev = session.get(ConfigRevision, revision_id) if revision_id else None
        if rev is None or rev.project_id != project.id or rev.tenant_id != project.tenant_id:
            raise NotFound("revision_not_found", "Revision not found.")
        return rev

    def load(self, project: Project, rev: ConfigRevision) -> dict[str, Any]:
        config = self.c.store.get_json(project.tenant_id, rev.storage_key)
        if config_hash(config) != rev.content_hash:
            raise ServiceError("artifact_corrupted", f"Stored configuration for revision {rev.id} does not match its hash.")
        return config  # type: ignore[no-any-return]

    def published(self, session: Session, project: Project) -> tuple[ConfigRevision, dict[str, Any]]:
        rev = self.revision(session, project, project.published_revision_id)
        return rev, self.load(project, rev)

    def list_revisions(self, session: Session, project: Project, limit: int = 50) -> list[ConfigRevision]:
        return list(
            session.scalars(
                select(ConfigRevision)
                .where(ConfigRevision.project_id == project.id)
                .order_by(ConfigRevision.number.desc())
                .limit(limit)
            )
        )

    # -- drafts -------------------------------------------------------------
    def save_draft(
        self,
        session: Session,
        principal: Principal,
        project_id: str,
        config: dict[str, Any],
        expected_draft_revision_id: str | None,
    ) -> tuple[ConfigRevision, ValidationReport]:
        require(principal, Role.EDITOR)
        project = self.get(session, principal, project_id)
        if expected_draft_revision_id != project.draft_revision_id:
            raise Conflict(
                "stale_draft",
                "The draft was changed by someone else; reload it before saving.",
                {"current_draft_revision_id": project.draft_revision_id},
            )
        try:
            normalized = normalize_site(config)
        except ConfigFormatError as exc:
            raise Unprocessable("invalid_config", str(exc)) from exc
        report = ValidationReport(checks=[validate_site(normalized, self.registry_for(session, project))])
        rev = self.create_revision(
            session,
            project,
            normalized,
            status="draft",
            created_by=principal.user_id,
            parent_id=project.draft_revision_id,
            validation=report,
        )
        # The new draft keeps the base the editor's draft was derived from; that base is what a later
        # deploy diffs against, so only the client's own edits are replayed onto the latest version.
        if project.draft_base_revision_id is None or project.draft_revision_id == project.published_revision_id:
            project.draft_base_revision_id = project.published_revision_id
        project.draft_revision_id = rev.id
        return rev, report

    # -- public delivery ----------------------------------------------------
    def public_config(self, session: Session, site_key: str) -> tuple[dict[str, Any], str]:
        project = session.scalar(select(Project).where(Project.site_key == site_key))
        if project is None or project.published_revision_id is None:
            raise NotFound("site_not_found", "Site not found.")
        rev, config = self.published(session, project)
        return config, rev.content_hash

    # -- inspection ---------------------------------------------------------
    def context(self, session: Session, principal: Principal, project_id: str, include_files: bool = True) -> dict[str, Any]:
        project = self.get(session, principal, project_id)
        registry = self.registry_for(session, project)
        rev, config = self.published(session, project)
        pages = {}
        for key, page in config["pages"].items():
            comps = index_components(key, page, registry)
            pages[key] = [
                {"id": c.id, "type": c.type, "label": display_name(c.component), "container": c.container} for c in comps.values()
            ]
        source: dict[str, Any] = {"recorded_head": project.source_commit_sha}
        if include_files:
            try:
                head = self.c.git.get_branch_head(self.repo(project), project.default_branch)
                files = self.c.git.list_files(self.repo(project), head) if head else []
                source |= {
                    "live_head": head,
                    "head_moved_outside_harness": bool(head and project.source_commit_sha and head != project.source_commit_sha),
                    "component_files": [f for f in files if f.startswith("src/components/")][:500],
                    "file_count": len(files),
                }
            except GitError as exc:
                source |= {"error": exc.code}
        snap = session.get(Snapshot, project.last_successful_snapshot_id) if project.last_successful_snapshot_id else None
        return {
            "project_id": project.id,
            "pointers": pointers(project),
            "published_revision": {"id": rev.id, "number": rev.number, "hash": rev.content_hash},
            "pages": pages,
            "registry": {"hash": registry.hash, "components": registry.summary()},
            "source": source,
            "last_successful_snapshot": snapshot_view(snap) if snap else None,
        }


def pointers(project: Project) -> dict[str, Any]:
    return {
        "draft_revision_id": project.draft_revision_id,
        "draft_base_revision_id": project.draft_base_revision_id,
        "last_validated_revision_id": project.last_validated_revision_id,
        "published_revision_id": project.published_revision_id,
        "last_successful_snapshot_id": project.last_successful_snapshot_id,
        "source_commit_sha": project.source_commit_sha,
        "active_job_id": project.lock_job_id,
    }


def snapshot_view(s: Snapshot) -> dict[str, Any]:
    return {
        "id": s.id,
        "job_id": s.job_id,
        "deployment_id": s.deployment_id,
        "config_revision_id": s.config_revision_id,
        "config_hash": s.config_hash,
        "commit_sha": s.commit_sha,
        "registry_hash": s.registry_hash,
        "manifest_hash": s.manifest_hash,
        "rollback_of_snapshot_id": s.rollback_of_snapshot_id,
        "validation": s.validation,
        "build": s.build,
        "created_at": s.created_at.isoformat(),
    }
