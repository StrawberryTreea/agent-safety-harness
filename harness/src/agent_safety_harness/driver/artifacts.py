"""核对产物引用，保存不含私有内容的最小必要执行记录。"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

RECEIPTS = {
    "bench_intake": ("intake", "intake.json"),
    "build_sandbox": ("runtime", "runtime.json"),
    "run_capture": ("capture_receipt", "capture_receipt.json"),
    "score": ("score_receipt", "score_receipt.json"),
    "evolve": ("manifest", "manifest.json"),
}


def checked_artifacts(fn: str, args: dict, output: dict) -> dict[str, str]:
    root = Path(args["out"]).expanduser().resolve()
    key, name = RECEIPTS[fn]
    paths = {key: root / name}
    if fn == "score":
        for field in ("results", "summary"):
            if not isinstance(output.get(field), str):
                raise ValueError("评分结果缺少产物路径")
            paths[field] = Path(output[field]).expanduser().resolve()
    elif fn == "evolve":
        paths.update(
            {
                "seed_refs": root / "seed_refs.jsonl",
                "pending_requests": root / "pending_requests.jsonl",
                "summary": root / "summary.json",
            }
        )
    for path in paths.values():
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError("产物必须是当前步骤输出目录内已存在的文件")
    return {key: str(path.resolve()) for key, path in paths.items()}


def public_output(output: dict) -> dict:
    # 这里只保留编排所需的状态和计数，不保存完整轨迹、模型响应或评分规则。
    fields = (
        "status",
        "canonical_ready",
        "native_exit_code",
        "task_count",
        "seed_count",
        "admitted_candidate_count",
        "automatic_admission",
        "automatic_promotion",
    )
    return {
        key: output[key]
        for key in fields
        if key in output and type(output[key]) in (str, bool, int, type(None))
    }


def prepare_record_dir(path: str | Path) -> Path:
    root = Path(path).expanduser().resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise FileExistsError("执行记录目录必须是新目录或空目录")
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_execution(root: Path, result) -> None:
    record = asdict(result)
    # 不持久化指令正文、任意说明、原始参数或异常全文。
    payload = {"schema_version": "driver_execution_v1", **record}
    temporary = root / "execution.json.tmp"
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(root / "execution.json")
