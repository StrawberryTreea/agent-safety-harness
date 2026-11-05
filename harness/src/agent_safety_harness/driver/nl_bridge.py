"""原始任务描述（原文保留，当前实现见下方修改说明）

nl_bridge · 用 harness_driver LLM 角色把自然语言转成 plan（伦锋 负责）

复用包内已有的 OpenAICompatibleChatClient（llm.py），密钥只走 api_key_env 环境变量。
关键点：system_prompt 里把 ToolRegistry 当前所有可用函数列给 LLM，
强制它只能产出“已批准函数”的白名单成员——driver 不能越权调未注册动作。

本次修改与新增功能：
    - 复用现有配置定位函数，并向模型提供本轮允许函数的完整参数 schema。
    - 模型只选择步骤，args 必须为空；资源参数、路径和权限由本地配置绑定。
    - 规划响应进入统一校验；模型调用失败、非法 JSON 或越权函数均明确拒绝。
    - 不发送私有任务、评分规则或失败证据，不静默降级为关键词计划。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import default_config_dir, load_json_config
from ..llm import OpenAICompatibleChatClient
from ..registry import REGISTRY, ToolRegistry
from .planner import Plan
from .validation import P0_FUNCTIONS, plan_from_record

CONFIG_PATH = default_config_dir() / "llm_roles.json"


class NLBridge:
    def __init__(
        self,
        client: OpenAICompatibleChatClient,
        *,
        registry: ToolRegistry = REGISTRY,
    ):
        self.client = client
        self.registry = registry

    @classmethod
    def from_config(
        cls,
        role: str = "harness_driver",
        config_path: Path = CONFIG_PATH,
        registry: ToolRegistry = REGISTRY,
    ) -> "NLBridge":
        roles = load_json_config(config_path).get("roles", {})
        if role not in roles:
            raise ValueError("llm_roles.json 中没有配置所请求的规划角色")
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

    def to_plan(
        self,
        instruction: str,
        *,
        registry: ToolRegistry | None = None,
        allowed=None,
    ) -> Plan:
        reg = registry or self.registry
        approved = P0_FUNCTIONS if allowed is None else frozenset(allowed)
        if not approved.issubset(P0_FUNCTIONS):
            raise ValueError("规划白名单不能超出已实现的 P0 能力")
        tools = [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
            }
            for spec in reg.list()
            if spec.name in approved
        ]
        # 本地运行配置、文件内容、私有规则和捕获数据均不得进入提示词。
        # 用户提供的指令也应只描述意图，不粘贴私有证据。
        system = (
            "你是 LLM/Agent 安全评测 harness 的编排大脑。给定自然语言指令，输出一个 JSON 执行计划："
            "schema_version 为 driver_plan_v1，steps 为非空的有序列表。"
            "每个 step 只能引用可用工具中存在的函数（精确 name），"
            "可包含 id、fn、depends_on、condition、note 和 args。"
            "严禁发明工具。args 必须为空对象 {}，资源参数由宿主程序从本地配置绑定；"
            "不得自行生成资源参数、路径、shell 命令或权限。analyze 尚未接入，不得添加该步骤。"
            "只输出 JSON，格式："
            '{"schema_version":"driver_plan_v1","steps":['
            '{"id":"step-1","fn":"<函数名>","args":{},"note":"..."}]}'
        )
        user = (
            f"可用工具：\n{json.dumps(tools, ensure_ascii=False, indent=2)}\n\n"
            f"指令：{instruction}"
        )
        try:
            content = self.client.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            )
        except Exception:
            # 模型服务异常可能包含请求正文或凭证，不能直接输出。
            raise RuntimeError("规划模型调用失败，未执行任何兜底计划") from None
        plan = plan_from_record(_extract_json(content))
        for step in plan.steps:
            if step.fn not in approved or step.fn not in reg.names():
                raise ValueError("LLM 提议了未注册或未经本轮许可的函数，不在白名单内")
            if step.args:
                raise ValueError("LLM 只能选择步骤，不能自行提供资源参数")
        return plan


def _extract_json(text: str) -> dict:
    # 允许单个 Markdown JSON 代码围栏，但不接受 JSON 前后的任意说明或命令。
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("LLM 输出的 JSON 存在重复字段")
            result[key] = value
        return result

    value = json.loads(text, object_pairs_hook=unique_keys)
    if not isinstance(value, dict):
        raise ValueError("LLM 输出必须是 JSON 对象")
    return value
