"""文件、Python 和自然语言计划共用校验入口；校验不通过即拒绝执行。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import re
from typing import Any

from jsonschema import Draft202012Validator, validators

from ..registry import REGISTRY, ToolRegistry
from .planner import Plan, PlanStep

P0_FUNCTIONS = frozenset(
    {"bench_intake", "build_sandbox", "run_capture", "score", "evolve"}
)
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]+$")
FIELD = re.compile(r"^(artifacts|output)(\.[A-Za-z0-9_]+)+$")


def read_object(path: str | Path) -> dict[str, Any]:
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON 对象存在重复字段")
            result[key] = value
        return result

    value = json.loads(
        Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique_keys
    )
    if not isinstance(value, dict):
        raise ValueError("输入必须是 JSON 对象")
    return value


def validate_config(config: dict[str, Any]) -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "allowed_functions": {
                "type": "array",
                "uniqueItems": True,
                "items": {"enum": sorted(P0_FUNCTIONS)},
            },
            "run_root": {"type": "string", "minLength": 1},
            "arguments": {
                "type": "object",
                "propertyNames": {"enum": sorted(P0_FUNCTIONS)},
                "additionalProperties": {"type": "object"},
            },
        },
    }
    _check_schema(config, schema, label="运行配置")


def allowed_functions(config: dict[str, Any]) -> frozenset[str]:
    return frozenset(config.get("allowed_functions", P0_FUNCTIONS))


def is_reference(value: Any) -> bool:
    return isinstance(value, dict) and "$from" in value


def references(value: Any):
    if is_reference(value):
        if (
            set(value) != {"$from", "field"}
            or not isinstance(value["$from"], str)
            or not isinstance(value["field"], str)
            or not FIELD.fullmatch(value["field"])
        ):
            raise ValueError("步骤产物引用格式无效")
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def _defer_reference(validator):
    def validate(checker, constraint, instance, schema):
        # 仅暂缓对当前引用占位值的 schema 校验。
        # 引用解析完成后，在调用注册函数前按原 schema 校验实际值。
        if not is_reference(instance):
            yield from validator(checker, constraint, instance, schema)

    return validate


DeferredValidator = validators.extend(
    Draft202012Validator,
    {
        name: _defer_reference(check)
        for name, check in Draft202012Validator.VALIDATORS.items()
    },
)


def _check_schema(value: Any, schema: dict, *, label: str, defer: bool = False) -> None:
    validator = DeferredValidator if defer else Draft202012Validator
    error = next(validator(schema).iter_errors(value), None)
    if error:
        # jsonschema 默认错误信息会插入具体值，可能泄露私有内容。
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        detail = ""
        if error.validator == "required" and isinstance(error.instance, dict):
            missing = sorted(set(error.validator_value) - set(error.instance))
            detail = "，缺少字段：" + ",".join(missing)
        raise ValueError(
            f"{label} 在 {location} 处不符合 {error.validator} 规则{detail}"
        )


def validate_arguments(
    fn: str, args: dict, registry: ToolRegistry, *, defer: bool = False
) -> None:
    if is_reference(args):
        raise ValueError("引用只能替代单个参数值，不能替代整个参数对象")
    _check_schema(args, registry.get(fn).input_schema, label=f"{fn} 参数", defer=defer)


def plan_from_record(record: dict[str, Any]) -> Plan:
    schema = {
        "type": "object",
        "required": ["schema_version", "steps"],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"const": "driver_plan_v1"},
            "rationale": {"type": "string"},
            "steps": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["fn"],
                    "additionalProperties": False,
                    "properties": {
                        "fn": {"type": "string"},
                        "id": {"type": ["string", "null"]},
                        "args": {"type": "object"},
                        "note": {"type": ["string", "null"]},
                        "condition": {"type": ["object", "null"]},
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "uniqueItems": True,
                        },
                    },
                },
            },
        },
    }
    _check_schema(record, schema, label="计划")
    return Plan(
        [PlanStep(**step) for step in record["steps"]], record.get("rationale", "")
    )


def validate_plan(
    plan: Plan, *, registry: ToolRegistry = REGISTRY, allowed=None
) -> Plan:
    if not isinstance(plan, Plan):
        raise ValueError("输入必须是 Plan 对象")
    result = plan_from_record(asdict(plan))
    allowed = P0_FUNCTIONS if allowed is None else frozenset(allowed)
    if not allowed.issubset(P0_FUNCTIONS):
        raise ValueError("本轮白名单不能超出已实现的 P0 能力")
    seen: list[str] = []
    literal_outputs: list[Path] = []
    for index, step in enumerate(result.steps):
        step.id = step.id or f"step-{index + 1}"
        if not IDENTIFIER.fullmatch(step.id) or step.id in seen:
            raise ValueError("步骤 ID 必须唯一，且仅包含可安全用于路径的字符")
        if step.fn == "analyze":
            raise ValueError("analyze 能力尚未接入，请提供已有分析产物")
        if step.fn not in allowed or step.fn not in registry.names():
            raise ValueError(f"步骤 {step.id}：函数未注册或未经本轮许可")
        if any(dependency not in seen for dependency in step.depends_on):
            raise ValueError(f"步骤 {step.id}：依赖必须指向前序步骤")
        if step.condition is not None:
            cond = deepcopy(step.condition)
            if "if_output_of" in cond:
                if set(cond) != {"if_output_of", "key", "equals"}:
                    raise ValueError("旧版条件格式无效")
                old_index = cond["if_output_of"]
                if type(old_index) is not int or not 0 <= old_index < index:
                    raise ValueError("条件索引必须指向有效的前序步骤")
                if not isinstance(cond["key"], str):
                    raise ValueError("条件字段名必须是文本")
                source = seen[old_index]
                cond = {
                    "$from": source,
                    "field": "output." + cond["key"],
                    "equals": cond["equals"],
                }
                if source not in step.depends_on:
                    step.depends_on.append(source)
            if set(cond) != {"$from", "field", "equals"}:
                raise ValueError("条件格式无效")
            list(references({key: cond[key] for key in ("$from", "field")}))
            if cond["$from"] not in step.depends_on:
                raise ValueError("条件来源必须列入依赖")
            step.condition = cond
        for reference in references(step.args):
            if reference["$from"] not in step.depends_on:
                raise ValueError(f"步骤 {step.id}：引用必须指向已声明的前序依赖")
        validate_arguments(step.fn, step.args, registry, defer=True)
        out = step.args.get("out")
        if isinstance(out, str):
            path = Path(out).expanduser().resolve()
            if any(
                path == other or path in other.parents or other in path.parents
                for other in literal_outputs
            ):
                raise ValueError("各步骤的输出目录必须独立，不能重复或相互嵌套")
            literal_outputs.append(path)
        seen.append(step.id)
    return result


def resolve_reference(reference: dict, context: dict[str, dict]) -> Any:
    value = context[reference["$from"]]
    for part in reference["field"].split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError("引用的输出字段不存在")
        value = value[part]
    return deepcopy(value)


def resolve_arguments(value: Any, context: dict[str, dict]) -> Any:
    if is_reference(value):
        return resolve_reference(value, context)
    if isinstance(value, dict):
        return {key: resolve_arguments(child, context) for key, child in value.items()}
    if isinstance(value, list):
        return [resolve_arguments(child, context) for child in value]
    return value
