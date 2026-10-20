"""Deterministic change classification and execution-path selection.

This is the final authority on which path a change takes. LLM suggestions are
inputs; where they disagree with these rules the rules win and the override is
recorded on the plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from webblock_harness.analyzer.intent import IntentSignals
from webblock_harness.domain import (
    SOURCE_REQUIRED_KINDS,
    ApprovalReason,
    ChangeItem,
    ChangeKind,
    CheckResult,
    CheckStatus,
    ExecutionPath,
    FileAction,
    Scope,
    SourcePlan,
)
from webblock_harness.security.paths import PathPolicyError, high_impact_reason, normalize_repo_path

LARGE_REMOVAL_THRESHOLD = 3


@dataclass
class Decision:
    path: ExecutionPath
    rationale: list[str] = field(default_factory=list)
    scopes: list[Scope] = field(default_factory=list)
    approval_reasons: list[ApprovalReason] = field(default_factory=list)
    required_validations: list[str] = field(default_factory=list)
    overrides: list[str] = field(default_factory=list)


def _scopes_for_source(changes: list[ChangeItem], intent: IntentSignals | None, plan: SourcePlan | None) -> list[Scope]:
    scopes = {Scope.FRONTEND}
    kinds = {c.kind for c in changes}
    if ChangeKind.API_CONTRACT_CHANGE in kinds or (intent and intent.backend):
        scopes.add(Scope.BACKEND)
    if ChangeKind.DATABASE_SCHEMA_CHANGE in kinds or (intent and intent.database):
        scopes |= {Scope.BACKEND, Scope.DATABASE}
    if plan:
        scopes |= {s for s in plan.scopes if s != Scope.CONFIG}
        if plan.migrations:
            scopes |= {Scope.BACKEND, Scope.DATABASE}
        if plan.api_endpoints:
            scopes.add(Scope.BACKEND)
    order = [Scope.FRONTEND, Scope.BACKEND, Scope.DATABASE]
    return [s for s in order if s in scopes]


def approvals_for_source_plan(plan: SourcePlan, user_authored: set[str]) -> set[ApprovalReason]:
    reasons: set[ApprovalReason] = set()
    if plan.migrations:
        reasons.add(ApprovalReason.DATABASE_SCHEMA)
    if any(m.destructive for m in plan.migrations):
        reasons.add(ApprovalReason.DESTRUCTIVE_MIGRATION)
    if plan.dependencies:
        reasons.add(ApprovalReason.NEW_DEPENDENCY)
    for f in plan.files_to_change:
        try:
            path = normalize_repo_path(f.path)
        except PathPolicyError:
            continue  # rejected later by the patch validator
        reason = high_impact_reason(path)
        if reason:
            reasons.add(ApprovalReason(reason))
        if f.action == FileAction.DELETE.value:
            reasons.add(ApprovalReason.DELETES_FILES)
        if f.action != FileAction.CREATE.value and path in user_authored:
            reasons.add(ApprovalReason.MODIFIES_USER_CODE)
    return reasons


def classify(
    changes: list[ChangeItem],
    intent: IntentSignals | None,
    config_validation: CheckResult | None,
    *,
    capability_requested: bool,
    llm_path: str | None = None,
    source_plan: SourcePlan | None = None,
    user_authored_files: set[str] | None = None,
    auto_approve: set[ApprovalReason] | None = None,
    required_checks: list[str] | None = None,
) -> Decision:
    kinds = {c.kind for c in changes}
    source_kinds = sorted(k.value for k in kinds & SOURCE_REQUIRED_KINDS)
    invalid_props = [c for c in changes if c.kind == ChangeKind.INVALID_PROPERTY]
    schema_issues = (
        [i for i in config_validation.issues if i.code not in ("unsupported_component", "component_not_allowed")]
        if config_validation and config_validation.status == CheckStatus.FAILED
        else []
    )

    if source_kinds or capability_requested:
        d = Decision(path=ExecutionPath.SOURCE_CODE)
        if source_kinds:
            d.rationale.append(
                f"The change needs capabilities the component registry cannot represent: {', '.join(source_kinds)}."
            )
        if capability_requested and not source_kinds:
            d.rationale.append("The request asks for new behaviour rather than new content.")
        d.rationale.append("Path B: a minimal, validated source patch on an isolated branch; no full regeneration.")
        d.scopes = _scopes_for_source(changes, intent, source_plan)
        reasons: set[ApprovalReason] = set(intent.approval_reasons) if intent else set()
        if Scope.DATABASE in d.scopes:
            reasons.add(ApprovalReason.DATABASE_SCHEMA)
        if source_plan:
            reasons |= approvals_for_source_plan(source_plan, user_authored_files or set())
        d.approval_reasons = sorted(reasons - (auto_approve or set()), key=lambda r: r.value)
        d.required_validations = [
            "path_policy",
            "protected_files",
            "static_security_scan",
            "component_registry_schema",
            "plan_conformance",
            "diff_review",
            *[f"ci:{c}" for c in (required_checks or [])],
            "deployment_verification",
        ]
        if invalid_props:
            d.rationale.append("Properties outside the registered schema will be added by extending the component in code.")
        if llm_path and llm_path != ExecutionPath.SOURCE_CODE.value:
            d.overrides.append(
                f"LLM suggested {llm_path}; rules require source_code ({', '.join(source_kinds) or 'capability request'})."
            )
        return d

    if invalid_props or schema_issues:
        d = Decision(path=ExecutionPath.REJECTED)
        d.rationale.append("The proposed configuration is invalid for the registered components; nothing will be applied.")
        d.rationale += [f"{c.component_type}.{'.'.join(map(str, c.prop_path or []))}: {c.detail}" for c in invalid_props][:10]
        d.rationale += [f"{i.location}: {i.message}" for i in schema_issues][:10]
        return d

    if not changes:
        d = Decision(path=ExecutionPath.NOOP, rationale=["The requested state already matches the current published state."])
        if llm_path and llm_path not in (ExecutionPath.RUNTIME_CONFIG.value,):
            d.overrides.append(f"LLM suggested {llm_path}; the structural diff is empty.")
        return d

    d = Decision(path=ExecutionPath.RUNTIME_CONFIG, scopes=[Scope.CONFIG])
    d.rationale.append(
        "Every change is a supported content, style, layout or structure update of registered components (Path A)."
    )
    d.rationale.append("Applied as a targeted JSON patch; no source code is generated.")
    removed = [c for c in changes if c.kind == ChangeKind.COMPONENT_REMOVED]
    reasons = set()
    if any(c.kind == ChangeKind.PAGE_REMOVED for c in changes) or len(removed) > LARGE_REMOVAL_THRESHOLD:
        reasons.add(ApprovalReason.LARGE_REMOVAL)
    d.approval_reasons = sorted(reasons - (auto_approve or set()), key=lambda r: r.value)
    d.required_validations = ["ops_apply_cleanly", "puck_schema", "registry_conformance", "deployment_verification"]
    if llm_path and llm_path != ExecutionPath.RUNTIME_CONFIG.value:
        d.overrides.append(f"LLM suggested {llm_path}; the structural diff contains only runtime-safe changes.")
    return d
