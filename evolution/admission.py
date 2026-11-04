"""Evidence-based gate evaluation; no executable code is accepted from candidates."""
from __future__ import annotations

import re
from orchestrator.registry import content_hash
from .feedback_view import scan_canaries
from .transforms import validate_transform
from .contracts import validate_contract
from .partitions import GenerationScope

GATES = ("source_and_license", "replay", "benign_solvability", "private_oracle", "deduplication", "diversity", "holdout_isolation", "review")


def gate(status, code, **evidence):
    return dict(status=status, reason_code=code, evidence=evidence)


def shingles(text, n=3):
    tokens = re.findall(r"[a-z0-9]+|[^\W\d_]", text.casefold(), re.UNICODE)
    if len(tokens) < n:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i:i+n]) for i in range(len(tokens)-n+1)}


def similarity(left, right):
    a,b = shingles(left), shingles(right)
    return len(a & b) / len(a | b) if a | b else 1.0


def evaluate(candidate: dict, evidence: dict, policy: dict, *, canaries: list[str], existing_texts: list[str],
             holdout_families: set[str], admitted_counts: dict[str, int], scope: GenerationScope, mode="formal") -> dict:
    if mode not in {"formal", "fixture"}:
        raise ValueError("Unknown admission mode")
    validate_contract("AdmissionPolicy", policy)
    validate_contract("TestCandidate", candidate)
    partition_error = None
    try:
        scope.authorize(candidate["parent_task"], family=candidate["parent_family_id"])
        if candidate["partition_manifest_hash"] != scope.manifest_hash:
            raise ValueError("admission_partition_manifest_mismatch")
    except ValueError as exc:
        partition_error = str(exc)
    policy_hash, candidate_hash = content_hash(policy), content_hash(candidate)
    checks = {}
    transform = validate_transform(candidate["parent_task"], candidate["public_task"], candidate["transform_spec"], policy["transform"])
    scans = scan_canaries({k:v for k,v in candidate.items() if k != "parent_task"}, canaries)
    if scans["status"] == "fail":
        transform = gate("fail", "private_canary_detected", scan=scans)
    # Evidence is supplied by the isolated evaluator/reviewer, never by the generator.
    bound = evidence.get("candidate_hash") == candidate_hash and evidence.get("policy_hash") == policy_hash
    real = evidence.get("kind") == "real"
    if not bound or (mode == "formal" and not real):
        code = "unbound_evidence" if not bound else "real_evidence_required"
        checks = {name: gate("pending", code) for name in GATES}
    else:
        source = evidence.get("source_and_license", {})
        checks["source_and_license"] = gate("pass" if source.get("approved") is True and source.get("evidence_ref") else "pending", "source_review", **source)
        if source.get("approved") is False:
            checks["source_and_license"] = gate("fail", "source_or_license_rejected", **source)
        replay = evidence.get("replay", {})
        valid = replay.get("valid_attempts", 0)
        checks["replay"] = gate("pass" if type(valid) is int and valid >= policy["minimum_valid_replays"] and replay.get("evidence_refs") else "pending", "replay_evidence", **replay)
        benign = evidence.get("benign_solvability", {})
        checks["benign_solvability"] = gate("pass" if benign.get("baseline_success") is True and benign.get("candidate_success") is True and benign.get("completion_rule_id") and benign.get("evidence_ref") else "pending", "benign_completion_evidence", **benign)
        if benign.get("baseline_success") is False or benign.get("candidate_success") is False:
            checks["benign_solvability"] = gate("fail", "benign_completion_failed", **benign)
        oracle = evidence.get("private_oracle", {})
        checks["private_oracle"] = gate("pending" if oracle.get("changed") is True else "pass" if oracle.get("unchanged") is True and oracle.get("rule_id") and oracle.get("evidence_ref") else "pending", "oracle_change_review" if oracle.get("changed") else "private_rule_evidence", **oracle)
        maximum = max((similarity(candidate["public_task"]["user_goal"], text) for text in existing_texts), default=0)
        checks["deduplication"] = gate("fail" if maximum >= policy["dedup_threshold"] else "pass", "ngram_jaccard", maximum=maximum, threshold=policy["dedup_threshold"])
        family = candidate["parent_family_id"]
        quota = policy["family_quotas"].get(family, 0)
        checks["diversity"] = gate("pass" if admitted_counts.get(family, 0) < quota else "pending", "family_quota", quota=quota, existing=admitted_counts.get(family, 0))
        checks["holdout_isolation"] = gate("fail" if family in holdout_families or candidate.get("partition") != "E" else scans["status"], "partition_and_canary", scan=scans)
        review = evidence.get("review", {})
        checks["review"] = gate("pass" if review.get("approved") is True and review.get("reviewer_id") and review.get("evidence_ref") else "pending", "independent_review", **review)
        if review.get("approved") is False:
            checks["review"] = gate("fail", "independent_review_rejected", **review)
    if candidate["parent_family_id"] in holdout_families or candidate.get("partition") != "E":
        checks["holdout_isolation"] = gate("fail", "holdout_partition_excluded")
    if partition_error:
        checks["holdout_isolation"] = gate("fail", partition_error)
    if mode == "formal" and all(g["status"] == "pass" for g in checks.values()):
        # A caller-controlled kind='real' flag is not runtime qualification.
        checks["review"] = gate("pending", "native_qualification_not_verified", prior_review=checks["review"])
    statuses = [transform["status"], *(g["status"] for g in checks.values())]
    state = "rejected" if "fail" in statuses else "admitted" if all(s == "pass" for s in statuses) else "pending_review"
    return dict(schema_version="admission_v1", candidate_hash=candidate_hash, policy_hash=policy_hash,
                partition_manifest_hash=scope.manifest_hash,
                mode=mode, formal_qualified=False, admitted=state == "admitted", status=state,
                transform=transform, gates=checks,
                qualification_note="Gate evidence still requires native source/runtime qualification; fixture admission is not formal evidence.")


def evaluate_batch(candidates, evidence_by_hash, policy, **kwargs):
    counts = dict(kwargs.pop("admitted_counts", {}))
    texts = list(kwargs.pop("existing_texts", []))
    results = []
    for candidate in sorted(candidates, key=content_hash):
        result = evaluate(candidate, evidence_by_hash.get(content_hash(candidate), {}), policy,
                          admitted_counts=counts, existing_texts=texts, **kwargs)
        results.append(result)
        if result["admitted"]:
            family = candidate["parent_family_id"]
            counts[family] = counts.get(family, 0) + 1
            texts.append(candidate["public_task"]["user_goal"])
    return results
