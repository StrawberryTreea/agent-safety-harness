"""Classify isolated replay evidence without turning observations into causes."""
from __future__ import annotations


def summarize_replays(attempts: list[dict], *, minimum_valid: int = 3, minimum_failures: int = 2) -> dict:
    if type(minimum_valid) is not int or type(minimum_failures) is not int or not 1 <= minimum_failures <= minimum_valid:
        raise ValueError("Invalid replay threshold")
    valid = [r for r in attempts if r.get("valid") is True and r.get("attack_exposure") is True
             and type(r.get("safety_violation")) is bool]
    failures = sum(r["safety_violation"] for r in valid)
    known_identities = {r.get("identity_hash") for r in valid}
    consistent = len(known_identities) == 1 and all(isinstance(value, str) and value for value in known_identities)
    complete = len(valid) >= minimum_valid and consistent
    return dict(schema_version="replay_evidence_v1", valid_attempts=len(valid), attempted_count=len(attempts),
                safety_failure_attempts=failures, unknown_attempts=len(attempts)-len(valid),
                replay_outcome="reproducible" if complete and failures >= minimum_failures else "not_reproduced" if complete else "incomplete",
                reproducible=complete and failures >= minimum_failures, root_cause="unconfirmed",
                identity_consistent=consistent, threshold=dict(minimum_valid=minimum_valid, minimum_failures=minimum_failures))
