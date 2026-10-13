from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = "product-federated-asr-v0.1-stage-selection"
STAGES = (("smoke20", 20), ("pilot100", 100))
STRATIFICATION_FIELDS = {
    "injecagent": ("attack_track", "attack_type"),
    "msb": ("agent_name", "attack_type"),
    "agentdojo": ("suite",),
    "agentleak": ("vertical", "attack_class"),
    "saber": ("scenario", "category"),
    "agentpoison_strategyqa": (),
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def stable_rank(seed: str, stage: str, case_id: str) -> str:
    value = f"{seed}\0{stage}\0{case_id}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def dimension_keys(row: dict[str, Any], fields: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    metadata = row.get("metadata", {})
    keys = [(field, str(metadata.get(field, "<missing>"))) for field in fields]
    if len(fields) > 1:
        joint = "|".join(f"{field}={metadata.get(field, '<missing>')}" for field in fields)
        keys.append(("__joint__", joint))
    return tuple(keys)


def balanced_sample(
    rows: Iterable[dict[str, Any]],
    size: int,
    fields: tuple[str, ...],
    seed: str,
    stage: str,
) -> list[dict[str, Any]]:
    pool = list(rows)
    if size > len(pool):
        raise ValueError(f"Cannot select {size} rows from a pool of {len(pool)}")
    if not fields:
        return sorted(pool, key=lambda row: stable_rank(seed, stage, row["case_id"]))[:size]

    keys_by_id = {row["case_id"]: dimension_keys(row, fields) for row in pool}
    population = Counter(key for keys in keys_by_id.values() for key in keys)
    desired = {key: size * count / len(pool) for key, count in population.items()}
    selected_counts: Counter[tuple[str, str]] = Counter()
    remaining = list(pool)
    selected = []

    for _ in range(size):
        def score(row: dict[str, Any]) -> tuple[float, str]:
            total = 0.0
            for key in keys_by_id[row["case_id"]]:
                weight = 0.25 if key[0] == "__joint__" else 1.0
                target = desired[key]
                total += weight * (target - selected_counts[key]) / max(target, 1.0)
            return (-total, stable_rank(seed, stage, row["case_id"]))

        chosen = min(remaining, key=score)
        selected.append(chosen)
        for key in keys_by_id[chosen["case_id"]]:
            selected_counts[key] += 1
        remaining.remove(chosen)
    return selected


def marginal_counts(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for field in fields:
        counts = Counter(str(row.get("metadata", {}).get(field, "<missing>")) for row in rows)
        result[field] = dict(sorted(counts.items()))
    return result


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build_selections(index_path: Path, output_root: Path, seed: str) -> dict[str, Any]:
    rows = read_jsonl(index_path)
    by_benchmark: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_benchmark[row["benchmark_id"]].append(row)
    if set(by_benchmark) != set(STRATIFICATION_FIELDS):
        raise ValueError(
            f"Index benchmarks {sorted(by_benchmark)} do not match selection policy "
            f"{sorted(STRATIFICATION_FIELDS)}"
        )

    manifest: dict[str, Any] = {
        "schema_version": "0.1",
        "dataset_id": "product-federated-asr-v0.1",
        "selection_version": "v0.1",
        "seed": seed,
        "policy": (
            "deterministic marginal balancing with a low-weight joint-stratum diversity term; "
            "pilot is selected from the pool remaining after smoke"
        ),
        "shared_across_products": True,
        "stages_are_disjoint": True,
        "source_index": str(index_path.resolve()),
        "benchmarks": {},
    }

    combined_by_stage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for benchmark_id in STRATIFICATION_FIELDS:
        fields = STRATIFICATION_FIELDS[benchmark_id]
        available = by_benchmark[benchmark_id]
        excluded: set[str] = set()
        stage_manifest: dict[str, Any] = {}
        for stage, size in STAGES:
            pool = [row for row in available if row["case_id"] not in excluded]
            selected = balanced_sample(pool, size, fields, seed, stage)
            selected_ids = {row["case_id"] for row in selected}
            if selected_ids & excluded:
                raise AssertionError(f"Overlapping stage selection for {benchmark_id}")
            excluded.update(selected_ids)
            output = output_root / stage / f"{benchmark_id}.jsonl"
            write_jsonl(output, selected)
            combined_by_stage[stage].extend(selected)
            stage_manifest[stage] = {
                "path": str(output.relative_to(output_root)),
                "case_count": len(selected),
                "marginal_counts": marginal_counts(selected, fields),
            }
        manifest["benchmarks"][benchmark_id] = {
            "population_count": len(available),
            "stratification_fields": list(fields),
            "stages": stage_manifest,
        }

    manifest["combined_stage_indexes"] = {}
    for stage, _ in STAGES:
        output = output_root / stage / "all.jsonl"
        write_jsonl(output, combined_by_stage[stage])
        manifest["combined_stage_indexes"][stage] = {
            "path": str(output.relative_to(output_root)),
            "case_count": len(combined_by_stage[stage]),
        }

    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build fixed smoke and pilot case selections.")
    parser.add_argument("--case-index", type=Path, default=PACKAGE_ROOT / "case_index.jsonl")
    parser.add_argument("--output-root", type=Path, default=PACKAGE_ROOT / "selections" / "v0.1")
    parser.add_argument("--seed", default=DEFAULT_SEED)
    args = parser.parse_args()
    manifest = build_selections(args.case_index.resolve(), args.output_root.resolve(), args.seed)
    summary = {
        benchmark_id: {
            stage: data["case_count"]
            for stage, data in benchmark["stages"].items()
        }
        for benchmark_id, benchmark in manifest["benchmarks"].items()
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
