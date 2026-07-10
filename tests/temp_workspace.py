from __future__ import annotations

import shutil
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkspaceTempDir:
    def __init__(self) -> None:
        self.path = ROOT / ".tmp" / f"test-{uuid.uuid4().hex}"

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=False)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        shutil.rmtree(self.path, ignore_errors=True)

