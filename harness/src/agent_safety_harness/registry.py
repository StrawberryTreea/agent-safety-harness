"""统一能力函数层 · TOOL REGISTRY

为什么有这一层（总体框架定死）：
    整个 harness 的“能力”先在这里注册成带 schema 的可调用函数，然后 CLI 和
    harness_driver（自然语言驱动）都从这里取函数、直接调用。这样：
      - CLI 手敲命令 和 自然语言描述，最终落到同一批函数，底层零重复；
      - harness_driver 直接调 Python 函数，绝不 shell 出 CLI 字符串（避免慢 + 丢结构化数据）；
      - 新增一个能力，只在引擎里写好 + 在此注册一次，两个交互面自动可用。

这是成员1（引擎组）负责落地的模块。所有能力函数的真实实现归各引擎模块，
本文件只做“注册 / 取用 / 调用”的薄调度层，不实现任何评测逻辑。
"""

from __future__ import annotations

import inspect
import warnings
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class FunctionSpec:
    name: str
    description: str
    fn: Callable[..., Any]
    input_schema: dict[str, Any] = field(default_factory=dict)   # JSON-schema-like，描述入参
    output_schema: dict[str, Any] = field(default_factory=dict)  # JSON-schema-like，描述返回值
    tags: list[str] = field(default_factory=list)
    arg_doc: dict[str, str] = field(default_factory=dict)        # 参数白话说明，给 driver/LLM 看


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, FunctionSpec] = {}

    def register(self, spec: FunctionSpec) -> FunctionSpec:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec
        return spec

    def register_fn(
        self,
        name: str,
        description: str,
        *,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        arg_doc: dict[str, str] | None = None,
    ):
        """装饰器：把一个普通函数注册为可调用工具。"""
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.register(
                FunctionSpec(
                    name=name,
                    description=description,
                    fn=fn,
                    input_schema=input_schema or {},
                    output_schema=output_schema or {},
                    tags=tags or [],
                    arg_doc=arg_doc or {},
                )
            )
            return fn
        return deco

    def get(self, name: str) -> FunctionSpec:
        if name not in self._tools:
            raise KeyError(f"No such tool: {name!r}. Registered: {sorted(self._tools)}")
        return self._tools[name]

    def list(self) -> list[FunctionSpec]:
        return sorted(self._tools.values(), key=lambda s: s.name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def call(self, name: str, **kwargs: Any) -> Any:
        spec = self.get(name)
        # 软校验：引擎函数自己会严格校验；这里只 warn 未知参数，不阻断。
        try:
            expected = set(spec.input_schema.get("properties", {})) or set(
                inspect.signature(spec.fn).parameters
            )
        except (ValueError, TypeError):
            expected = set()
        unexpected = set(kwargs) - expected
        if unexpected:
            warnings.warn(f"Tool {name!r} got unexpected args {sorted(unexpected)}")
        return spec.fn(**kwargs)


# 包级单例：CLI 与 driver 共享同一个注册表。
REGISTRY = ToolRegistry()
