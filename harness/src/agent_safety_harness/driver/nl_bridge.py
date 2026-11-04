"""nl_bridge · 用 harness_driver LLM 角色把自然语言转成 plan（伦锋 负责）

复用包内已有的 OpenAICompatibleChatClient（llm.py），密钥只走 api_key_env 环境变量。
关键点：system_prompt 里把 ToolRegistry 当前所有可用函数列给 LLM，
强制它只能产出“已批准函数”的白名单成员——driver 不能越权调未注册动作。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ..llm import OpenAICompatibleChatClient
from ..registry import REGISTRY, ToolRegistry
from .planner import Plan, PlanStep

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "llm_roles.json"


class NLBridge:
    def __init__(self, client: OpenAICompatibleChatClient, *, registry: ToolRegistry = REGISTRY) -> None:
        self.client = client
        self.registry = registry

    @classmethod
    def from_config(
        cls,
        role: str = "harness_driver",
        config_path: Path = CONFIG_PATH,
        registry: ToolRegistry = REGISTRY,
    ) -> "NLBridge":
        if not config_path.exists():
            raise FileNotFoundError(f"找不到 llm_roles.json：{config_path}")
        data = json.loads(config_path.read_text(encoding="utf-8"))
        roles = data.get("roles", {})
        if role not in roles:
            raise KeyError(f"llm_roles.json 中没有角色 {role!r}；已有：{sorted(roles)}")
        cfg = roles[role]
        client = OpenAICompatibleChatClient(
            base_url=cfg["base_url"],
            api_key_env=cfg["api_key_env"],
            model=cfg["model"],
            temperature=cfg.get("temperature", 0.0),
            max_tokens=cfg.get("max_tokens", 1024),
            timeout_seconds=cfg.get("timeout_seconds", 60),
        )
        return cls(client, registry=registry)

    def to_plan(self, instruction: str, *, registry: Optional[ToolRegistry] = None) -> Plan:
        reg = registry or self.registry
        tools = [
            {"name": s.name, "description": s.description, "args": list(s.arg_doc or {})}
            for s in reg.list()
        ]
        system = (
            "你是 LLM/Agent 安全评测 harness 的编排大脑。给定自然语言指令，输出一个 JSON 执行计划："
            "一个有序 steps 列表。每个 step 只能引用 available tools 中存在的函数（精确 name），并给出 args。"
            "严禁发明工具。只输出 JSON，格式："
            '{"steps":[{"fn":"<函数名>","args":{...},"note":"..."}]}'
        )
        user = (
            f"可用工具：\n{json.dumps(tools, ensure_ascii=False, indent=2)}\n\n"
            f"指令：{instruction}"
        )
        content = self.client.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        plan_json = _extract_json(content)
        steps: list[PlanStep] = []
        for s in plan_json.get("steps", []):
            fn = s.get("fn")
            if fn not in reg.names():
                raise ValueError(f"LLM 提议了未注册函数 {fn!r}，不在白名单内")
            steps.append(PlanStep(fn=fn, args=s.get("args", {}), note=s.get("note")))
        return Plan(steps=steps, rationale="LLM 生成")


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        text = parts[1]
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM 输出中找不到 JSON 对象")
    return json.loads(text[start : end + 1])
