"""Append-only, self-verifying test versions; fixture versions are visibly marked."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid
from datetime import datetime, timezone
from orchestrator.registry import canonical_json, content_hash
from .admission import GATES
from .contracts import validate_contract


def publish(root: Path, candidates: list[dict], decisions: list[dict], *, parent_version: str | None,
            policy_hash: str, seed: int, kind: str) -> dict:
    if kind not in {"fixture", "formal"} or type(seed) is not int:
        raise ValueError("Invalid dataset kind or seed")
    if kind == "formal":
        raise ValueError("Formal dataset publication requires a registered native qualification backend")
    indexed = {d["candidate_hash"]: d for d in decisions}
    if len(indexed) != len(decisions):
        raise ValueError("Duplicate admission decisions")
    candidate_hashes = [content_hash(c) for c in candidates]
    if len(set(candidate_hashes)) != len(candidates) or set(indexed) != set(candidate_hashes):
        raise ValueError("Candidates and admission decisions must match one to one")
    for candidate in candidates:
        validate_contract("TestCandidate", candidate)
    if len({c["candidate_id"] for c in candidates}) != len(candidates):
        raise ValueError("Candidate IDs must be unique")
    admitted = []
    for candidate in candidates:
        result = indexed[content_hash(candidate)]
        if result.get("policy_hash") != policy_hash:
            raise ValueError("Admission policy mismatch")
        if result.get("admitted") is not True:
            continue
        if result.get("partition_manifest_hash") != candidate["partition_manifest_hash"]:
            raise ValueError("Admission partition manifest mismatch")
        if result.get("status") != "admitted":
            raise ValueError("Conflicting admission status")
        if result.get("mode") != ("formal" if kind == "formal" else "fixture"):
            raise ValueError("Cannot publish fixture evidence into a formal dataset")
        if result.get("transform", {}).get("status") != "pass" or set(result.get("gates", {})) != set(GATES) or any(g.get("status") != "pass" for g in result["gates"].values()):
            raise ValueError("Incomplete admission gates")
        if kind == "formal" and result.get("formal_qualified") is not True:
            raise ValueError("Native formal qualification required")
        admitted.append(candidate)
    admitted.sort(key=content_hash)
    payload = dict(schema_version="dataset_version_v1", kind=kind, parent_version=parent_version,
                   admission_policy_hash=policy_hash, seed=seed, candidate_hashes=[content_hash(c) for c in admitted])
    version = content_hash(payload)
    destination = root / version
    frozen_at = (json.loads((destination / "manifest.json").read_text(encoding="utf-8"))["frozen_at"]
                 if destination.exists() else datetime.now(timezone.utc).isoformat())
    manifest = {**payload, "version": version, "admitted_count": len(admitted),
                "diff": {"added_candidate_ids": [c["candidate_id"] for c in admitted]},
                "frozen_at": frozen_at, "note": "Content-addressed immutable snapshot; no formal claims for fixtures"}
    validate_contract("DatasetVersion", manifest)
    root.mkdir(parents=True, exist_ok=True)
    expected = canonical_json(manifest) + "\n"
    serialized = "".join(canonical_json(c) + "\n" for c in admitted)
    if destination.exists():
        if (destination / "manifest.json").read_text(encoding="utf-8") != expected or (destination / "candidates.jsonl").read_text(encoding="utf-8") != serialized:
            raise ValueError("Dataset version has been modified")
        return manifest
    # Build completely before publishing. The unique staging directory remains evidence on failure.
    stage = root / (".staging-" + uuid.uuid4().hex)
    stage.mkdir()
    (stage / "manifest.json").write_text(expected, encoding="utf-8", newline="\n")
    (stage / "candidates.jsonl").write_text(serialized, encoding="utf-8", newline="\n")
    try:
        os.rename(stage, destination)
    except FileExistsError:
        # Another publisher won the race: verify it instead of overwriting it.
        if (destination / "manifest.json").read_text(encoding="utf-8") != expected or (destination / "candidates.jsonl").read_text(encoding="utf-8") != serialized:
            raise ValueError("Conflicting dataset publication")
    return manifest
