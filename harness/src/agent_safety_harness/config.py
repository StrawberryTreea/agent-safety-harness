from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_config_dir() -> Path:
    return project_root() / "config"


def load_json_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    try:
        return json.loads(config_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Config file not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON config at {config_path}: {exc}") from exc


def load_named_model(model_id: str, models_config: str | None = None) -> dict[str, Any]:
    config_path = Path(models_config) if models_config else default_config_dir() / "models.json"
    config = load_json_config(config_path)
    models = config.get("models") or {}
    try:
        model = dict(models[model_id])
    except KeyError as exc:
        known = ", ".join(sorted(models))
        raise ValueError(f"Unknown configured model {model_id!r}. Known: {known}") from exc
    model["id"] = model_id
    return model


def load_named_agent(agent_id: str, agents_config: str | None = None) -> dict[str, Any]:
    config_path = Path(agents_config) if agents_config else default_config_dir() / "agents.json"
    config = load_json_config(config_path)
    agents = config.get("agents") or {}
    try:
        agent = dict(agents[agent_id])
    except KeyError as exc:
        known = ", ".join(sorted(agents))
        raise ValueError(f"Unknown configured agent {agent_id!r}. Known: {known}") from exc
    agent["id"] = agent_id
    return agent


def load_import_preset(preset_id: str, imports_config: str | None = None) -> dict[str, Any]:
    config_path = Path(imports_config) if imports_config else default_config_dir() / "benchmark_imports.json"
    config = load_json_config(config_path)
    imports = config.get("imports") or {}
    templates = config.get("templates") or {}
    try:
        preset = _resolve_import_preset(preset_id, imports, templates)
    except KeyError as exc:
        known = ", ".join(sorted({*imports.keys(), *templates.keys()}))
        raise ValueError(f"Unknown benchmark import preset {preset_id!r}. Known: {known}") from exc
    preset["id"] = preset_id
    return preset


def _resolve_import_preset(preset_id: str, imports: dict[str, Any], templates: dict[str, Any]) -> dict[str, Any]:
    seen: set[str] = set()

    def resolve(entry_id: str) -> dict[str, Any]:
        if entry_id in seen:
            chain = " -> ".join([*seen, entry_id])
            raise ValueError(f"Cyclic benchmark import preset inheritance detected: {chain}")
        seen.add(entry_id)
        if entry_id in imports:
            entry = dict(imports[entry_id])
        elif entry_id in templates:
            entry = dict(templates[entry_id])
        else:
            known = ", ".join(sorted({*imports.keys(), *templates.keys()}))
            raise KeyError(f"Unknown benchmark import preset {entry_id!r}. Known: {known}")

        parent_id = entry.pop("extends", None)
        if parent_id:
            base = resolve(str(parent_id))
            return _deep_merge_dict(base, entry)
        return entry

    return resolve(preset_id)


def _deep_merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def load_policy_taxonomy(path: str | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else default_config_dir() / "policies.json"
    return load_json_config(config_path)
