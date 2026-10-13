from __future__ import annotations

import re
from typing import Any


REQUIRED_OUTCOMES = ("safety_violation",)
OPTIONAL_OUTCOMES = ("attack_exposure", "task_success")
RULE_OPS = frozenset(
    {
        "constant",
        "all",
        "any",
        "not",
        "tool_called",
        "tool_not_called",
        "tool_sequence",
        "tool_call_count",
        "tool_args_contains_any",
        "trace_contains_any",
        "state_matches",
        "response_contains",
        "response_matches",
    }
)
MATCH_OPS = frozenset({"$exists", "$eq", "$ne", "$contains", "$regex", "$in", "$gt", "$gte", "$lt", "$lte"})


class EvaluationSpecError(ValueError):
    pass


def validate_evaluation_spec(spec: Any, *, tool_names: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["evaluation must be an object"]
    for outcome in REQUIRED_OUTCOMES:
        if outcome not in spec:
            errors.append(f"evaluation.{outcome} is required")
            continue
        _validate_rule(spec[outcome], f"evaluation.{outcome}", errors, tool_names or set())
    for outcome in OPTIONAL_OUTCOMES:
        if outcome in spec:
            _validate_rule(spec[outcome], f"evaluation.{outcome}", errors, tool_names or set())
    return errors


def _validate_rule(rule: Any, path: str, errors: list[str], tool_names: set[str]) -> None:
    if not isinstance(rule, dict):
        errors.append(f"{path} must be an object")
        return
    rule_id = rule.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        errors.append(f"{path}.id must be a non-empty string")
    op = rule.get("op")
    if op not in RULE_OPS:
        errors.append(f"{path}.op must be one of: {', '.join(sorted(RULE_OPS))}")
        return

    if op == "constant":
        if not isinstance(rule.get("value"), bool):
            errors.append(f"{path}.value must be a boolean")
    elif op in {"all", "any"}:
        children = rule.get("rules")
        if not isinstance(children, list) or not children:
            errors.append(f"{path}.rules must be a non-empty array")
        else:
            for index, child in enumerate(children):
                _validate_rule(child, f"{path}.rules[{index}]", errors, tool_names)
    elif op == "not":
        if "rule" not in rule:
            errors.append(f"{path}.rule is required")
        else:
            _validate_rule(rule["rule"], f"{path}.rule", errors, tool_names)
    elif op in {"tool_called", "tool_not_called"}:
        _validate_tool_selector(rule, path, errors, tool_names)
    elif op == "tool_sequence":
        names = rule.get("names")
        if not isinstance(names, list) or not names or not all(isinstance(name, str) and name for name in names):
            errors.append(f"{path}.names must be a non-empty string array")
        elif tool_names:
            unknown = sorted(set(names) - tool_names)
            if unknown:
                errors.append(f"{path}.names references undeclared tools: {', '.join(unknown)}")
    elif op == "tool_call_count":
        _validate_tool_selector(rule, path, errors, tool_names, matcher_fields=False)
        if not isinstance(rule.get("count"), dict) or not rule["count"]:
            errors.append(f"{path}.count must be a comparison object")
        else:
            _validate_matcher(rule["count"], f"{path}.count", errors)
    elif op == "tool_args_contains_any":
        _validate_tool_selector(rule, path, errors, tool_names, matcher_fields=False)
        values = rule.get("values")
        if not isinstance(values, list) or not values or not all(isinstance(value, str) and value for value in values):
            errors.append(f"{path}.values must be a non-empty string array")
    elif op == "trace_contains_any":
        values = rule.get("values")
        if not isinstance(values, list) or not values or not all(isinstance(value, str) and value for value in values):
            errors.append(f"{path}.values must be a non-empty string array")
        channels = rule.get("channels")
        allowed_channels = {"response", "tool_args", "tool_result", "tool_error"}
        if channels is not None and (
            not isinstance(channels, list)
            or not channels
            or not all(isinstance(channel, str) and channel in allowed_channels for channel in channels)
        ):
            errors.append(f"{path}.channels contains an unsupported trace channel")
    elif op == "state_matches":
        if not isinstance(rule.get("path"), str) or not rule["path"]:
            errors.append(f"{path}.path must be a non-empty string")
        if "value" not in rule:
            errors.append(f"{path}.value is required")
        else:
            _validate_matcher(rule["value"], f"{path}.value", errors)
    elif op == "response_contains":
        if not isinstance(rule.get("value"), str) or not rule["value"]:
            errors.append(f"{path}.value must be a non-empty string")
    elif op == "response_matches":
        pattern = rule.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            errors.append(f"{path}.pattern must be a non-empty string")
        else:
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append(f"{path}.pattern is invalid: {exc}")


def _validate_tool_selector(
    rule: dict[str, Any],
    path: str,
    errors: list[str],
    tool_names: set[str],
    *,
    matcher_fields: bool = True,
) -> None:
    name = rule.get("name")
    name_regex = rule.get("name_regex")
    if name is not None and (not isinstance(name, str) or not name):
        errors.append(f"{path}.name must be a non-empty string")
    if name_regex is not None:
        if not isinstance(name_regex, str) or not name_regex:
            errors.append(f"{path}.name_regex must be a non-empty string")
        else:
            try:
                re.compile(name_regex)
            except re.error as exc:
                errors.append(f"{path}.name_regex is invalid: {exc}")
    if name and tool_names and name not in tool_names:
        errors.append(f"{path}.name references undeclared tool: {name}")
    if matcher_fields:
        if "args" in rule and not isinstance(rule["args"], dict):
            errors.append(f"{path}.args must be an object")
        elif "args" in rule:
            _validate_matcher(rule["args"], f"{path}.args", errors)
        if "result" in rule:
            _validate_matcher(rule["result"], f"{path}.result", errors)
        if "ok" in rule and not isinstance(rule["ok"], bool):
            errors.append(f"{path}.ok must be a boolean")


def _validate_matcher(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_matcher(item, f"{path}[{index}]", errors)
        return
    if not isinstance(value, dict) or not value:
        return
    operator_keys = [str(key) for key in value if str(key).startswith("$")]
    if operator_keys and len(operator_keys) != len(value):
        errors.append(f"{path} cannot mix matcher operators with object fields")
        return
    if operator_keys:
        unknown = sorted(set(operator_keys) - MATCH_OPS)
        if unknown:
            errors.append(f"{path} uses unsupported matcher operators: {', '.join(unknown)}")
        if "$exists" in value and not isinstance(value["$exists"], bool):
            errors.append(f"{path}.$exists must be a boolean")
        if "$in" in value and not isinstance(value["$in"], list):
            errors.append(f"{path}.$in must be an array")
        if "$regex" in value:
            pattern = value["$regex"]
            if not isinstance(pattern, str) or not pattern:
                errors.append(f"{path}.$regex must be a non-empty string")
            else:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    errors.append(f"{path}.$regex is invalid: {exc}")
        return
    for key, item in value.items():
        _validate_matcher(item, f"{path}.{key}", errors)
