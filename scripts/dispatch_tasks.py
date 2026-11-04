#!/usr/bin/env python3
"""Gate for handing out work: check readiness first, then dispatch once.

Why this exists
---------------
Opening an issue and assigning it to someone sends that person a notification.
Handing out five tasks one at a time therefore sends five separate emails at
five different moments, which reads as noise rather than as a plan. The fix is
not to send fewer emails by sending them more quietly -- it is to send them at
one deliberate moment, after the repository is actually ready for the work.

This script encodes that moment as two steps:

  --check    (default) evaluate the readiness criteria and print a verdict.
             Changes nothing. Exits 0 when every criterion passes.
  --dispatch assign the prepared task issues to their owners, in one run.

The readiness criteria are deliberately mechanical. Each one is a fact about
the repository that can be checked by a program, not a judgement call:

  1. contract      the schema/registry/protocol files exist and are tracked
  2. pipeline      the pipeline entry point answers --help
  3. acceptance    scripts/check_repo.py passes (full suite, no model calls)
  4. documentation the document map is consistent with the tracked files
  5. roster        every CODEOWNERS path has >= 3 owners, all in the roster
  6. tasks         every prepared task issue names an owner and has content

Only when all six pass does --dispatch write anything.

Usage
-----
    python scripts/dispatch_tasks.py                 # readiness report, no writes
    python scripts/dispatch_tasks.py --json out.json # same, machine readable
    python scripts/dispatch_tasks.py --dispatch      # assign tasks, one batch
    python scripts/dispatch_tasks.py --dispatch --dry-run   # show what it would do

Network access is only needed for --dispatch. The readiness check runs
entirely against the local working tree.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROSTER = ROOT / "docs" / "roster.json"

# Files that define the contract. If these are missing or untracked, no task
# can be handed out safely: every track depends on them.
# Paths match .github/CODEOWNERS and the CI gate "契约变更需王浩知悉".
CONTRACT_FILES = (
    "harness/src/agent_safety_harness/schema.py",
    "harness/src/agent_safety_harness/registry.py",
    "harness/src/agent_safety_harness/driver/registrations.py",
    "protocol/default_taxonomies.yaml",
)

# Labels that mark an issue as a prepared, dispatchable task.
TASK_LABELS = ("P0", "P1", "P2")

# Owner label prefix, e.g. "owner:liuke" -> "liuke".
OWNER_LABEL_PREFIX = "owner:"


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


# --------------------------------------------------------------------------
# Readiness criteria. Each returns (passed, detail).
# --------------------------------------------------------------------------

def check_contract(root: Path = ROOT) -> Tuple[bool, str]:
    """The contract files must exist and be tracked by Git."""
    missing = [rel for rel in CONTRACT_FILES if not (root / rel).exists()]
    if missing:
        return False, "缺少契约文件：" + "、".join(missing)
    tracked = subprocess.run(
        ["git", "ls-files", *CONTRACT_FILES], cwd=root,
        capture_output=True, text=True, check=False,
    )
    if tracked.returncode != 0:
        return False, "git ls-files 失败：" + tracked.stderr.strip()
    untracked = sorted(set(CONTRACT_FILES) - set(tracked.stdout.split()))
    if untracked:
        return False, "契约文件未纳入版本管理：" + "、".join(untracked)
    return True, f"{len(CONTRACT_FILES)} 个契约文件齐备且已入库"


def project_python(root: Path = ROOT) -> str:
    """The interpreter that actually has the project's dependencies installed.

    The gate runs the acceptance suite in a subprocess, and that suite imports
    third-party packages (jsonschema, requests, ...). Those live in the
    project's own .venv, not in whatever interpreter happens to be running
    this script. Using sys.executable therefore reports a false failure on any
    machine where the ambient Python lacks the dependencies -- the gate would
    then block dispatch for a reason that has nothing to do with the code.

    Prefer .venv when it exists; fall back to the current interpreter so the
    script still works in a bare checkout.
    """
    for relative in (".venv/Scripts/python.exe", ".venv/bin/python", ".venv/Scripts/python"):
        candidate = root / relative
        if candidate.exists():
            return str(candidate)
    return sys.executable


def check_pipeline(root: Path = ROOT) -> Tuple[bool, str]:
    """The pipeline entry point must be runnable, or no task can be started."""
    entry = root / "pipeline.py"
    if not entry.exists():
        return False, "缺少 pipeline.py"
    try:
        result = subprocess.run(
            [project_python(root), str(entry), "--help"], cwd=root,
            capture_output=True, text=True, timeout=120, check=False,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"无法启动 pipeline.py：{exc}"
    if result.returncode != 0:
        return False, f"pipeline.py --help 退出码 {result.returncode}"
    return True, "pipeline.py --help 可用"


def check_acceptance(root: Path = ROOT) -> Tuple[bool, str]:
    """The full acceptance suite must pass."""
    out = root / "runs" / ("dispatch-acceptance-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
    try:
        result = subprocess.run(
            [project_python(root), "scripts/check_repo.py", "--out", str(out)],
            cwd=root, capture_output=True, text=True, timeout=600, check=False,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"无法运行验收脚本：{exc}"
    if result.returncode != 0:
        tail = (result.stdout or result.stderr or "").strip().splitlines()
        return False, "验收未通过：" + (tail[-1] if tail else "无输出")
    return True, f"scripts/check_repo.py 全绿（日志：{out.name}）"


def check_documentation(root: Path = ROOT) -> Tuple[bool, str]:
    """The document map must list exactly the tracked documents."""
    script = root / "scripts" / "check_doc_map.py"
    if not script.exists():
        return False, "缺少 scripts/check_doc_map.py"
    try:
        result = subprocess.run(
            [project_python(root), "scripts/check_doc_map.py"], cwd=root,
            capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"无法运行文档地图检查：{exc}"
    if result.returncode != 0:
        return False, "文档地图不一致（新增文档未登记或路径失效）"
    return True, "文档地图与受管文件一致"


def check_roster(root: Path = ROOT, roster_path: Path = DEFAULT_ROSTER) -> Tuple[bool, str]:
    """Every CODEOWNERS path needs enough owners, all of them known members."""
    sys.path.insert(0, str(root / "scripts"))
    try:
        import oncall_roster  # noqa: E402
    except ImportError as exc:
        return False, f"无法加载 oncall_roster：{exc}"
    if not roster_path.exists():
        return False, f"缺少名册：{roster_path.name}"
    try:
        roster = oncall_roster.load_roster(roster_path)
    except ValueError as exc:
        return False, f"名册不合法：{exc}"
    problems = oncall_roster.codeowners_coverage(root / ".github" / "CODEOWNERS", roster, 2)
    if problems:
        first = problems[0]
        return False, f"CODEOWNERS 覆盖度不达标（{len(problems)} 处）：{first['pattern']}"
    return True, f"{len(roster['members'])} 名成员，每条路径至少 2 个知情人（主 owner + 代理人）"


def check_tasks(tasks: Sequence[Dict[str, Any]]) -> Tuple[bool, str]:
    """Every prepared task must name an owner and carry real content."""
    if not tasks:
        return False, "没有找到已准备的 P0 任务"
    problems: List[str] = []
    for task in tasks:
        number = task.get("number")
        if not task.get("owner"):
            problems.append(f"#{number} 没有 owner 标签")
        body = str(task.get("body") or "")
        if len(body) < 200:
            problems.append(f"#{number} 正文过短，交付物可能没写全")
        if "完成标准" not in body and "验收命令" not in body:
            problems.append(f"#{number} 缺少完成标准或验收命令")
    if problems:
        return False, "；".join(problems[:5])
    return True, f"{len(tasks)} 个任务都有负责人与验收标准"


# --------------------------------------------------------------------------
# GitHub API
# --------------------------------------------------------------------------

def api_base() -> str:
    """Base URL for GitHub API calls. Overridable for tests via DISPATCH_API_BASE."""
    return os.environ.get("DISPATCH_API_BASE", "https://api.github.com").rstrip("/")


def api_request(method: str, path: str, token: Optional[str], payload: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{api_base()}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Accept": "application/vnd.github+json",
                                              "User-Agent": "agent-safety-harness-dispatch",
                                              "Content-Type": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def resolve_token(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value
    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    token = result.stdout.strip()
    return token or None


def task_issues(repo: str, token: Optional[str]) -> List[Dict[str, Any]]:
    """Open issues carrying a priority label, with their owner label resolved.

    The GitHub `labels` query parameter is an OR over labels, so it also returns
    issues that only match one of the names. The priority label is therefore
    re-checked locally: an issue counts as a task only when it really carries
    one of P0/P1/P2. Without this, helper issues such as the on-call board
    (which is labelled type:chore + track:infra) would be treated as tasks.
    """
    issues = api_request("GET", f"/repos/{repo}/issues", token,
                         {"state": "open", "labels": ",".join(TASK_LABELS), "per_page": 100}) or []
    tasks: List[Dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        labels = [str(label.get("name", "")) for label in issue.get("labels") or []]
        if not any(label in TASK_LABELS for label in labels):
            continue
        owner = ""
        for label in labels:
            if label.startswith(OWNER_LABEL_PREFIX):
                owner = label[len(OWNER_LABEL_PREFIX):]
                break
        tasks.append({
            "number": issue.get("number"),
            "title": issue.get("title", ""),
            "body": issue.get("body", "") or "",
            "labels": labels,
            "owner": owner,
            "assignees": [str(a.get("login", "")) for a in issue.get("assignees") or []],
        })
    return tasks


def dispatch(repo: str, tasks: Sequence[Dict[str, Any]], roster: Dict[str, Any],
             token: Optional[str], dry_run: bool = False) -> List[Dict[str, Any]]:
    """Assign each task to its owner. One batch, one notification per person."""
    accounts = {str(member.get("id")): str(member.get("github", "")) for member in roster["members"]}
    results: List[Dict[str, Any]] = []
    for task in tasks:
        account = accounts.get(task.get("owner", ""), "")
        record = {"number": task.get("number"), "owner": task.get("owner"), "account": account,
                  "title": task.get("title", "")}
        if not account:
            record["status"] = "skipped"
            record["detail"] = "名册里找不到该 owner"
            results.append(record)
            continue
        if task.get("assignees") == [account]:
            record["status"] = "already"
            record["detail"] = "已指派给该成员"
            results.append(record)
            continue
        if dry_run:
            record["status"] = "would-assign"
            record["detail"] = f"将指派给 {account}"
            results.append(record)
            continue
        try:
            api_request("PATCH", f"/repos/{repo}/issues/{task['number']}", token,
                        {"assignees": [account]})
            record["status"] = "assigned"
            record["detail"] = f"已指派给 {account}"
        except urllib.error.HTTPError as exc:
            record["status"] = "failed"
            record["detail"] = f"指派失败：{exc}"
        results.append(record)
    return results


def render_report(checks: Sequence[Dict[str, Any]]) -> str:
    lines = ["仓库准备就绪检查", ""]
    width = max(len(str(check["name"])) for check in checks) if checks else 0
    for check in checks:
        mark = "PASS" if check["passed"] else "FAIL"
        lines.append(f"  [{mark}] {str(check['name']).ljust(width)}  {check['detail']}")
    ready = all(check["passed"] for check in checks)
    lines.extend(["", f"结论：{'仓库已就绪，可以统一派工' if ready else '尚未就绪，先不要派工'}"])
    return "\n".join(lines)


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dispatch", action="store_true",
                        help="assign the prepared tasks once every readiness check passes")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --dispatch, show the assignments without writing them")
    parser.add_argument("--repo", help="owner/name override")
    parser.add_argument("--roster", type=Path, default=DEFAULT_ROSTER)
    parser.add_argument("--token", help="GitHub token (default: GITHUB_TOKEN / GH_TOKEN / gh auth token)")
    parser.add_argument("--json", type=Path, help="write the report as JSON")
    parser.add_argument("--skip-acceptance", action="store_true",
                        help="skip the full acceptance suite (faster, less conclusive)")
    args = parser.parse_args()

    roster_data = json.loads(args.roster.resolve().read_text(encoding="utf-8"))
    repo = args.repo or str(roster_data.get("repo") or "StrawberryTreea/agent-safety-harness")

    token = resolve_token(args.token)
    tasks: List[Dict[str, Any]] = []
    if token:
        try:
            tasks = task_issues(repo, token)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            print(f"读取任务列表失败：{exc}", file=sys.stderr)
            tasks = []

    checks: List[Dict[str, Any]] = []

    def add(name: str, outcome: Tuple[bool, str]) -> None:
        checks.append({"name": name, "passed": outcome[0], "detail": outcome[1]})

    add("契约文件", check_contract())
    add("流水线可运行", check_pipeline())
    if args.skip_acceptance:
        checks.append({"name": "整仓验收", "passed": True, "detail": "已按 --skip-acceptance 跳过"})
    else:
        add("整仓验收", check_acceptance())
    add("文档地图", check_documentation())
    add("名册与 owner 覆盖", check_roster())
    if tasks:
        add("任务内容完整", check_tasks(tasks))
    else:
        checks.append({"name": "任务内容完整", "passed": False,
                       "detail": "未取到任务列表（需要 token 且仓库中已有带优先级标签的 issue）"})

    ready = all(check["passed"] for check in checks)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "repo": repo,
              "ready": ready, "checks": checks, "tasks": tasks}

    print(render_report(checks))

    if args.dispatch:
        if not ready:
            print("\n尚未就绪，未做任何派工。", file=sys.stderr)
            report["dispatch"] = {"performed": False, "reason": "readiness checks failed"}
            if args.json:
                args.json.parent.mkdir(parents=True, exist_ok=True)
                args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return 1
        if not token:
            print("\n缺少 GitHub token，无法派工。", file=sys.stderr)
            report["dispatch"] = {"performed": False, "reason": "missing token"}
            if args.json:
                args.json.parent.mkdir(parents=True, exist_ok=True)
                args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return 1
        roster = {
            "members": roster_data.get("members", []),
        }
        results = dispatch(repo, tasks, roster, token, dry_run=args.dry_run)
        print("\n派工结果" + ("（dry-run，未写入）" if args.dry_run else "") + "：")
        for record in results:
            print(f"  #{record['number']}  {record['status']:<14} {record['detail']}  {record['title']}")
        report["dispatch"] = {"performed": not args.dry_run, "results": results}

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
