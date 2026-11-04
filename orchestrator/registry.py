"""Content identities, independent of output paths, timestamps and credentials."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

IDENTITY_VERSION = "run_identity_v1"
TRACKS = {"llm_qa", "agent_execution", "agent_product", "trace_judge"}
_FIELDS = {"schema_version", "track", "inputs_hash", "effective_prompt_hash", "target_hash",
           "environment_hash", "scorer_hash", "dataset_version", "seed", "identity_hash"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_identity(*, track: str, inputs_hash: str, effective_prompt: Any, target: dict,
                  environment: dict, scorer: dict, dataset_version: str, seed: int) -> dict:
    if track not in TRACKS or type(seed) is not int:
        raise ValueError("Invalid track or seed")
    if not isinstance(inputs_hash, str) or len(inputs_hash) != 64 or any(c not in "0123456789abcdef" for c in inputs_hash):
        raise ValueError("inputs_hash must be a lowercase SHA-256 digest")
    if not isinstance(dataset_version, str) or not dataset_version.strip():
        raise ValueError("dataset_version is required")
    value = dict(schema_version=IDENTITY_VERSION, track=track, inputs_hash=inputs_hash,
                 effective_prompt_hash=content_hash(effective_prompt), target_hash=content_hash(target),
                 environment_hash=content_hash(environment), scorer_hash=content_hash(scorer),
                 dataset_version=dataset_version, seed=seed)
    return {**value, "identity_hash": content_hash(value)}


def validate_identity(identity: dict) -> None:
    if not isinstance(identity, dict) or set(identity) != _FIELDS:
        raise ValueError("Invalid RunIdentity fields")
    if identity["schema_version"] != IDENTITY_VERSION or identity["track"] not in TRACKS:
        raise ValueError("Unsupported RunIdentity version or track")
    if type(identity["seed"]) is not int or not isinstance(identity["dataset_version"], str) or not identity["dataset_version"].strip():
        raise ValueError("Invalid RunIdentity seed or dataset version")
    for name in ("inputs_hash", "effective_prompt_hash", "target_hash", "environment_hash", "scorer_hash", "identity_hash"):
        digest = identity[name]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"Invalid digest: {name}")
    payload = {k: v for k, v in identity.items() if k != "identity_hash"}
    if content_hash(payload) != identity["identity_hash"]:
        raise ValueError("Corrupt RunIdentity")


def bind_run(directory: Path, identity: dict, *, result_names: tuple[str, ...] = ("results.jsonl",)) -> None:
    """Refuse legacy results or changed identities; never overwrite an identity."""
    validate_identity(identity)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "run_identity.json"
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        validate_identity(previous)
        if previous != identity:
            raise ValueError("stale_identity: use a new run directory for changed inputs or configuration")
        return
    if any((directory / name).exists() and (directory / name).stat().st_size for name in result_names):
        raise ValueError("legacy_identity_missing: nonempty results require a new run directory")
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(identity) + "\n")
    except FileExistsError:
        # A concurrent creator must have bound the exact same identity.
        bind_run(directory, identity, result_names=result_names)
