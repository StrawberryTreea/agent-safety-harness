"""原始任务描述（原文保留，当前实现见下方修改说明）

driver 子包演示入口：python -m agent_safety_harness.driver

展示“自然语言/关键词指令 → plan → 执行（dry_run）”整条链路，不依赖真实引擎。
真实接入后，把 dry_run=True 改成 False 即跑真引擎。

本次修改与新增功能：
    - 演示入口扩展为正式 CLI，支持计划文件或“指令＋本地配置”两种输入。
    - 无参数时显示帮助；默认 dry-run，显式指定 --execute 才真实执行。
    - 不再通过修改代码中的 dry_run 值切换执行；使用命令行参数控制。
    - 新增独立执行记录目录、退出码及中文错误提示。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import uuid

from .executor import execute_plan
from .nl_bridge import NLBridge
from .planner import plan_from_instruction
from .validation import (
    allowed_functions,
    plan_from_record,
    read_object,
    validate_config,
)


def main(argv: list[str] | None = None) -> int:
    # 模块文档保留原任务和修改记录；命令行帮助只展示当前用法，避免混入旧演示说明。
    parser = argparse.ArgumentParser(
        description="driver 入口：默认仅校验计划（dry-run），显式指定 --execute 才调用真实函数。"
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--plan", type=Path, help="可信的 driver_plan_v1 结构化 JSON 计划"
    )
    source.add_argument(
        "--instruction", help="仅描述意图；资源参数必须通过 --config 提供"
    )
    parser.add_argument(
        "--config", type=Path, help="本地白名单、参数绑定及 run_root 配置"
    )
    parser.add_argument("--planner", choices=("keyword", "llm"), default="keyword")
    parser.add_argument(
        "--roles-config", type=Path, help="指定 LLM 角色配置；仅用于 --planner llm"
    )
    parser.add_argument(
        "--execute", action="store_true", help="调用真实函数；不指定时仅校验计划"
    )
    parser.add_argument(
        "--out", type=Path, help="独立的 driver 执行记录目录，与引擎输出目录分开"
    )
    args = parser.parse_args(argv)
    if args.plan is None and args.instruction is None:
        parser.print_help()
        return 0
    if args.plan is not None and (args.planner != "keyword" or args.roles_config):
        parser.error("模型规划选项必须配合 --instruction 使用")
    if args.instruction is not None and args.config is None:
        parser.error("--instruction 必须同时提供 --config")
    if args.roles_config and args.planner != "llm":
        parser.error("--roles-config 必须配合 --planner llm 使用")
    try:
        config = read_object(args.config) if args.config else {}
        validate_config(config)
        if args.plan:
            if "arguments" in config or "run_root" in config:
                raise ValueError(
                    "文件计划的配置只接受 allowed_functions；资源参数应写在计划中"
                )
            plan = plan_from_record(read_object(args.plan))
        else:
            client = None
            if args.planner == "llm":
                # 显式选择 LLM 规划器即表示需要调用规划模型，
                # 即使生成的业务计划只做 dry-run，也会发生这次模型调用。
                client = (
                    NLBridge.from_config(config_path=args.roles_config)
                    if args.roles_config
                    else NLBridge.from_config()
                )
            plan = plan_from_instruction(args.instruction, config=config, client=client)
        record_dir = args.out or Path("runs") / ("driver-" + uuid.uuid4().hex)
        result = execute_plan(
            plan,
            dry_run=not args.execute,
            allowed=allowed_functions(config),
            record_dir=record_dir,
        )
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        if args.execute:
            print(f"执行记录：{record_dir.resolve()}")
        return (
            0
            if result.status in {"planned", "completed", "completed_with_skips"}
            else 2
        )
    except Exception as exc:
        # 校验错误只指出字段，不输出字段中的私有内容。
        # 模型服务或 JSON 解析异常不直接输出原始文字。
        if type(exc) is ValueError:
            print(f"driver 拒绝执行：{exc}", file=sys.stderr)
        else:
            print(f"driver 执行失败：{type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
