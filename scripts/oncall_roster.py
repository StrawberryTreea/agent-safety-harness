#!/usr/bin/env python3
"""On-call roster: detect inactive members and hand work over to their backup.

Reads docs/roster.json (the single source of truth for members, backups and
thresholds), queries the GitHub REST API for each member's latest activity in
this repository, classifies every member, and produces a handover report.

Two modes:
  --report   (default) write the report as JSON and Markdown, change nothing
  --apply    additionally reassign the open issues of an escalated member to
             their backup and refresh the on-call board issue

Only network calls need a token (GITHUB_TOKEN / GH_TOKEN, or `gh auth token`).
The decision logic is pure and covered by tests/test_oncall_roster.py.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROSTER = ROOT / "docs" / "roster.json"
BOARD_TITLE = "在岗与代理看板（自动维护）"

STATUS_ACTIVE = "active"
STATUS_WARN = "warn"
STATUS_ESCALATE = "escalate"

STATUS_LABELS = {
    STATUS_ACTIVE: "在岗",
    STATUS_WARN: "预警（14 天以上无活动）",
    STATUS_ESCALATE: "需移交（30 天以上无活动）",
}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def load_roster(path: Path) -> Dict[str, Any]:
    return load_roster_from_data(json.loads(path.read_text(encoding="utf-8")))


def load_roster_from_data(data: Dict[str, Any]) -> Dict[str, Any]:
    members = data.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError("'members' must be a non-empty list")
    ids = [str(member.get("id", "")) for member in members]
    if len(set(ids)) != len(ids):
        raise ValueError("member ids must be unique")
    thresholds = data.get("thresholds_days") or {}
    return {
        "repo": str(data.get("repo") or "StrawberryTreea/agent-safety-harness"),
        "arbiter": str(data.get("arbiter") or ""),
        "warn_days": int(thresholds.get("warn", 14)),
        "escalate_days": int(thresholds.get("escalate", 30)),
        "members": members,
    }


def member_id_map(roster: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(member["id"]): member for member in roster["members"]}


def classify(inactive_days: Optional[int], warn_days: int, escalate_days: int) -> str:
    """Map days since last activity to a status. None means no activity was found."""
    if inactive_days is None:
        return STATUS_ESCALATE
    if inactive_days >= escalate_days:
        return STATUS_ESCALATE
    if inactive_days >= warn_days:
        return STATUS_WARN
    return STATUS_ACTIVE


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def latest_timestamp(payloads: Iterable[Any]) -> Optional[datetime]:
    stamps: List[datetime] = []
    for payload in payloads:
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            for key in ("updated_at", "created_at", "commit_date", "date"):
                stamp = _parse_timestamp(item.get(key))
                if stamp is not None:
                    stamps.append(stamp)
                    break
    return max(stamps) if stamps else None


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


def api_get(path: str, token: Optional[str], params: Optional[Dict[str, Any]] = None) -> Any:
    url = f"https://api.github.com{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "agent-safety-harness-oncall"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def api_request(method: str, path: str, token: Optional[str], payload: Optional[Dict[str, Any]] = None) -> Any:
    url = f"https://api.github.com{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Accept": "application/vnd.github+json",
                                              "User-Agent": "agent-safety-harness-oncall",
                                              "Content-Type": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def member_activity(repo: str, login: str, token: Optional[str], since: str) -> Tuple[Optional[datetime], Dict[str, Any]]:
    """Latest timestamp across commits, issues and pull requests authored by the member.

    Only self-authored activity counts. Being mentioned or assigned an issue is not
    activity: an absent member still accumulates assigned issues, so counting those
    would keep an offline member looking present.
    """
    commits = api_get(f"/repos/{repo}/commits", token, {"author": login, "since": since, "per_page": 5})
    search = api_get("/search/issues", token, {
        "q": f"repo:{repo} author:{login}",
        "sort": "updated",
        "order": "desc",
        "per_page": 5,
    })
    issues = (search or {}).get("items") if isinstance(search, dict) else None
    stamp = latest_timestamp([commits or [], issues or []])
    detail = {
        "commits": len(commits) if isinstance(commits, list) else 0,
        "issues_or_prs": len(issues) if isinstance(issues, list) else 0,
    }
    return stamp, detail


def _joined_date(member: Dict[str, Any]) -> Optional[datetime]:
    stamp = _parse_timestamp(member.get("joined"))
    if stamp is None:
        return None
    return stamp.replace(hour=0, minute=0, second=0, microsecond=0)


def evaluate(roster: Dict[str, Any], activity: Dict[str, Optional[datetime]], now: datetime) -> List[Dict[str, Any]]:
    """Classify each member.

    A member with no recorded activity yet is measured from their join date, not
    treated as absent forever: someone who joined yesterday has not been away for
    30 days, they have simply not pushed yet.
    """
    rows: List[Dict[str, Any]] = []
    for member in roster["members"]:
        mid = str(member["id"])
        stamp = activity.get(mid)
        joined = _joined_date(member)
        if stamp is None:
            reference = joined
            inactive_days = None if reference is None else max(0, (now - reference).days)
            basis = "join-date" if reference is not None else "unknown"
        else:
            inactive_days = max(0, (now - stamp).days)
            basis = "activity"
        status = classify(inactive_days, roster["warn_days"], roster["escalate_days"])
        rows.append({
            "id": mid,
            "name": str(member.get("name", mid)),
            "github": str(member.get("github", "")),
            "role": str(member.get("role", "")),
            "owns": list(member.get("owns", [])),
            "backup": member.get("backup"),
            "backup_scope": str(member.get("backup_scope", "")),
            "joined": member.get("joined"),
            "last_active": stamp.isoformat() if stamp else None,
            "inactive_days": inactive_days,
            "inactive_basis": basis,
            "status": status,
        })
    return rows


def handover_actions(rows: Sequence[Dict[str, Any]], roster: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Concrete actions for members who crossed the escalation threshold."""
    ids = member_id_map(roster)
    actions: List[Dict[str, Any]] = []
    for row in rows:
        if row["status"] != STATUS_ESCALATE:
            continue
        backup = row.get("backup")
        if not backup or backup not in ids:
            actions.append({"member": row["id"], "action": "缺少代理配置", "detail": "docs/roster.json 需要补 backup 字段",
                            "automatic": False})
            continue
        if row["id"] == roster.get("arbiter"):
            actions.append({
                "member": row["id"],
                "action": "仲裁权移交",
                "detail": f"仲裁与口径裁决由 {ids[backup]['name']}（{backup}）代行；仓库权限与分支保护变更暂缓",
                "automatic": False,
            })
            continue
        actions.append({
            "member": row["id"],
            "action": "未完成工作移交",
            "detail": (f"名下 open issue 转给 {ids[backup]['name']}（{backup}）；"
                       f"CODEOWNERS 中 {row['github']} 的条目替换为 {ids[backup]['github']}；"
                       f"必要时移除 {row['github']} 的仓库权限"),
            "automatic": True,
        })
    return actions


def parse_codeowners(path: Path) -> List[Tuple[str, List[str]]]:
    """Parse .github/CODEOWNERS into (pattern, owners) pairs, ignoring comments."""
    rules: List[Tuple[str, List[str]]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        rules.append((parts[0], [part.lstrip("@") for part in parts[1:]]))
    return rules


def codeowners_coverage(codeowners_path: Path, roster: Dict[str, Any], min_owners: int = 3) -> List[Dict[str, Any]]:
    """Check every CODEOWNERS rule has enough owners to survive one absence.

    The branch protection requires a CODEOWNER approval and forbids approving your
    own last push, so a path needs at least three owners: the author cannot approve
    their own commit, and one absent member must not block the merge.
    """
    rules = parse_codeowners(codeowners_path)
    problems: List[Dict[str, Any]] = []
    for pattern, owners in rules:
        if len(owners) < min_owners:
            problems.append({
                "pattern": pattern,
                "owners": owners,
                "owner_count": len(owners),
                "required": min_owners,
                "reason": "owner 数量不足，任一人缺席或作者本人即无法完成评审",
            })
    known = {str(member.get("github", "")) for member in roster["members"]}
    for pattern, owners in rules:
        unknown = [owner for owner in owners if owner and owner not in known]
        if unknown:
            problems.append({
                "pattern": pattern,
                "owners": owners,
                "owner_count": len(owners),
                "required": min_owners,
                "reason": f"CODEOWNERS 里的账号不在 docs/roster.json 名册中：{', '.join(unknown)}",
            })
    return problems


def open_issues_for(repo: str, login: str, token: Optional[str]) -> List[Dict[str, Any]]:
    issues = api_get(f"/repos/{repo}/issues", token, {"assignee": login, "state": "open", "per_page": 50})
    return [issue for issue in (issues or []) if isinstance(issue, dict) and "pull_request" not in issue]


def reassign_issue(repo: str, number: int, backup_login: str, token: Optional[str], note: str) -> bool:
    try:
        api_request("PATCH", f"/repos/{repo}/issues/{number}", token, {"assignees": [backup_login]})
    except urllib.error.HTTPError as exc:
        print(f"reassign issue #{number} failed: {exc}", file=sys.stderr)
        return False
    api_request("POST", f"/repos/{repo}/issues/{number}/comments", token, {"body": note})
    return True


def find_board_issue(repo: str, token: Optional[str]) -> Optional[Dict[str, Any]]:
    issues = api_get("/search/issues", token, {"q": f"repo:{repo} in:title \"{BOARD_TITLE}\"", "per_page": 5})
    for item in (issues or {}).get("items", []):
        if isinstance(item, dict) and "pull_request" not in item:
            return item
    return None


def render_markdown(rows: Sequence[Dict[str, Any]], actions: Sequence[Dict[str, Any]], roster: Dict[str, Any],
                    generated_at: str) -> str:
    lines = [f"# 在岗与代理看板（自动维护）", "", f"生成时间：{generated_at}", "",
             f"判定阈值：无活动 {roster['warn_days']} 天进入预警，{roster['escalate_days']} 天进入需移交。", "",
             "| 成员 | 账号 | 职责 | 最后活动 | 已离线(天) | 状态 | 代理人 |",
             "|---|---|---|---|---|---|---|"]
    names = member_id_map(roster)
    for row in rows:
        backup = row.get("backup")
        backup_text = f"{names[backup]['name']}（{backup}）" if backup in names else "未配置"
        if row["last_active"]:
            last = row["last_active"]
        elif row.get("inactive_basis") == "join-date":
            last = f"自加入（{row.get('joined')}）起无提交"
        else:
            last = "无记录"
        days = "—" if row["inactive_days"] is None else str(row["inactive_days"])
        lines.append(f"| {row['name']} | @{row['github']} | {row['role']} | {last} | {days} | "
                     f"{STATUS_LABELS[row['status']]} | {backup_text} |")
    lines.append("")
    if actions:
        lines.append("## 需要处理的动作")
        lines.append("")
        for action in actions:
            marker = "自动" if action.get("automatic") else "手动"
            lines.append(f"- **{action['action']}**（{action['member']}，{marker}）：{action['detail']}")
    else:
        lines.append("## 需要处理的动作")
        lines.append("")
        lines.append("- 无。全部成员在阈值内。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, default=DEFAULT_ROSTER)
    parser.add_argument("--apply", action="store_true", help="reassign escalated issues and refresh the board issue")
    parser.add_argument("--token", help="GitHub token (default: GITHUB_TOKEN / GH_TOKEN / gh auth token)")
    parser.add_argument("--repo", help="owner/name override")
    parser.add_argument("--out", type=Path, help="directory for report files")
    parser.add_argument("--codeowners", type=Path, default=ROOT / ".github" / "CODEOWNERS")
    parser.add_argument("--min-owners", type=int, default=3, help="minimum owners per CODEOWNERS rule")
    parser.add_argument("--offline", action="store_true",
                        help="skip GitHub lookups; verify roster and CODEOWNERS coverage only")
    args = parser.parse_args()

    roster = load_roster(args.roster.resolve())
    if args.repo:
        roster["repo"] = args.repo
    coverage = codeowners_coverage(args.codeowners.resolve(), roster, args.min_owners)
    token = None if args.offline else resolve_token(args.token)
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=365)).isoformat()

    activity: Dict[str, Optional[datetime]] = {}
    for member in roster["members"]:
        login = str(member.get("github", ""))
        stamp: Optional[datetime] = None
        if login and not args.offline:
            try:
                stamp, _ = member_activity(roster["repo"], login, token, since)
            except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as exc:
                print(f"activity lookup failed for {login}: {exc}", file=sys.stderr)
        activity[str(member["id"])] = stamp

    rows = evaluate(roster, activity, now)
    actions = handover_actions(rows, roster)
    if coverage:
        actions.append({
            "member": "-",
            "action": "CODEOWNERS 覆盖度不足",
            "detail": "；".join(f"{item['pattern']}（{item['owner_count']}/{item['required']}）：{item['reason']}"
                                for item in coverage),
            "automatic": False,
        })
    generated_at = now.astimezone().isoformat(timespec="seconds")
    markdown = render_markdown(rows, actions, roster, generated_at)

    out = args.out.resolve() if args.out else ROOT / "runs" / ("oncall-" + now.strftime("%Y%m%dT%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "oncall_report.json").write_text(
        json.dumps({"generated_at": generated_at, "repo": roster["repo"], "members": rows, "actions": actions},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "oncall_report.md").write_text(markdown, encoding="utf-8")
    print(markdown)

    if args.apply:
        names = member_id_map(roster)
        for row in rows:
            if row["status"] != STATUS_ESCALATE or not row.get("backup"):
                continue
            backup = names[row["backup"]]
            if row["id"] == roster.get("arbiter"):
                continue
            for issue in open_issues_for(roster["repo"], row["github"], token):
                note = (f"值班移交：{row['name']}（@{row['github']}）已 {row['inactive_days']} 天无仓库活动，"
                        f"按 docs/roster.json 约定由 {backup['name']}（@{backup['github']}）接手。")
                if reassign_issue(roster["repo"], int(issue["number"]), backup["github"], token, note):
                    print(f"reassigned issue #{issue['number']} -> @{backup['github']}")
        board = find_board_issue(roster["repo"], token)
        if board:
            api_request("PATCH", f"/repos/{roster['repo']}/issues/{board['number']}", token, {"body": markdown})
            print(f"updated board issue #{board['number']}")
        else:
            created = api_request("POST", f"/repos/{roster['repo']}/issues", token, {
                "title": BOARD_TITLE, "body": markdown, "labels": ["type:chore", "track:infra"]})
            print(f"created board issue #{created.get('number')}")
    print(f"report: {out / 'oncall_report.md'}", file=sys.stderr)
    if coverage:
        print("CODEOWNERS coverage check failed:", file=sys.stderr)
        for item in coverage:
            print(f"  {item['pattern']} ({item['owner_count']}/{item['required']}): {item['reason']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
