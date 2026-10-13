"""Portable external assets; command-line paths override these defaults."""
import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def default_data_root() -> Path:
    configured = os.environ.get("AGENT_EVAL_DATA_ROOT")
    return Path(configured).expanduser().resolve() if configured else PACKAGE_ROOT.parent / "数据集" / "评测数据"


def runtime_python(benchmark: str) -> Path:
    configured = os.environ.get(f"{benchmark.upper()}_PYTHON")
    return Path(configured).expanduser().resolve() if configured else PACKAGE_ROOT / ".venvs" / benchmark / "Scripts" / "python.exe"
