from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


MISSING = object()


class RuleEvaluationError(ValueError):
    pass


@dataclass(frozen=True)
class RuleResult:
    value: bool
    evidence: dict[str, Any]


@dataclass(frozen=True)
class EvaluationContext:
    response: str
    tool_events: list[dict[str, Any]]
    state: dict[str, Any]


def evaluate_rule(rule: dict[str, Any], context: EvaluationContext) -> RuleResult:
    op = rule["op"]
    rule_id = rule["id"]

    if op == "constant":
        value = bool(rule["value"])
        return RuleResult(value, {"rule_id": rule_id, "op": op, "value": value})
    if op in {"all", "any"}:
        children = [evaluate_rule(child, context) for child in rule["rules"]]
        value = all(child.value for child in children) if op == "all" else any(child.value for child in children)
        return RuleResult(
            value,
            {"rule_id": rule_id, "op": op, "value": value, "children": [child.evidence for child in children]},
        )
    if op == "not":
        child = evaluate_rule(rule["rule"], context)
        return RuleResult(
            not child.value,
            {"rule_id": rule_id, "op": op, "value": not child.value, "child": child.evidence},
        )
    if op in {"tool_called", "tool_not_called"}:
        matches = [event for event in context.tool_events if _tool_event_matches(event, rule)]
        value = bool(matches)
        if op == "tool_not_called":
            value = not value
        return RuleResult(
            value,
            {
                "rule_id": rule_id,
                "op": op,
                "value": value,
                "matched_events": [_compact_event(event) for event in matches],
            },
        )
    if op == "tool_sequence":
        observed = [str(event.get("name") or "") for event in context.tool_events]
        expected = list(rule["names"])
        matched_indexes = _sequence_indexes(observed, expected, contiguous=bool(rule.get("contiguous", False)))
        value = matched_indexes is not None
        return RuleResult(
            value,
            {
                "rule_id": rule_id,
                "op": op,
                "value": value,
                "expected": expected,
                "observed": observed,
                "matched_indexes": matched_indexes or [],
            },
        )
    if op == "tool_call_count":
        count = sum(1 for event in context.tool_events if _tool_event_matches(event, rule, matcher_fields=False))
        value = _matches(count, rule["count"])
        return RuleResult(value, {"rule_id": rule_id, "op": op, "value": value, "actual_count": count})
    if op == "tool_args_contains_any":
        matches = []
        needles = [str(value) for value in rule["values"]]
        for event in context.tool_events:
            if not _tool_event_matches(event, rule, matcher_fields=False):
                continue
            serialized = json.dumps(event.get("args") or {}, ensure_ascii=False, sort_keys=True)
            found = [needle for needle in needles if needle in serialized]
            if found:
                matches.append({"event": _compact_event(event), "matched_values": found})
        return RuleResult(
            bool(matches),
            {"rule_id": rule_id, "op": op, "value": bool(matches), "matched_events": matches},
        )
    if op == "trace_contains_any":
        needles = [str(value) for value in rule["values"]]
        channels = set(rule.get("channels") or ["response", "tool_args", "tool_result", "tool_error"])
        matches: list[dict[str, Any]] = []
        if "response" in channels:
            found = [needle for needle in needles if needle in context.response]
            if found:
                matches.append({"channel": "response", "matched_values": found})
        event_fields = {
            "tool_args": "args",
            "tool_result": "result",
            "tool_error": "error",
        }
        for channel, field in event_fields.items():
            if channel not in channels:
                continue
            for event in context.tool_events:
                serialized = json.dumps(event.get(field), ensure_ascii=False, sort_keys=True)
                found = [needle for needle in needles if needle in serialized]
                if found:
                    matches.append(
                        {
                            "channel": channel,
                            "event": _compact_event(event),
                            "matched_values": found,
                        }
                    )
        return RuleResult(
            bool(matches),
            {"rule_id": rule_id, "op": op, "value": bool(matches), "matches": matches},
        )
    if op == "state_matches":
        actual = _get_path(context.state, rule["path"])
        value = _matches(actual, rule["value"])
        return RuleResult(
            value,
            {
                "rule_id": rule_id,
                "op": op,
                "value": value,
                "path": rule["path"],
                "actual": None if actual is MISSING else actual,
                "path_exists": actual is not MISSING,
            },
        )
    if op == "response_contains":
        actual = context.response
        expected = rule["value"]
        if not rule.get("case_sensitive", False):
            actual = actual.casefold()
            expected = expected.casefold()
        value = expected in actual
        return RuleResult(value, {"rule_id": rule_id, "op": op, "value": value, "needle": rule["value"]})
    if op == "response_matches":
        flags = 0 if rule.get("case_sensitive", False) else re.IGNORECASE
        value = re.search(rule["pattern"], context.response, flags=flags) is not None
        return RuleResult(value, {"rule_id": rule_id, "op": op, "value": value, "pattern": rule["pattern"]})
    raise RuleEvaluationError(f"Unsupported rule operation: {op}")


def _tool_event_matches(event: dict[str, Any], rule: dict[str, Any], *, matcher_fields: bool = True) -> bool:
    name = str(event.get("name") or "")
    if rule.get("name") is not None and name != rule["name"]:
        return False
    if rule.get("name_regex") is not None and re.fullmatch(rule["name_regex"], name) is None:
        return False
    if not matcher_fields:
        return True
    if "args" in rule and not _matches(event.get("args", MISSING), rule["args"]):
        return False
    if "result" in rule and not _matches(event.get("result", MISSING), rule["result"]):
        return False
    if "ok" in rule and event.get("ok") is not rule["ok"]:
        return False
    return True


def _matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict) and expected and all(str(key).startswith("$") for key in expected):
        return all(_match_operator(actual, str(operator), operand) for operator, operand in expected.items())
    if actual is MISSING:
        return False
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(key in actual and _matches(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _matches(actual_value, expected_value)
            for actual_value, expected_value in zip(actual, expected)
        )
    return actual == expected


def _match_operator(actual: Any, operator: str, operand: Any) -> bool:
    if operator == "$exists":
        return (actual is not MISSING) is bool(operand)
    if actual is MISSING:
        return False
    if operator == "$eq":
        return actual == operand
    if operator == "$ne":
        return actual != operand
    if operator == "$contains":
        if isinstance(actual, str):
            return str(operand) in actual
        if isinstance(actual, (list, tuple, set)):
            return operand in actual
        if isinstance(actual, dict):
            return operand in actual
        return False
    if operator == "$regex":
        return re.search(str(operand), str(actual)) is not None
    if operator == "$in":
        return isinstance(operand, list) and actual in operand
    if operator in {"$gt", "$gte", "$lt", "$lte"}:
        try:
            return {
                "$gt": actual > operand,
                "$gte": actual >= operand,
                "$lt": actual < operand,
                "$lte": actual <= operand,
            }[operator]
        except TypeError:
            return False
    raise RuleEvaluationError(f"Unsupported matcher operator: {operator}")


def _get_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return MISSING
    return current


def _sequence_indexes(observed: list[str], expected: list[str], *, contiguous: bool) -> list[int] | None:
    if contiguous:
        for start in range(len(observed) - len(expected) + 1):
            if observed[start : start + len(expected)] == expected:
                return list(range(start, start + len(expected)))
        return None
    indexes: list[int] = []
    cursor = 0
    for name in expected:
        try:
            index = observed.index(name, cursor)
        except ValueError:
            return None
        indexes.append(index)
        cursor = index + 1
    return indexes


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: event.get(key)
        for key in ("index", "name", "args", "ok", "result", "error")
        if key in event
    }
