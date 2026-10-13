"""Only skip integration cases whose explicitly named external assets are absent."""
from functools import wraps
from pathlib import Path
import unittest


def requires_assets(*assets: Path):
    def decorate(function):
        @wraps(function)
        def checked(*args, **kwargs):
            missing = [str(path) for path in assets if not path.exists()]
            if missing:
                raise unittest.SkipTest(
                    "External integration assets missing: " + ", ".join(missing)
                    + "; configure AGENT_EVAL_DATA_ROOT and benchmark-specific Python environments"
                )
            (Path(__file__).resolve().parents[1] / ".codex_tmp").mkdir(exist_ok=True)
            return function(*args, **kwargs)
        return checked
    return decorate
