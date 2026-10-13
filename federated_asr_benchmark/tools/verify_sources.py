from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def safe_source_path(data_root: Path, path_text: str) -> Path:
    if "\\" in path_text:
        raise RuntimeError(f"source path is not canonical POSIX form: {path_text}")
    relative = Path(path_text)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise RuntimeError(f"unsafe source path: {path_text}")
    path = data_root / relative
    if path.resolve() != path.absolute() or path.is_symlink():
        raise RuntimeError(f"source path uses a symlink or escape: {path_text}")
    return path


def verify_sources(data_root: Path, manifest_path: Path) -> dict[str, Any]:
    data_root = data_root.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    locks = manifest.get("sources")
    if not isinstance(locks, dict) or not locks:
        raise RuntimeError("candidate manifest has no source entries")
    verified: dict[str, Any] = {}
    for name, lock in sorted(locks.items()):
        path = safe_source_path(data_root, str(lock.get("path") or ""))
        if "glob" not in lock:
            if not path.is_file():
                raise RuntimeError(f"source file is missing: {name}: {path}")
            verified[name] = {"type": "file"}
        else:
            glob_pattern = lock.get("glob")
            if not isinstance(glob_pattern, str) or not glob_pattern:
                raise RuntimeError(f"tree source has no glob: {name}")
            if not path.is_dir():
                raise RuntimeError(f"source tree is missing: {name}: {path}")
            paths = sorted(candidate for candidate in path.glob(glob_pattern) if candidate.is_file())
            if any(candidate.is_symlink() for candidate in paths):
                raise RuntimeError(f"source tree contains a symlink: {name}")
            if not paths:
                raise RuntimeError(f"source tree is empty: {name}")
            verified[name] = {
                "type": "tree",
                "file_count": len(paths),
            }
    return {"status": "passed", "source_count": len(verified), "sources": verified}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that external benchmark sources are available.")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    result = verify_sources(args.data_root, args.manifest)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
