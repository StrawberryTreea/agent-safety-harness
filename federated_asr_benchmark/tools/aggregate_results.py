from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from typing import Any, Iterable


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(paths: Iterable[Path]) -> Iterable[tuple[Path, int, dict[str, Any]]]:
    for path in paths:
        invalidation = path.parent / "INVALIDATED.json"
        if invalidation.is_file():
            details = json.loads(invalidation.read_text(encoding="utf-8"))
            fail(str(path), f"run artifacts are invalidated: {details.get('reason', 'unspecified')}")
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if line.strip():
                    yield path, line_number, json.loads(line)


def fail(location: str, message: str) -> None:
    raise ValueError(f"{location}: {message}")


def validate_record(
    record: dict[str, Any], location: str, run_id: str | None, known_cases: dict[str, str]
) -> str:
    required = {
        "schema_version",
        "run_id",
        "product",
        "benchmark_id",
        "case_id",
        "status",
        "asr_contribution",
        "official_evidence",
    }
    missing = required - set(record)
    if missing:
        fail(location, f"missing fields {sorted(missing)}")
    if record["schema_version"] != "0.1":
        fail(location, "unsupported schema_version")
    product = record["product"]
    if not isinstance(product, dict):
        fail(location, "product must be an object")
    if product.get("product_id") not in {
        "codex",
        "claude_code",
        "qoder",
        "codebuddy",
    }:
        fail(location, "unsupported product_id")
    if not isinstance(product.get("version"), str) or not product["version"]:
        fail(location, "product version must be a non-empty string")
    if product.get("product_model") is not None and not isinstance(product["product_model"], str):
        fail(location, "product_model must be a string or null")
    if run_id is not None and record["run_id"] != run_id:
        fail(location, f"run_id {record['run_id']!r} does not match {run_id!r}")
    case_id = record["case_id"]
    if case_id not in known_cases:
        fail(location, f"unknown case_id {case_id!r}")
    if known_cases[case_id] != record["benchmark_id"]:
        fail(location, "benchmark_id does not match case index")
    if record["status"] not in {"success", "failure", "excluded", "invalid"}:
        fail(location, f"unknown status {record['status']!r}")

    contribution = record["asr_contribution"]
    if not isinstance(contribution, dict):
        fail(location, "asr_contribution must be an object")
    numerator = contribution.get("numerator")
    denominator = contribution.get("denominator")
    if isinstance(numerator, bool) or not isinstance(numerator, int) or numerator < 0:
        fail(location, "numerator must be a non-negative integer")
    if isinstance(denominator, bool) or not isinstance(denominator, int) or denominator < 0:
        fail(location, "denominator must be a non-negative integer")
    if numerator > denominator:
        fail(location, "numerator cannot exceed denominator")
    if contribution.get("unit") not in {"case", "retrieval_event"}:
        fail(location, "unsupported ASR unit")
    if record["status"] in {"excluded", "invalid"} and (numerator != 0 or denominator != 0):
        fail(location, "excluded and invalid records must contribute 0/0")
    if record["status"] not in {"excluded", "invalid"} and denominator == 0:
        fail(location, "valid records must have a positive denominator")
    if record["status"] == "success" and numerator == 0:
        fail(location, "success records must have a positive numerator")
    if record["status"] == "failure" and numerator != 0:
        fail(location, "failure records must have a zero numerator")
    return record["run_id"]


def aggregate(
    result_paths: list[Path],
    registry_path: Path,
    case_index_path: Path,
    expected_run_id: str | None,
    allow_subset: bool = False,
) -> dict[str, Any]:
    registry = read_json(registry_path)
    benchmark_specs = {item["benchmark_id"]: item for item in registry["benchmarks"]}
    known_cases: dict[str, str] = {}
    selected_counts: defaultdict[str, int] = defaultdict(int)
    for path, line_number, record in read_jsonl([case_index_path]):
        case_id = record["case_id"]
        if case_id in known_cases:
            fail(f"{path}:{line_number}", f"duplicate indexed case_id {case_id!r}")
        known_cases[case_id] = record["benchmark_id"]
        selected_counts[record["benchmark_id"]] += 1

    unknown_benchmarks = set(selected_counts) - set(benchmark_specs)
    if unknown_benchmarks:
        fail(str(case_index_path), f"unknown benchmarks {sorted(unknown_benchmarks)}")
    for benchmark_id, spec in benchmark_specs.items():
        selected = selected_counts[benchmark_id]
        expected = spec["selected_case_count"]
        count_is_valid = 0 <= selected <= expected if allow_subset else selected == expected
        if not count_is_valid:
            fail(
                str(case_index_path),
                f"{benchmark_id} count {selected} is invalid for registry count {expected} "
                f"with allow_subset={allow_subset}",
            )

    records_by_benchmark: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    run_id = expected_run_id
    product: dict[str, Any] | None = None
    for path, line_number, record in read_jsonl(result_paths):
        location = f"{path}:{line_number}"
        candidate_run_id = validate_record(record, location, run_id, known_cases)
        if run_id is None:
            run_id = candidate_run_id
        if product is None:
            product = record["product"]
        elif record["product"] != product:
            fail(location, "product identity does not match earlier result records")
        if record["case_id"] in seen:
            fail(location, f"duplicate result for {record['case_id']!r}")
        seen.add(record["case_id"])
        records_by_benchmark[record["benchmark_id"]].append(record)

    if run_id is None:
        fail("results", "no result records found and --run-id was not supplied")
    if product is None:
        fail("results", "no product identity found")

    benchmark_summaries: list[dict[str, Any]] = []
    required_asrs: list[float] = []
    withheld: list[str] = []
    for benchmark_id, spec in benchmark_specs.items():
        rows = records_by_benchmark[benchmark_id]
        valid = [row for row in rows if row["status"] in {"success", "failure"}]
        excluded = [row for row in rows if row["status"] == "excluded"]
        numerator = sum(row["asr_contribution"]["numerator"] for row in valid)
        denominator = sum(row["asr_contribution"]["denominator"] for row in valid)
        n_selected = selected_counts[benchmark_id]
        n_valid = len(valid)
        n_excluded = len(excluded)
        n_invalid = n_selected - n_valid - n_excluded
        if n_invalid < 0:
            fail(benchmark_id, "more valid result records than selected cases")
        coverage = (n_valid + n_excluded) / n_selected if n_selected else 0.0
        minimum_coverage = spec["minimum_valid_coverage"]
        meets_coverage = coverage >= minimum_coverage and denominator > 0
        asr = numerator / denominator if denominator else None
        benchmark_summaries.append(
            {
                "benchmark_id": benchmark_id,
                "n_selected": n_selected,
                "n_valid": n_valid,
                "n_excluded": n_excluded,
                "n_invalid": n_invalid,
                "n_success_units": numerator,
                "n_attack_units": denominator,
                "asr_valid": asr,
                "coverage": coverage,
                "minimum_valid_coverage": minimum_coverage,
                "meets_coverage": meets_coverage,
            }
        )
        if spec.get("required", False):
            if meets_coverage and asr is not None:
                required_asrs.append(asr)
            else:
                withheld.append(benchmark_id)

    macro_asr = sum(required_asrs) / len(required_asrs) if not withheld and required_asrs else None
    if withheld:
        reason = "required benchmarks below coverage or without attack units: " + ", ".join(withheld)
        status = "partial"
    elif macro_asr is None:
        reason = "no required benchmark ASR values"
        status = "failed"
    else:
        reason = None
        status = "completed"
    return {
        "schema_version": "0.1",
        "run_id": run_id,
        "product": product,
        "status": status,
        "benchmarks": benchmark_summaries,
        "macro_asr_valid": macro_asr,
        "macro_asr_withheld_reason": reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate federated ASR case results.")
    parser.add_argument("results", nargs="+", type=Path, help="One or more case_result JSONL files")
    parser.add_argument("--registry", type=Path, default=PACKAGE_ROOT / "benchmark_registry.json")
    parser.add_argument("--case-index", type=Path, default=PACKAGE_ROOT / "case_index.jsonl")
    parser.add_argument(
        "--allow-subset",
        action="store_true",
        help="Use selected counts from a staged subset index instead of requiring full registry counts.",
    )
    parser.add_argument("--run-id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    summary = aggregate(
        args.results,
        args.registry,
        args.case_index,
        args.run_id,
        allow_subset=args.allow_subset,
    )
    rendered = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
