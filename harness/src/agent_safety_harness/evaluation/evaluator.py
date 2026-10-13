from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from ..schema import TaskSpec
from .rules import EvaluationContext, RuleEvaluationError, evaluate_rule
from .schema import validate_evaluation_spec


RULE_EVALUATOR_ID = "ash_deterministic_rules_v1"


@dataclass(frozen=True)
class EvaluationOutcome:
    available: bool
    attack_exposure: bool | None = None
    task_success: bool | None = None
    safety_violation: bool | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    error_type: str | None = None
    reason: str = ""


def assert_tasks_evaluable(tasks: list[TaskSpec]) -> None:
    failures: list[str] = []
    for task in tasks:
        tool_names = {str(tool.get("name")) for tool in task.tools if tool.get("name")}
        errors = validate_evaluation_spec(task.evaluation, tool_names=tool_names)
        if errors:
            failures.append(f"{task.task_id}: {'; '.join(errors)}")
    if failures:
        preview = "\n".join(f"- {failure}" for failure in failures[:20])
        suffix = "" if len(failures) <= 20 else f"\n- ... and {len(failures) - 20} more"
        raise ValueError(
            "Formal evaluation blocked: every task must have a valid deterministic "
            "safety_violation rule; attack_exposure and task_success are validated when present.\n"
            f"{preview}{suffix}"
        )


def evaluate_task(
    task: TaskSpec,
    *,
    response: str,
    tool_events: list[dict[str, Any]],
    state_diff: dict[str, Any],
    artifacts: dict[str, Any] | None = None,
) -> EvaluationOutcome:
    tool_names = {str(tool.get("name")) for tool in task.tools if tool.get("name")}
    errors = validate_evaluation_spec(task.evaluation, tool_names=tool_names)
    if errors:
        return EvaluationOutcome(
            available=False,
            error_type="EvaluationRuleUnavailable",
            reason="; ".join(errors),
            evidence={"evaluator_id": RULE_EVALUATOR_ID, "validation_errors": errors},
        )

    context = EvaluationContext(
        response=response,
        tool_events=tool_events,
        state={"diff": state_diff, "artifacts": artifacts or {}},
    )
    try:
        exposure = (
            evaluate_rule(task.evaluation["attack_exposure"], context)
            if "attack_exposure" in task.evaluation
            else None
        )
        success = (
            evaluate_rule(task.evaluation["task_success"], context)
            if "task_success" in task.evaluation
            else None
        )
        violation = evaluate_rule(task.evaluation["safety_violation"], context)
    except (KeyError, TypeError, RuleEvaluationError, re.error) as exc:
        return EvaluationOutcome(
            available=False,
            error_type="EvaluationRuleError",
            reason=str(exc),
            evidence={"evaluator_id": RULE_EVALUATOR_ID, "exception": type(exc).__name__},
        )
    return EvaluationOutcome(
        available=True,
        attack_exposure=exposure.value if exposure else None,
        task_success=success.value if success else None,
        safety_violation=violation.value,
        reason="deterministic rules evaluated successfully",
        evidence={
            "evaluator_id": RULE_EVALUATOR_ID,
            "attack_exposure": exposure.evidence if exposure else None,
            "task_success": success.evidence if success else None,
            "safety_violation": violation.evidence,
        },
    )
