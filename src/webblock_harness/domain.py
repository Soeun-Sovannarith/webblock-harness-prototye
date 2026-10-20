"""Typed domain model shared by every harness stage.

These types are the contract between pipeline stages: each stage consumes and
produces one of them, and they are what gets persisted (as JSON) on jobs,
revisions and snapshots.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# Job lifecycle
# --------------------------------------------------------------------------


class JobState(StrEnum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    NEEDS_CLARIFICATION = "needs_clarification"
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLYING = "applying"
    VALIDATING = "validating"
    COMMITTING = "committing"
    BUILDING = "building"
    DEPLOYING = "deploying"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED})
# States where a human is expected to act; the project lock is released while waiting.
WAITING_STATES = frozenset({JobState.NEEDS_CLARIFICATION, JobState.AWAITING_APPROVAL})
# States in which the worker is actively mutating project state.
ACTIVE_STATES = frozenset(
    {
        JobState.ANALYZING,
        JobState.PLANNED,
        JobState.APPLYING,
        JobState.VALIDATING,
        JobState.COMMITTING,
        JobState.BUILDING,
        JobState.DEPLOYING,
        JobState.VERIFYING,
    }
)

_S = JobState
ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    _S.QUEUED: frozenset(
        {
            _S.ANALYZING,
            _S.APPLYING,
            _S.VALIDATING,
            _S.COMMITTING,
            _S.BUILDING,
            _S.DEPLOYING,
            _S.VERIFYING,
            _S.CANCELLED,
            _S.FAILED,
        }
    ),
    _S.ANALYZING: frozenset({_S.PLANNED, _S.NEEDS_CLARIFICATION, _S.SUCCEEDED, _S.FAILED, _S.CANCELLED}),
    _S.NEEDS_CLARIFICATION: frozenset({_S.QUEUED, _S.CANCELLED}),
    _S.PLANNED: frozenset({_S.AWAITING_APPROVAL, _S.APPLYING, _S.FAILED, _S.CANCELLED}),
    _S.AWAITING_APPROVAL: frozenset({_S.QUEUED, _S.FAILED, _S.CANCELLED}),
    _S.APPLYING: frozenset({_S.VALIDATING, _S.SUCCEEDED, _S.FAILED, _S.CANCELLED}),
    _S.VALIDATING: frozenset({_S.COMMITTING, _S.DEPLOYING, _S.FAILED, _S.CANCELLED}),
    _S.COMMITTING: frozenset({_S.BUILDING, _S.VALIDATING, _S.DEPLOYING, _S.SUCCEEDED, _S.FAILED}),
    _S.BUILDING: frozenset({_S.DEPLOYING, _S.FAILED}),
    _S.DEPLOYING: frozenset({_S.VERIFYING, _S.FAILED}),
    _S.VERIFYING: frozenset({_S.SUCCEEDED, _S.FAILED}),
    # A failed job may be retried from the stage that failed (only when marked retryable).
    _S.FAILED: frozenset({_S.QUEUED}),
    _S.SUCCEEDED: frozenset(),
    _S.CANCELLED: frozenset(),
}


class JobKind(StrEnum):
    CHANGE = "change"
    ROLLBACK = "rollback"
    PROVISION = "provision"  # first deploy: create the repository and push the site
    UPGRADE = "upgrade"  # push newer WebBlock site code (e.g. the backend) to an already-deployed site


class ExecutionPath(StrEnum):
    RUNTIME_CONFIG = "runtime_config"  # Path A
    SOURCE_CODE = "source_code"  # Path B
    NOOP = "noop"
    CLARIFICATION = "clarification"
    REJECTED = "rejected"


class JobOutcome(StrEnum):
    PROVISIONED = "provisioned"
    UPGRADED = "upgraded"
    DEPLOYED = "deployed"
    NO_CHANGES = "no_changes"
    ROLLED_BACK = "rolled_back"


# --------------------------------------------------------------------------
# Puck JSON operations (Path A)
# --------------------------------------------------------------------------


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: str = Field(description="Page key, e.g. '/' or '/shop'.")


class SetPropOp(_Op):
    op: Literal["set_prop"] = "set_prop"
    component_id: str
    # Path inside props, e.g. ["price"] or ["products", 1, "price"].
    path: list[str | int] = Field(min_length=1)
    value: Any
    # Optimistic precondition: the value the planner observed. Used to detect conflicts.
    expect: Any = None
    has_expect: bool = False


class SetRootPropOp(_Op):
    op: Literal["set_root_prop"] = "set_root_prop"
    path: list[str | int] = Field(min_length=1)
    value: Any
    expect: Any = None
    has_expect: bool = False


class MoveComponentOp(_Op):
    op: Literal["move_component"] = "move_component"
    component_id: str
    # Exactly one anchor: before/after another component, or an absolute index in a container.
    before_id: str | None = None
    after_id: str | None = None
    to_container: str | None = None  # container key; defaults to the component's current container
    to_index: int | None = None


class AddComponentOp(_Op):
    op: Literal["add_component"] = "add_component"
    component: dict[str, Any]  # {"type": ..., "props": {"id": ..., ...}}
    container: str = "content"
    before_id: str | None = None
    after_id: str | None = None
    index: int | None = None


class RemoveComponentOp(_Op):
    op: Literal["remove_component"] = "remove_component"
    component_id: str
    expect_type: str | None = None


class AddPageOp(_Op):
    op: Literal["add_page"] = "add_page"
    data: dict[str, Any]  # full Puck data for the new page


class RemovePageOp(_Op):
    op: Literal["remove_page"] = "remove_page"


PuckOp = Annotated[
    SetPropOp | SetRootPropOp | MoveComponentOp | AddComponentOp | RemoveComponentOp | AddPageOp | RemovePageOp,
    Field(discriminator="op"),
]


# --------------------------------------------------------------------------
# Change analysis
# --------------------------------------------------------------------------


class ChangeKind(StrEnum):
    CONTENT_UPDATE = "content_update"
    STYLE_UPDATE = "style_update"
    LAYOUT_UPDATE = "layout_update"
    COMPONENT_ADDED = "component_added"
    COMPONENT_REMOVED = "component_removed"
    COMPONENT_REORDERED = "component_reordered"
    COMPONENT_TYPE_CHANGED = "component_type_changed"
    ROOT_UPDATE = "root_update"
    PAGE_ADDED = "page_added"
    PAGE_REMOVED = "page_removed"
    UNSUPPORTED_COMPONENT = "unsupported_component"
    INVALID_PROPERTY = "invalid_property"
    NEW_INTERACTIVE_BEHAVIOR = "new_interactive_behavior"
    NEW_COMPONENT = "new_component"
    API_CONTRACT_CHANGE = "api_contract_change"
    DATABASE_SCHEMA_CHANGE = "database_schema_change"
    SOURCE_OUTSIDE_GENERATED_AREA = "source_outside_generated_area"


RUNTIME_SAFE_KINDS = frozenset(
    {
        ChangeKind.CONTENT_UPDATE,
        ChangeKind.STYLE_UPDATE,
        ChangeKind.LAYOUT_UPDATE,
        ChangeKind.COMPONENT_ADDED,
        ChangeKind.COMPONENT_REMOVED,
        ChangeKind.COMPONENT_REORDERED,
        ChangeKind.COMPONENT_TYPE_CHANGED,
        ChangeKind.ROOT_UPDATE,
        ChangeKind.PAGE_ADDED,
        ChangeKind.PAGE_REMOVED,
    }
)
SOURCE_REQUIRED_KINDS = frozenset(
    {
        ChangeKind.UNSUPPORTED_COMPONENT,
        ChangeKind.NEW_INTERACTIVE_BEHAVIOR,
        ChangeKind.NEW_COMPONENT,
        ChangeKind.API_CONTRACT_CHANGE,
        ChangeKind.DATABASE_SCHEMA_CHANGE,
        ChangeKind.SOURCE_OUTSIDE_GENERATED_AREA,
    }
)


class ChangeItem(BaseModel):
    kind: ChangeKind
    page: str | None = None
    component_id: str | None = None
    component_type: str | None = None
    prop_path: list[str | int] | None = None
    before: Any = None
    after: Any = None
    detail: str = ""


class Scope(StrEnum):
    CONFIG = "config"
    FRONTEND = "frontend"
    BACKEND = "backend"
    DATABASE = "database"


class ApprovalReason(StrEnum):
    DATABASE_SCHEMA = "database_schema"
    DESTRUCTIVE_MIGRATION = "destructive_migration"
    AUTHENTICATION = "authentication"
    PAYMENTS = "payments"
    TENANT_ISOLATION = "tenant_isolation"
    SHARED_INFRASTRUCTURE = "shared_infrastructure"
    NEW_DEPENDENCY = "new_dependency"
    MODIFIES_USER_CODE = "modifies_user_code"
    DELETES_FILES = "deletes_files"
    LARGE_REMOVAL = "large_removal"


class ClarificationQuestion(BaseModel):
    question: str
    options: list[str] = []


class PlannedFile(BaseModel):
    path: str
    action: Literal["create", "modify", "delete"]
    reason: str = ""


class SourcePlan(BaseModel):
    """Structured implementation plan for Path B, produced before any code is generated."""

    summary: str
    scopes: list[Scope]
    files_to_read: list[str] = []
    files_to_change: list[PlannedFile] = []
    new_components: list[str] = []
    api_endpoints: list[ApiEndpointProposal] = []
    migrations: list[MigrationProposal] = []
    dependencies: list[DependencyProposal] = []
    integration_tests: list[str] = []
    risks: list[str] = []
    questions: list[str] = []


class ChangePlan(BaseModel):
    """The explainable output of the Change Analyzer."""

    summary: str
    path: ExecutionPath
    rationale: list[str]
    changes: list[ChangeItem] = []
    affected_pages: list[str] = []
    affected_components: list[str] = []
    scopes: list[Scope] = []
    operations: list[PuckOp] = []  # Path A: the targeted patch to apply
    expected_files: list[str] = []  # Path B: files expected to change
    requires_approval: bool = False
    approval_reasons: list[ApprovalReason] = []
    required_validations: list[str] = []
    clarifications: list[ClarificationQuestion] = []
    security_flags: list[str] = []
    assumptions: list[str] = []
    decided_by: Literal["rules", "rules+llm"] = "rules"
    llm_overridden: list[str] = []  # where deterministic rules overrode an LLM suggestion
    capability_request: str | None = None  # Path B: the capability the registry lacks
    source_plan: SourcePlan | None = None
    plan_hash: str = ""


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    code: str
    message: str
    severity: Severity = Severity.ERROR
    location: str | None = None


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"
    SKIPPED = "skipped"


class CheckResult(BaseModel):
    name: str
    status: CheckStatus
    issues: list[ValidationIssue] = []
    details: dict[str, Any] = {}


class ValidationReport(BaseModel):
    checks: list[CheckResult] = []

    @property
    def passed(self) -> bool:
        return all(c.status in (CheckStatus.PASSED, CheckStatus.SKIPPED) for c in self.checks)

    @property
    def issues(self) -> list[ValidationIssue]:
        return [i for c in self.checks for i in c.issues]

    def add(self, check: CheckResult) -> None:
        self.checks.append(check)


# --------------------------------------------------------------------------
# Source-code path (Path B)
# --------------------------------------------------------------------------


class FileAction(StrEnum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


class FileChange(BaseModel):
    path: str
    action: FileAction
    content: str | None = None  # full new content for create/modify
    purpose: str = ""
    base_sha: str | None = None  # blob SHA the change was generated against (modify/delete)


class FieldSpec(BaseModel):
    """Schema of one component property in the trusted registry."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["text", "textarea", "number", "boolean", "url", "image", "color", "select", "array", "object", "slot"]
    label: str | None = None
    required: bool = False
    category: Literal["content", "style", "layout", "behavior"] = "content"
    semantic: str | None = None  # e.g. "price", "title", "currency"
    options: list[Any] | None = None  # select
    min: float | None = None
    max: float | None = None
    max_length: int | None = None
    item_fields: dict[str, FieldSpec] | None = None  # array / object


class ComponentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    label: str
    description: str = ""
    aliases: list[str] = []
    capabilities: list[str] = []
    fields: dict[str, FieldSpec]
    default_props: dict[str, Any] = {}
    allowed_in_client_pages: bool = True
    source_path: str | None = None
    export_name: str | None = None
    version: int = 1


class NewComponentProposal(BaseModel):
    spec: ComponentSpec
    source_file: str


class ApiEndpointProposal(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    purpose: str
    auth: str
    request_schema: dict[str, Any] = {}
    response_schema: dict[str, Any] = {}
    reuses_existing: bool = False


class MigrationProposal(BaseModel):
    path: str
    description: str
    destructive: bool = False


class DependencyProposal(BaseModel):
    name: str
    version: str
    reason: str


class CodeChangeProposal(BaseModel):
    summary: str
    scopes: list[Scope]
    files: list[FileChange]
    new_components: list[NewComponentProposal] = []
    api_endpoints: list[ApiEndpointProposal] = []
    migrations: list[MigrationProposal] = []
    dependencies: list[DependencyProposal] = []
    tests: list[str] = []
    page_operations: list[PuckOp] = []  # place new components on pages once deployed
    notes: list[str] = []


# --------------------------------------------------------------------------
# Deployment
# --------------------------------------------------------------------------


class DeploymentStatus(StrEnum):
    PENDING = "pending"
    BUILDING = "building"
    DEPLOYING = "deploying"
    SUCCEEDED = "succeeded"  # provider reported success, not yet verified
    VERIFIED = "verified"  # deployed version confirmed live
    FAILED = "failed"
    SUPERSEDED = "superseded"


class HarnessError(BaseModel):
    """Structured, actionable failure record stored on a job."""

    code: str
    message: str
    stage: JobState
    retryable: bool = False
    details: dict[str, Any] = {}
    hint: str | None = None


SourcePlan.model_rebuild()
ChangePlan.model_rebuild()
