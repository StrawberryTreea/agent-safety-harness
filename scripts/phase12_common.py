#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

try:
    import yaml
except ImportError as exc:  # pragma: no cover - deployment guard
    raise SystemExit("PyYAML is required. Install it with: python -m pip install -r requirements.txt") from exc


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from protocol_manager import load_protocol, validate_protocol_dir  # noqa: E402

TEXT_EXTENSIONS = {".jsonl", ".json", ".csv", ".tsv", ".txt", ".md", ".html", ".htm", ".yaml", ".yml"}
DATA_EXTENSIONS = {".jsonl", ".json", ".csv", ".tsv"}
BENCHMARK_GLOBS = ("**/*.jsonl", "**/*.json", "**/*.csv", "**/*.tsv")
EVIDENCE_EXTENSIONS = {".html", ".htm", ".md", ".pdf", ".txt"}
AUTO_FILL_FIELDS = frozenset({"id", "dataset_name", "source_id"})

# canonical field -> candidate raw field names, highest priority first
FIELD_ALIASES: Dict[str, List[str]] = {
    "id": ["id", "sample_id", "record_id", "uid", "example_id"],
    "dataset_name": ["dataset_name", "dataset", "source_dataset", "benchmark"],
    "source_id": ["source_id", "original_id", "orig_id", "sample_id"],
    "test_text": [
        "test_text",
        "prompt",
        "question",
        "query",
        "input",
        "text",
        "instruction",
        "raw_question",
        "raw_query",
        "raw_test_text",
        "raw_input",
        "raw_prompt",
        "benign_prompt",
        "redteam_query",
        "redacted_query",
    ],
    "reference_answer": [
        "reference_answer",
        "label",
        "answer",
        "target",
        "ref",
        "gold",
        "expected_answer",
    ],
    "expected_behavior": ["expected_behavior", "behavior", "expected"],
    "l1_category": ["l1_category", "category_l1"],
    "l2_category": ["l2_category", "category_l2"],
    "eval_method": ["eval_method", "evaluation_method", "eval_type"],
    "note": ["note", "notes", "comment", "remarks"],
    "type": ["type", "question_type", "task_type"],
    "safety_l1_dimension": ["safety_l1_dimension", "safety_l1", "l1_dimension", "l1", "harm_l1"],
    "safety_l2_harm_type": ["safety_l2_harm_type", "safety_l2", "l2_harm_type", "l2", "harm_l2", "harm_type"],
    "base_prompt": ["base_prompt"],
    "benign_prompt": ["benign_prompt"],
    "historical_target_response": ["historical_target_response"],
    "image_path": ["image_path"],
    "pii_units": ["pii_units"],
    "raw_choices": ["raw_choices", "choices"],
    "raw_history": ["raw_history", "history"],
    "raw_input": ["raw_input"],
    "raw_options": ["raw_options", "options"],
    "raw_post": ["raw_post"],
    "raw_prompt": ["raw_prompt"],
    "raw_query": ["raw_query"],
    "raw_question": ["raw_question"],
    "raw_test_text": ["raw_test_text"],
    "redacted_query": ["redacted_query"],
    "redteam_query": ["redteam_query"],
    "rubric": ["rubric"],
    "target_response": ["target_response"],
}


def configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


def load_phase_protocol(protocol_dir: Path) -> Dict[str, Dict[str, Any]]:
    validation = validate_protocol_dir(protocol_dir)
    if not validation["valid"]:
        details = "; ".join(f"{item['path']}: {item['message']}" for item in validation["errors"][:8])
        raise ValueError(f"Phase 0 protocol is invalid: {details}")
    return load_protocol(protocol_dir.resolve())


def _field_names_from_schema_section(section: Any) -> List[str]:
    if not isinstance(section, dict):
        return []
    fields = section.get("fields")
    if not isinstance(fields, list):
        return []
    names: List[str] = []
    for item in fields:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return names


def _field_types_from_schema_section(section: Any) -> Dict[str, str]:
    if not isinstance(section, dict) or not isinstance(section.get("fields"), list):
        return {}
    field_types: Dict[str, str] = {}
    for item in section["fields"]:
        if isinstance(item, str) and item.strip():
            field_types[item.strip()] = "str"
        elif isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                field_types[name.strip()] = normalize_text(item.get("type")).lower() or "str"
    return field_types


def load_canonical_schema(schema_path: Path) -> Dict[str, Any]:
    if not schema_path.is_file():
        raise FileNotFoundError(f"canonical schema does not exist: {schema_path}")
    if schema_path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError("canonical schema must be YAML")
    document = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("canonical schema must be a mapping")

    core_fields = _field_names_from_schema_section(document.get("core_schema"))
    extended_fields = _field_names_from_schema_section(document.get("extended_schema"))
    if not core_fields:
        raise ValueError("canonical schema core_schema.fields must be non-empty")

    core_required = bool((document.get("core_schema") or {}).get("required", True))
    required_fields = list(core_fields) if core_required else []
    all_fields = core_fields + [field for field in extended_fields if field not in core_fields]
    mapping_required_fields = [field for field in required_fields if field not in AUTO_FILL_FIELDS]
    field_types = _field_types_from_schema_section(document.get("core_schema"))
    field_types.update(_field_types_from_schema_section(document.get("extended_schema")))

    return {
        "schema_path": str(schema_path.resolve()),
        "schema_name": normalize_text(document.get("schema_name")) or schema_path.stem,
        "version": normalize_text(document.get("version")),
        "core_fields": core_fields,
        "extended_fields": extended_fields,
        "all_fields": all_fields,
        "required_fields": required_fields,
        "mapping_required_fields": mapping_required_fields,
        "field_types": {field: field_types.get(field, "str") for field in all_fields},
        "raw": document,
    }


def default_canonical_schema_path(protocol_dir: Path) -> Path:
    return protocol_dir.resolve() / "canonical_schema.yaml"


def load_intake_config(config_path: Optional[Path]) -> Dict[str, Any]:
    if config_path is None:
        return {"benchmark_overrides": {}, "evidence": []}
    if not config_path.is_file():
        raise FileNotFoundError(f"intake config does not exist: {config_path}")
    if config_path.suffix.lower() in {".yaml", ".yml"}:
        document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    elif config_path.suffix.lower() == ".json":
        document = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        raise ValueError("intake config must be YAML or JSON")
    if not isinstance(document, dict):
        raise ValueError("intake config must be a mapping")

    benchmark_overrides = document.get("benchmark_overrides")
    if benchmark_overrides is None:
        benchmark_overrides = {}
    if not isinstance(benchmark_overrides, dict):
        raise ValueError("intake config benchmark_overrides must be a mapping")

    evidence = document.get("evidence", [])
    if evidence is None:
        evidence = []
    if isinstance(evidence, dict):
        evidence = [evidence]
    if not isinstance(evidence, list):
        raise ValueError("intake config evidence must be a list")

    return {
        "config_path": str(config_path.resolve()),
        "benchmark_overrides": benchmark_overrides,
        "evidence": [item for item in evidence if isinstance(item, dict)],
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest().upper()


def stable_slug(text: str) -> str:
    value = re.sub(r"[^0-9A-Za-z._-]+", "_", text.strip())
    value = re.sub(r"_+", "_", value).strip("._-")
    return value.lower() or "dataset"


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def stable_record_id(dataset_name: str, source_id: str, test_text: str, reference_answer: str) -> str:
    seed = "\n".join(
        [
            stable_slug(dataset_name),
            normalize_text(source_id),
            normalize_text(test_text),
            normalize_text(reference_answer),
        ]
    )
    return f"{stable_slug(dataset_name)}-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def read_records(path: Path, limit: Optional[int] = None) -> Iterator[Dict[str, Any]]:
    suffix = path.suffix.lower()
    count = 0
    if suffix == ".jsonl":
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, 1):
                if limit is not None and count >= limit:
                    return
                text = line.strip()
                if not text:
                    continue
                value = json.loads(text)
                if not isinstance(value, dict):
                    raise ValueError(f"{path}:{line_number} expected JSON object")
                yield value
                count += 1
    elif suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(value, dict):
            if isinstance(value.get("data"), list):
                rows = value["data"]
            elif isinstance(value.get("records"), list):
                rows = value["records"]
            else:
                rows = [value]
        elif isinstance(value, list):
            rows = value
        else:
            raise ValueError(f"{path} expected JSON object or array")
        for item in rows:
            if limit is not None and count >= limit:
                return
            if isinstance(item, dict):
                yield item
                count += 1
    elif suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle, delimiter=delimiter):
                if limit is not None and count >= limit:
                    return
                yield dict(row)
                count += 1
    else:
        return


def count_records(path: Path) -> Optional[int]:
    try:
        if path.suffix.lower() == ".jsonl":
            count = 0
            with path.open("rb") as handle:
                for line in handle:
                    if line.strip():
                        count += 1
            return count
        if path.suffix.lower() in DATA_EXTENSIONS:
            return sum(1 for _ in read_records(path))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, csv.Error, ValueError):
        return None
    return None


def infer_schema(path: Path, sample_size: int = 100) -> Dict[str, Any]:
    rows = list(read_records(path, limit=sample_size)) if path.suffix.lower() in DATA_EXTENSIONS else []
    fields: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        for key, value in row.items():
            item = fields.setdefault(
                str(key),
                {"types": Counter(), "non_null": 0, "empty": 0, "examples": []},
            )
            item["types"][type(value).__name__] += 1
            if value is None or normalize_text(value) == "":
                item["empty"] += 1
            else:
                item["non_null"] += 1
                if len(item["examples"]) < 3:
                    item["examples"].append(normalize_text(value)[:160])
    return {
        "sample_size": len(rows),
        "fields": {
            key: {
                "types": dict(value["types"]),
                "non_null": value["non_null"],
                "empty": value["empty"],
                "examples": value["examples"],
            }
            for key, value in sorted(fields.items())
        },
    }


def canonical_fields_from_schema(schema: Dict[str, Any]) -> List[str]:
    return list(schema["all_fields"])


def required_fields_from_schema(schema: Dict[str, Any]) -> List[str]:
    return list(schema["required_fields"])


def mapping_required_fields_from_schema(schema: Dict[str, Any]) -> List[str]:
    return list(schema["mapping_required_fields"])


def discover_data_files(root: Path) -> List[Path]:
    if not root.is_dir():
        raise FileNotFoundError(f"benchmark root does not exist: {root}")
    discovered: List[Path] = []
    for pattern in BENCHMARK_GLOBS:
        discovered.extend(path.resolve() for path in root.glob(pattern) if path.is_file())
    return sorted(set(discovered))


def discover_evidence_files(root: Path) -> List[Path]:
    if not root.is_dir():
        raise FileNotFoundError(f"evidence root does not exist: {root}")
    discovered = [
        path.resolve()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in EVIDENCE_EXTENSIONS
    ]
    return sorted(set(discovered))


def benchmark_id_for_path(root: Path, source_path: Path) -> str:
    try:
        relative = source_path.resolve().relative_to(root.resolve())
        return stable_slug(str(relative.with_suffix("")))
    except ValueError:
        return stable_slug(source_path.stem)


def suggest_field_mapping(
    raw_fields: Iterable[str],
    canonical_fields: List[str],
    overrides: Optional[Dict[str, str]] = None,
    aliases: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, str]:
    raw_list = [str(item) for item in raw_fields]
    raw_set = set(raw_list)
    alias_table = aliases or FIELD_ALIASES
    override_map = overrides or {}
    mapping: Dict[str, str] = {}
    used_raw: set[str] = set()

    for canonical in canonical_fields:
        if canonical in override_map:
            raw_name = str(override_map[canonical])
            if raw_name in raw_set and raw_name not in used_raw:
                mapping[canonical] = raw_name
                used_raw.add(raw_name)
            continue
        if canonical in raw_set and canonical not in used_raw:
            mapping[canonical] = canonical
            used_raw.add(canonical)
            continue
        for alias in alias_table.get(canonical, []):
            if alias in raw_set and alias not in used_raw:
                mapping[canonical] = alias
                used_raw.add(alias)
                break
    return mapping


def get_manifest_path(entry: Dict[str, Any], field: str) -> Optional[Path]:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value)


def resolve_path(base_dir: Path, raw_path: Path) -> Path:
    if raw_path.is_absolute():
        return raw_path.expanduser().resolve()
    return (base_dir / raw_path).expanduser().resolve()


def probe_mapping(
    mapping_required_fields: List[str],
    raw_fields: Iterable[str],
    field_mapping: Dict[str, str],
    overrides: Optional[Dict[str, str]] = None,
    aliases: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    raw_set = {str(item) for item in raw_fields}
    override_map = overrides or {}
    alias_table = aliases or FIELD_ALIASES
    mapped_raw = {str(value) for value in field_mapping.values() if isinstance(value, str)}
    mapped_canonical = set(field_mapping.keys())
    missing_required = [
        field
        for field in mapping_required_fields
        if field not in mapped_canonical or field_mapping.get(field) not in raw_set
    ]
    ambiguous_mappings: Dict[str, Dict[str, Any]] = {}
    for field in mapping_required_fields:
        if field in override_map:
            continue
        candidates = []
        for candidate in [field, *alias_table.get(field, [])]:
            if candidate in raw_set and candidate not in candidates:
                candidates.append(candidate)
        if len(candidates) > 1:
            ambiguous_mappings[field] = {
                "selected": field_mapping.get(field),
                "candidates": candidates,
            }
    unmapped_raw = sorted(raw_set - mapped_raw)
    return {
        "mapped_canonical_fields": sorted(mapped_canonical),
        "mapped_raw_fields": sorted(mapped_raw),
        "missing_canonical_fields": missing_required,
        "ambiguous_mappings": ambiguous_mappings,
        "unmapped_raw_fields": unmapped_raw,
        "complete": not missing_required and not ambiguous_mappings,
    }


def taxonomy_names(protocol: Dict[str, Dict[str, Any]]) -> Tuple[set[str], set[str]]:
    taxonomy = protocol["taxonomy"]
    l1 = {str(item.get("name")) for item in taxonomy.get("l1_dimensions", []) if isinstance(item, dict)}
    l2 = {str(item.get("name")) for item in taxonomy.get("l2_dimensions", []) if isinstance(item, dict)}
    return l1, l2


def compute_stats(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    total = 0
    by_dataset: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    by_l1: Counter[str] = Counter()
    by_l2: Counter[str] = Counter()
    for record in records:
        total += 1
        by_dataset[normalize_text(record.get("dataset_name")) or "<missing>"] += 1
        by_type[normalize_text(record.get("type")) or "<missing>"] += 1
        by_l1[normalize_text(record.get("safety_l1_dimension")) or "<missing>"] += 1
        by_l2[normalize_text(record.get("safety_l2_harm_type")) or "<missing>"] += 1
    return {
        "total": total,
        "by_dataset_name": dict(sorted(by_dataset.items())),
        "by_type": dict(sorted(by_type.items())),
        "by_safety_l1_dimension": dict(sorted(by_l1.items())),
        "by_safety_l2_harm_type": dict(sorted(by_l2.items())),
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=False) + "\n")
            count += 1
    return count


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))
