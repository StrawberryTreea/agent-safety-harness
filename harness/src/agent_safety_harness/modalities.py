"""Configuration-backed modality and taxonomy contracts; no closed modality enum."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .config import default_config_dir, load_json_config


def modality_contract(modality: str, config_path: str | Path | None = None) -> tuple[dict, dict]:
    path = Path(config_path) if config_path else default_config_dir() / "modalities.json"
    config = load_json_config(path)
    entries = config.get("modalities", {})
    if modality not in entries:
        raise ValueError(f"Unregistered modality {modality!r}; registered: {sorted(entries)}")
    registry_path = (path.resolve().parent / config["taxonomy_registry"]).resolve()
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    return entries[modality], registry.get("task_taxonomies", {})


def validate_modality(record: dict[str, Any], config_path: str | Path | None = None) -> str:
    modality = record.get("modality", "agent")
    if not isinstance(modality, str) or not modality:
        raise ValueError("modality must be a registered non-empty string")
    contract, taxonomies = modality_contract(modality, config_path)
    try:
        jsonschema.validate(record, contract["schema"])
    except jsonschema.ValidationError as exc:
        raise ValueError(f"Invalid {modality} task: {exc.message}") from None
    taxonomy = record.get("taxonomy_ref") or contract["default_taxonomy_ref"]
    if taxonomy not in taxonomies or modality not in taxonomies[taxonomy].get("modalities", []):
        raise ValueError(f"Unregistered or incompatible taxonomy_ref {taxonomy!r} for {modality!r}")
    return str(taxonomy)
