#!/usr/bin/env python3
"""Keep docs/DOCUMENT_MAP.md honest.

Two failure modes this catches:
  1. The map lists a path that does not exist (renamed or deleted without updating the map).
  2. A tracked Markdown file is missing from the map (new document nobody was told about).

The map is the single index for humans and for coding tools, so a stale map is worse
than no map: it sends readers and agents to files that no longer exist, and silently
omits files that do.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "docs" / "DOCUMENT_MAP.md"

# Paths that are documentation but are deliberately not listed as documents.
EXEMPT = {
    "docs/DOCUMENT_MAP.md",  # the map itself
}

# Sections of the map that hold path tables.
PATH_PATTERN = re.compile(
    r"`(\.?[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:md|json|yaml|yml|py|html|ps1|jsonl))`"
)


def tracked_markdown() -> set[str]:
    """Markdown files under Git version control, as repo-relative posix paths."""
    result = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {result.stderr.strip()}")
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def listed_paths(map_text: str) -> set[str]:
    """Repository-relative paths referenced in the map's path tables."""
    found: set[str] = set()
    for raw in PATH_PATTERN.findall(map_text):
        candidate = raw.strip()
        if not candidate or candidate.startswith(("http", "#")):
            continue
        # Skip prose that merely mentions a directory or a pattern.
        if candidate.endswith("/") or "*" in candidate or candidate.startswith("~"):
            continue
        if " " in candidate:
            continue
        found.add(candidate)
    return found


def relative_label(path: Path) -> str:
    """Repo-relative label when possible, absolute otherwise."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def check(map_path: Path) -> dict:
    map_text = map_path.read_text(encoding="utf-8")
    listed = listed_paths(map_text)
    tracked = tracked_markdown()

    missing_files = sorted(p for p in listed if not (ROOT / p).exists())
    unlisted = sorted(p for p in tracked - listed if p not in EXEMPT)

    # A path mentioned in prose but not a real tracked document is fine as long as
    # the file exists; a mentioned path that does not exist is a broken pointer.
    return {
        "map": relative_label(map_path),
        "tracked_markdown": len(tracked),
        "listed_paths": len(listed),
        "missing_files": missing_files,
        "unlisted": unlisted,
        "status": "pass" if not missing_files and not unlisted else "fail",
    }


def render(result: dict) -> str:
    lines = [
        f"document map: {result['map']}",
        f"  tracked markdown : {result['tracked_markdown']}",
        f"  listed in map    : {result['listed_paths']}",
        f"  status           : {result['status'].upper()}",
    ]
    if result["missing_files"]:
        lines.append("  listed but NOT on disk:")
        lines.extend(f"    - {path}" for path in result["missing_files"])
    if result["unlisted"]:
        lines.append("  on disk but NOT in map:")
        lines.extend(f"    - {path}" for path in result["unlisted"])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", type=Path, default=MAP)
    parser.add_argument("--json", type=Path, help="write the result as JSON")
    args = parser.parse_args()

    map_path = args.map.resolve()
    if not map_path.exists():
        print(f"document map not found: {map_path}", file=sys.stderr)
        return 1

    result = check(map_path)
    print(render(result))

    if args.json:
        import json

        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
