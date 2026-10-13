from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
AGENT_SAFETYBENCH_REVISION = "74feea8de601b3a1449a93fcf70017fe61556f73"


def main() -> int:
    parser = argparse.ArgumentParser(description="Vendor pinned official benchmark runtimes.")
    parser.add_argument("--source", type=Path, default=ROOT / "runs" / "official_repo_probe")
    parser.add_argument(
        "--target",
        type=Path,
        default=ROOT / "official_benchmarks" / "agent_safetybench",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    source = args.source.resolve()
    target = args.target.resolve()
    revision = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
    ).stdout.strip()
    if revision != AGENT_SAFETYBENCH_REVISION:
        raise RuntimeError(f"Unexpected Agent-SafetyBench revision: {revision}")
    if target.exists():
        if not args.overwrite:
            raise FileExistsError(f"Target exists: {target}. Use --overwrite.")
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for filename in ("LICENSE", "README.md", "requirements.txt"):
        shutil.copy2(source / filename, target / filename)
    for dirname in ("data", "environments", "evaluation", "score"):
        shutil.copytree(
            source / dirname,
            target / dirname,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "evaluation_results", "shield_results"),
        )

    files = [
        path.relative_to(target).as_posix()
        for path in sorted(target.rglob("*"))
        if path.is_file() and path.name != "SOURCE_MANIFEST.json"
    ]
    manifest = {
        "benchmark": "Agent-SafetyBench",
        "upstream": "https://github.com/thu-coai/Agent-SafetyBench",
        "revision": revision,
        "files": files,
    }
    (target / "SOURCE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Vendored Agent-SafetyBench {revision} ({len(files)} files) to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
