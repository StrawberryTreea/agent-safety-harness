"""Read-only asset inventory. Presence is never runtime qualification."""
from __future__ import annotations

import os
from pathlib import Path


def inventory(repo: Path, data_root: Path | None = None) -> dict:
    configured = data_root or (Path(os.environ["AGENT_EVAL_DATA_ROOT"]) if os.environ.get("AGENT_EVAL_DATA_ROOT") else None)
    root = configured or repo / "数据集" / "评测数据"
    sources = {"injecagent": root / "InjecAgent-main", "agentdojo": root / "AgentDojo"}
    return {"schema_version": "loop_a_qualification_v1", "data_root_configured": configured is not None,
            "data_root_exists": root.is_dir(), "formal_ready": False,
            "sources": [{"source_id": name, "asset_path": str(path), "assets_present": path.is_dir(),
                         "qualification_status": "unverified" if path.is_dir() else "blocked_missing_assets",
                         "required_evidence": ["license", "benign_completion", "attack_delivery", "private_scorer",
                                               "replay", "real_isolation", "fixed_target"]}
                        for name, path in sources.items()],
            "note": "No credential values read; no API, CLI or network calls; runtime evidence is still required."}
