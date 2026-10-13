from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = (
    "injecagent",
    "msb",
    "agentdojo",
    "agentleak",
    "saber",
    "agentpoison_strategyqa",
)
EXPECTED_POPULATION = 5384
EXPECTED_COMPLETED = 1508
EXPECTED_COMPLEMENT = 3876


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def build_complement(
    population_path: Path,
    completed_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    population = read_jsonl(population_path)
    completed = read_jsonl(completed_path)
    population_ids = [row["case_id"] for row in population]
    completed_ids = [row["case_id"] for row in completed]
    if len(population_ids) != len(set(population_ids)):
        raise RuntimeError("existing-six population contains duplicate case IDs")
    if len(completed_ids) != len(set(completed_ids)):
        raise RuntimeError("cumulative28 contains duplicate case IDs")
    unknown = sorted(set(completed_ids) - set(population_ids))
    if unknown:
        raise RuntimeError(f"cumulative28 is not a population subset: {unknown[:10]}")
    completed_set = set(completed_ids)
    complement = [row for row in population if row["case_id"] not in completed_set]
    complement_ids = {row["case_id"] for row in complement}
    if completed_set & complement_ids:
        raise RuntimeError("cumulative28 and complement overlap")
    if completed_set | complement_ids != set(population_ids):
        raise RuntimeError("cumulative28 and complement do not cover the population")
    if (
        len(population) != EXPECTED_POPULATION
        or len(completed) != EXPECTED_COMPLETED
        or len(complement) != EXPECTED_COMPLEMENT
    ):
        raise RuntimeError(
            "existing-six lattice drifted: "
            f"population={len(population)}, cumulative28={len(completed)}, "
            f"complement={len(complement)}"
        )
    population_counts = Counter(row["benchmark_id"] for row in population)
    completed_counts = Counter(row["benchmark_id"] for row in completed)
    complement_counts = Counter(row["benchmark_id"] for row in complement)
    if set(population_counts) != set(BENCHMARKS):
        raise RuntimeError(f"existing-six benchmark set drifted: {sorted(population_counts)}")
    for benchmark in BENCHMARKS:
        if completed_counts[benchmark] + complement_counts[benchmark] != population_counts[benchmark]:
            raise RuntimeError(f"benchmark complement count drifted: {benchmark}")
    manifest = {
        "schema_version": "0.3",
        "selection_id": "existing-six-population-minus-cumulative28",
        "source_population": population_path.name,
        "completed_selection": completed_path.as_posix(),
        "population_count": len(population),
        "completed_count": len(completed),
        "complement_count": len(complement),
        "counts": {
            benchmark: {
                "population": population_counts[benchmark],
                "completed": completed_counts[benchmark],
                "complement": complement_counts[benchmark],
            }
            for benchmark in BENCHMARKS
        },
        "proof": {
            "case_ids_unique": True,
            "completed_is_population_subset": True,
            "completed_and_complement_disjoint": True,
            "completed_union_complement_equals_population": True,
            "order": "source case_index order",
        },
    }
    return complement, manifest


def write_selection(
    output_root: Path,
    rows: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["benchmark_id"]].append(row)
    for benchmark in BENCHMARKS:
        path = output_root / f"{benchmark}.jsonl"
        write_jsonl_atomic(path, grouped[benchmark])
    all_path = output_root / "all.jsonl"
    write_jsonl_atomic(all_path, rows)
    write_json_atomic(output_root / "manifest.json", manifest)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze the existing-six population minus cumulative28."
    )
    parser.add_argument("--population", type=Path, default=PACKAGE_ROOT / "case_index.jsonl")
    parser.add_argument(
        "--completed",
        type=Path,
        default=PACKAGE_ROOT / "selections" / "v0.2" / "cumulative28" / "all.jsonl",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PACKAGE_ROOT / "selections" / "v0.3-existing-six-complement",
    )
    args = parser.parse_args()
    rows, manifest = build_complement(args.population.resolve(), args.completed.resolve())
    write_selection(args.output_root.resolve(), rows, manifest)
    print(
        json.dumps(
            {
                "selection_id": manifest["selection_id"],
                "complement_count": len(rows),
                "counts": {
                    benchmark: manifest["counts"][benchmark]["complement"]
                    for benchmark in BENCHMARKS
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
