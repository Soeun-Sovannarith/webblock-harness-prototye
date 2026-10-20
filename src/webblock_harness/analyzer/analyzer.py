"""The Change Analyzer.

Compares (1) the proposed configuration or request, (2) the last successfully
deployed configuration, (3) the component registry, (4) the repository's source
files and history and (5) the requested outcome — and produces an explainable,
hashed ``ChangePlan``. The classification is always made by deterministic
rules over the *structural* diff of the candidate configuration; the LLM is only
consulted to understand ambiguous intent and its proposals are re-validated.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from webblock_harness.analyzer.classifier import classify
from webblock_harness.analyzer.intent import IntentSignals, detect_intent
from webblock_harness.analyzer.llm import Planner, PlannerError
from webblock_harness.analyzer.resolver import resolve_request
from webblock_harness.domain import (
    ApprovalReason,
    ChangeItem,
    ChangeKind,
    ChangePlan,
    ClarificationQuestion,
    ExecutionPath,
    MoveComponentOp,
    PuckOp,
    Scope,
    SourcePlan,
)
from webblock_harness.integrations.git import CommitInfo
from webblock_harness.puck.diff import diff_sites
from webblock_harness.puck.normalize import canonical_json, normalize_site
from webblock_harness.puck.ops import OpConflict, apply_ops
from webblock_harness.puck.validate import validate_site
from webblock_harness.registry.registry import ComponentRegistry
from webblock_harness.security.paths import PathPolicyError, in_generated_area, normalize_repo_path
from webblock_harness.security.untrusted import BLOCKING_FLAGS, scan_untrusted_text

MIN_LLM_CONFIDENCE = 0.6


@dataclass
class SourceContext:
    head_sha: str | None
    files: list[str] = field(default_factory=list)
    history: Callable[[str], list[CommitInfo]] | None = None
    harness_author_email: str = ""

    def user_authored(self, paths: list[str]) -> set[str]:
        """Files whose recent history contains commits not made by the harness."""
        if self.history is None:
            return set()
        out: set[str] = set()
        for path in paths:
            if path not in self.files:
                continue
            commits = self.history(path)
            if any(c.author_email != self.harness_author_email for c in commits):
                out.add(path)
        return out


@dataclass
class AnalysisInput:
    base_config: dict[str, Any]
    registry: ComponentRegistry
    request_text: str = ""
    proposed_config: dict[str, Any] | None = None
    explicit_ops: list[PuckOp] | None = None
    answers: list[str] = field(default_factory=list)
    source: SourceContext | None = None
    base_label: str = "last deployed snapshot"
    auto_approve: set[ApprovalReason] = field(default_factory=set)
    required_checks: Callable[[set[str]], list[str]] | None = None


def plan_hash(plan: ChangePlan) -> str:
    payload = plan.model_dump(mode="json", exclude={"plan_hash"})
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _summarize(changes: list[ChangeItem]) -> str:
    if not changes:
        return "No changes."
    parts: list[str] = []
    for c in changes[:6]:
        target = c.component_id or c.page or ""
        if c.prop_path:
            parts.append(f"{c.kind.value} {target}.{'.'.join(map(str, c.prop_path))}: {c.before!r} → {c.after!r}")
        else:
            parts.append(" ".join(x for x in (c.kind.value, target, c.detail) if x))
    more = f" (+{len(changes) - 6} more)" if len(changes) > 6 else ""
    return "; ".join(parts) + more


class ChangeAnalyzer:
    def __init__(self, planner: Planner) -> None:
        self.planner = planner

    def analyze(self, inp: AnalysisInput) -> ChangePlan:
        text = inp.request_text.strip()
        flags = scan_untrusted_text(" ".join([text, *inp.answers]))
        if BLOCKING_FLAGS & set(flags):
            return self._finish(
                ChangePlan(
                    summary="Request rejected by security policy.",
                    path=ExecutionPath.REJECTED,
                    rationale=[
                        "The request asks for access to credentials, other tenants or command execution, which the harness never performs."
                    ],
                    security_flags=flags,
                )
            )

        intent = detect_intent(" ".join([text, *inp.answers])) if text else None
        ops: list[PuckOp] = []
        assumptions: list[str] = []
        clarifications: list[ClarificationQuestion] = []
        llm_used = False
        llm_path: str | None = None
        capability: str | None = None
        origin = "request"

        if inp.explicit_ops is not None:
            ops, origin = list(inp.explicit_ops), "explicit operations"
        elif inp.proposed_config is not None:
            proposed = normalize_site(inp.proposed_config)
            ops, origin = diff_sites(inp.base_config, proposed, inp.registry).ops, "editor submission"
        elif text:
            assert intent is not None
            if intent.destructive and not inp.answers:
                clarifications.append(
                    ClarificationQuestion(
                        question="This sounds like it would remove or rebuild a large part of the site. Please confirm exactly what should be removed or replaced.",
                    )
                )
            elif intent.capability_request:
                capability = text
            else:
                res = resolve_request(text, inp.base_config, inp.registry)
                if res.matched:
                    ops, assumptions, clarifications = res.ops, res.assumptions, res.clarifications
                    assumptions.insert(0, f"Resolved by deterministic rule '{res.rule}'.")
                elif self.planner.available:
                    llm_used = True
                    try:
                        sugg = self.planner.suggest_config_change(text, inp.base_config, inp.registry.summary(), inp.answers)
                    except PlannerError as exc:
                        if exc.retryable:
                            raise
                        clarifications.append(
                            ClarificationQuestion(
                                question=f"I could not interpret the request automatically ({exc.code}). Could you rephrase it more specifically?"
                            )
                        )
                    else:
                        llm_path = sugg.path
                        if sugg.path == "runtime_config":
                            if sugg.confidence < MIN_LLM_CONFIDENCE:
                                clarifications.append(
                                    ClarificationQuestion(
                                        question=f"I think you mean: {sugg.reasoning or 'an edit to existing sections'}. Is that right?",
                                        options=["Yes", "No — let me rephrase"],
                                    )
                                )
                            ops = sugg.operations
                            assumptions.append(f"Interpreted with LLM assistance: {sugg.reasoning}")
                        elif sugg.path == "source_code":
                            capability = sugg.capability_needed or text
                            # Deterministic check: does the registry already provide it?
                            existing = inp.registry.find_by_reference(capability) if sugg.capability_needed else []
                            if existing and not intent.capability_request:
                                clarifications.append(
                                    ClarificationQuestion(
                                        question=f"The site already has {', '.join(s.label for s in existing)}. Should I use it instead of building something new?",
                                        options=["Use the existing component", "Build something new"],
                                    )
                                )
                                capability = None
                        else:
                            clarifications += [ClarificationQuestion(question=q) for q in sugg.questions] or [
                                ClarificationQuestion(question="Could you describe the change in more detail?")
                            ]
                else:
                    clarifications.append(
                        ClarificationQuestion(
                            question="I could not map this request to a specific section or setting. Which section should change, and how?"
                        )
                    )

        if clarifications:
            return self._finish(
                ChangePlan(
                    summary="Clarification needed before any change is made.",
                    path=ExecutionPath.CLARIFICATION,
                    rationale=["The request is ambiguous; nothing is generated or applied until it is clarified."],
                    clarifications=clarifications,
                    assumptions=assumptions,
                    security_flags=flags,
                    decided_by="rules+llm" if llm_used else "rules",
                    operations=ops,
                )
            )

        # Apply candidate operations to the base and diff *structurally* -------
        try:
            candidate = apply_ops(inp.base_config, ops, inp.registry)
        except OpConflict as exc:
            return self._finish(
                ChangePlan(
                    summary="The requested edit does not apply to the current configuration.",
                    path=ExecutionPath.REJECTED,
                    rationale=[f"Operation {exc.op_index} ({origin}) failed: {exc.message}"],
                    security_flags=flags,
                    decided_by="rules+llm" if llm_used else "rules",
                )
            )
        moved = frozenset(op.component_id for op in ops if isinstance(op, MoveComponentOp))
        diff = diff_sites(inp.base_config, candidate, inp.registry, moved_hint=moved)
        changes = list(diff.changes)
        validation = validate_site(candidate, inp.registry)

        # Registry ↔ source consistency: a component the client repo cannot render is not runtime-safe.
        if inp.source and inp.source.files:
            files = set(inp.source.files)
            for c in changes:
                if c.kind in (ChangeKind.COMPONENT_ADDED, ChangeKind.COMPONENT_TYPE_CHANGED) and c.component_type:
                    spec = inp.registry.get(c.component_type)
                    if spec and spec.source_path and spec.source_path not in files:
                        changes.append(
                            ChangeItem(
                                kind=ChangeKind.UNSUPPORTED_COMPONENT,
                                page=c.page,
                                component_id=c.component_id,
                                component_type=c.component_type,
                                detail=f"{c.component_type} is registered but {spec.source_path} is missing from the repository",
                            )
                        )

        if capability:
            changes += self._capability_changes(capability, intent)

        decision = classify(
            changes,
            intent,
            validation,
            capability_requested=capability is not None,
            llm_path=llm_path,
            auto_approve=inp.auto_approve,
        )

        source_plan: SourcePlan | None = None
        expected_files: list[str] = []
        if decision.path == ExecutionPath.SOURCE_CODE:
            capability = capability or "; ".join(
                sorted(
                    {
                        c.detail or c.kind.value
                        for c in changes
                        if c.kind in (ChangeKind.UNSUPPORTED_COMPONENT, ChangeKind.INVALID_PROPERTY)
                    }
                )
            )
            if self.planner.available:
                llm_used = True
                repo_files = inp.source.files if inp.source else []
                source_plan = self.planner.plan_source_change(
                    self._source_request(text, capability, changes), inp.registry.summary(), repo_files, inp.answers
                )
                if source_plan.questions:
                    return self._finish(
                        ChangePlan(
                            summary="Clarification needed before planning code changes.",
                            path=ExecutionPath.CLARIFICATION,
                            rationale=["The implementation plan has open questions."],
                            clarifications=[ClarificationQuestion(question=q) for q in source_plan.questions],
                            changes=changes,
                            security_flags=flags,
                            decided_by="rules+llm",
                            capability_request=capability,
                        )
                    )
                expected_files = []
                for f in source_plan.files_to_change:
                    try:
                        expected_files.append(normalize_repo_path(f.path))
                    except PathPolicyError:
                        flags.append(f"invalid_planned_path:{f.path[:80]}")
                user_authored = (
                    inp.source.user_authored([p for p in expected_files if not in_generated_area(p)]) if inp.source else set()
                )
                decision = classify(
                    changes,
                    intent,
                    validation,
                    capability_requested=True,
                    llm_path=llm_path,
                    source_plan=source_plan,
                    user_authored_files=user_authored,
                    auto_approve=inp.auto_approve,
                    required_checks=inp.required_checks({s.value for s in (source_plan.scopes or [])} | {"frontend"})
                    if inp.required_checks
                    else None,
                )
                if user_authored:
                    assumptions.append(f"Files with manual edits will be modified: {', '.join(sorted(user_authored))}.")
            else:
                assumptions.append("No LLM is configured: the plan is recorded, but code generation will fail until one is.")
                if inp.required_checks:
                    decision = classify(
                        changes,
                        intent,
                        validation,
                        capability_requested=True,
                        llm_path=llm_path,
                        auto_approve=inp.auto_approve,
                        required_checks=inp.required_checks({s.value for s in decision.scopes}),
                    )

        affected_components = sorted({c.component_id for c in changes if c.component_id})
        plan = ChangePlan(
            summary=(source_plan.summary if source_plan else _summarize(changes))
            if decision.path != ExecutionPath.NOOP
            else "No changes: the requested state is already live.",
            path=decision.path,
            rationale=[f"Compared against the {inp.base_label} ({origin}).", *decision.rationale],
            changes=changes,
            affected_pages=sorted({c.page for c in changes if c.page}),
            affected_components=affected_components,
            scopes=decision.scopes,
            operations=ops if decision.path in (ExecutionPath.RUNTIME_CONFIG, ExecutionPath.SOURCE_CODE) else [],
            expected_files=expected_files
            if decision.path == ExecutionPath.SOURCE_CODE
            else ([] if decision.path != ExecutionPath.RUNTIME_CONFIG else ["(runtime configuration only — no source files)"]),
            requires_approval=bool(decision.approval_reasons),
            approval_reasons=decision.approval_reasons,
            required_validations=decision.required_validations,
            security_flags=flags,
            assumptions=assumptions,
            decided_by="rules+llm" if llm_used else "rules",
            llm_overridden=decision.overrides,
            capability_request=capability if decision.path == ExecutionPath.SOURCE_CODE else None,
            source_plan=source_plan,
        )
        if decision.path == ExecutionPath.SOURCE_CODE and Scope.DATABASE in plan.scopes:
            plan.rationale.append("Database changes are planned separately from frontend code and require approval.")
        return self._finish(plan)

    @staticmethod
    def _capability_changes(capability: str, intent: IntentSignals | None) -> list[ChangeItem]:
        items = [ChangeItem(kind=ChangeKind.NEW_COMPONENT, detail=f"Requested capability: {capability[:200]}")]
        if intent is None:
            return items
        if intent.interactive:
            items.append(
                ChangeItem(
                    kind=ChangeKind.NEW_INTERACTIVE_BEHAVIOR,
                    detail=", ".join(t for t in intent.matched_terms if t.startswith("interactive")),
                )
            )
        if intent.backend:
            items.append(
                ChangeItem(
                    kind=ChangeKind.API_CONTRACT_CHANGE,
                    detail=", ".join(t for t in intent.matched_terms if t.startswith("backend")) or "server-side behaviour",
                )
            )
        if intent.database:
            items.append(
                ChangeItem(
                    kind=ChangeKind.DATABASE_SCHEMA_CHANGE,
                    detail=", ".join(t for t in intent.matched_terms if t.startswith("database")),
                )
            )
        return items

    @staticmethod
    def _source_request(text: str, capability: str | None, changes: list[ChangeItem]) -> str:
        if text:
            return text
        missing = [f"{c.component_type}: {c.detail}" for c in changes if c.kind == ChangeKind.UNSUPPORTED_COMPONENT]
        return "Implement the components used in the editor that the registry does not support: " + "; ".join(
            missing or [capability or ""]
        )

    @staticmethod
    def _finish(plan: ChangePlan) -> ChangePlan:
        plan.plan_hash = plan_hash(plan)
        return plan
