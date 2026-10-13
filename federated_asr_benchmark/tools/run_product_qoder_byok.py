from __future__ import annotations

from pathlib import Path
import sys


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

import product_eval.runner as runner_module
from product_eval.qoder_byok_compat import (
    QODER_BYOK_CLI_VERSION,
    QoderByokProductRunner,
    qoder_cli_executable,
    resolve_qoder_byok_model,
    validate_qoder_byok_runtime_model,
)
from tools import run_product as target
original_load_request = target.load_request


def load_qoder_byok_request(path: Path):
    request = original_load_request(path)
    product = request["product"]
    if product["product_id"] != "qoder":
        raise RuntimeError("Qoder BYOK entry point received a non-Qoder request")
    executable = qoder_cli_executable(PACKAGE_ROOT)
    if not executable.is_file():
        raise RuntimeError(f"Pinned Qoder CLI is missing: {executable}")
    product["executable"] = str(executable)
    product["version"] = QODER_BYOK_CLI_VERSION
    return request


target.load_request = load_qoder_byok_request
target.resolve_requested_model = resolve_qoder_byok_model
target.ProductRunner = QoderByokProductRunner
runner_module.validate_runtime_model = validate_qoder_byok_runtime_model


if __name__ == "__main__":
    raise SystemExit(target.main())
