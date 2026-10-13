from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
from typing import Any, Iterable


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "0.2"
DEFAULT_INCLUDED_ROOTS = (
    "docs",
    "examples",
    "manifests",
    "product_eval",
    "release",
    "schemas",
    "selections",
    "tests",
    "tools",
)
DEFAULT_INCLUDED_FILES = (
    "README.md",
    "release.ps1",
    "requirements-agentpoison.in",
    "requirements-agentpoison.lock",
    "requirements-controller.lock",
    "benchmark_registry.json",
    "case_index.jsonl",
    "case_index_manifest.json",
    "product_registry.json",
)
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def _validate_relative(path_text: str) -> Path:
    if "\\" in path_text:
        raise RuntimeError(f"manifest path is not canonical POSIX form: {path_text}")
    path = Path(path_text)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise RuntimeError(f"unsafe manifest path: {path_text}")
    return path


def _included_file(path: Path, relative: Path) -> bool:
    return (
        path.is_file()
        and not path.is_symlink()
        and not any(part in EXCLUDED_PARTS for part in relative.parts)
        and path.suffix.lower() not in EXCLUDED_SUFFIXES
    )


def iter_release_files(
    root: Path,
    *,
    included_roots: Iterable[str] = DEFAULT_INCLUDED_ROOTS,
    included_files: Iterable[str] = DEFAULT_INCLUDED_FILES,
) -> list[tuple[str, Path]]:
    root = root.resolve()
    result: dict[str, Path] = {}
    for path_text in included_roots:
        relative_root = _validate_relative(path_text)
        source_root = root / relative_root
        if not source_root.is_dir() or source_root.is_symlink():
            raise RuntimeError(f"included release root is missing or unsafe: {path_text}")
        for path in source_root.rglob("*"):
            relative = path.relative_to(root)
            if _included_file(path, relative):
                result[relative.as_posix()] = path
            elif path.is_symlink():
                raise RuntimeError(f"release cannot contain symlink: {relative.as_posix()}")
    for path_text in included_files:
        relative = _validate_relative(path_text)
        path = root / relative
        if not _included_file(path, relative):
            raise RuntimeError(f"included release file is missing or unsafe: {path_text}")
        result[relative.as_posix()] = path
    return sorted(result.items())


def build_manifest(
    root: Path,
    *,
    release_id: str,
    included_roots: Iterable[str] = DEFAULT_INCLUDED_ROOTS,
    included_files: Iterable[str] = DEFAULT_INCLUDED_FILES,
) -> dict[str, Any]:
    roots = tuple(included_roots)
    files = tuple(included_files)
    entries = [
        {"path": relative}
        for relative, _ in iter_release_files(
            root, included_roots=roots, included_files=files
        )
    ]
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "release_id": release_id,
        "included_roots": list(roots),
        "included_files": list(files),
        "entries": entries,
    }
    return manifest


def write_manifest_atomic(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def verify_manifest(root: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") not in {SCHEMA_VERSION, "0.1"}:
        raise RuntimeError("unsupported release manifest schema")

    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise RuntimeError("release manifest entries must be a list")
    expected: dict[str, dict[str, Any]] = {}
    for entry in entries:
        relative = str(entry.get("path") or "")
        _validate_relative(relative)
        if relative in expected:
            raise RuntimeError(f"duplicate release manifest path: {relative}")
        expected[relative] = entry
    actual = dict(
        iter_release_files(
            root,
            included_roots=manifest.get("included_roots") or (),
            included_files=manifest.get("included_files") or (),
        )
    )
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    if missing or unexpected:
        raise RuntimeError(
            "release verification failed: "
            f"missing={missing[:10]}, unexpected={unexpected[:10]}"
        )
    return {
        "status": "passed",
        "release_id": manifest["release_id"],
        "file_count": len(expected),
    }


def export_release(root: Path, manifest_path: Path, destination: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = manifest_path.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise RuntimeError(f"release export destination already exists: {destination}")
    verified = verify_manifest(root, manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    temporary = destination.with_name(f"{destination.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise RuntimeError(f"release export temporary path already exists: {temporary}")
    try:
        temporary.mkdir(parents=True)
        for included_root in manifest["included_roots"]:
            (temporary / _validate_relative(included_root)).mkdir(parents=True, exist_ok=True)
        for entry in manifest["entries"]:
            relative = _validate_relative(entry["path"])
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / relative, target)
        shutil.copy2(manifest_path, temporary / "release-manifest.json")
        temporary.replace(destination)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    result = verify_manifest(destination, destination / "release-manifest.json")
    result["destination"] = str(destination)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or inspect a release file inventory.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--root", type=Path, default=PACKAGE_ROOT)
    build.add_argument("--release-id", required=True)
    build.add_argument("--output", required=True, type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--root", type=Path, default=PACKAGE_ROOT)
    verify.add_argument("--manifest", required=True, type=Path)
    export = subparsers.add_parser("export")
    export.add_argument("--root", type=Path, default=PACKAGE_ROOT)
    export.add_argument("--manifest", required=True, type=Path)
    export.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "build":
        manifest = build_manifest(args.root, release_id=args.release_id)
        write_manifest_atomic(args.output, manifest)
        output = {
            "status": "built",
            "release_id": manifest["release_id"],
            "file_count": len(manifest["entries"]),
        }
    elif args.command == "verify":
        output = verify_manifest(args.root.resolve(), args.manifest.resolve())
    else:
        output = export_release(
            args.root.resolve(), args.manifest.resolve(), args.destination
        )
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
