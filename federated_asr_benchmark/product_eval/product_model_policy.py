from __future__ import annotations

import os


CANONICAL_PRODUCT_MODEL = "deepseek-v4-flash"
QODER_MODEL_PLACEHOLDER = "${QODER_DEEPSEEK_MODEL_ID}"
QODER_EXTERNAL_MODEL_KEY = "deepseek/deepseek-v4-flash"

PRODUCT_MODEL_BINDINGS = {
    "codex": {
        "request_model": "deepseek-v4-flash",
        "runtime_models": ("deepseek-v4-flash",),
        "runtime_detection_required": False,
    },
    "claude_code": {
        "request_model": "deepseek-v4-flash",
        "runtime_models": ("deepseek-v4-flash",),
        "runtime_detection_required": True,
    },
    "qoder": {
        "request_model": QODER_EXTERNAL_MODEL_KEY,
        "runtime_models": (
            "DeepSeek-V4-Flash",
            "DeepSeek V4 Flash",
            QODER_EXTERNAL_MODEL_KEY,
        ),
        "runtime_detection_required": True,
    },
    "codebuddy": {
        "request_model": "deepseek-v4-flash",
        "runtime_models": (
            "deepseek-v4-flash",
            "custom-local:deepseek-v4-flash",
        ),
        "runtime_detection_required": True,
    },
}


def binding_for(product_id: str) -> dict[str, object]:
    try:
        return PRODUCT_MODEL_BINDINGS[product_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported product for model policy: {product_id}") from exc


def validate_requested_model(product_id: str, requested_model: str | None) -> None:
    binding = binding_for(product_id)
    expected = binding.get("request_model")
    expected_env = binding.get("request_model_env")
    if expected_env:
        expected = os.environ.get(str(expected_env))
        if not expected:
            raise RuntimeError(
                f"{product_id} custom DeepSeek model ID is not configured. Save the BYOK "
                f"DeepSeek-V4-Flash model in Qoder, then set {expected_env} to the model ID "
                "reported by qodercli. Managed model aliases such as 'dfmodel' are forbidden."
            )
    if requested_model != expected:
        raise RuntimeError(
            f"{product_id} must request {expected!r} under the controlled product-model "
            f"policy; received {requested_model!r}"
        )


def resolve_requested_model(product_id: str, requested_model: str | None) -> str | None:
    if product_id == "qoder" and requested_model == QODER_MODEL_PLACEHOLDER:
        requested_model = QODER_EXTERNAL_MODEL_KEY
    validate_requested_model(product_id, requested_model)
    return requested_model


def validate_runtime_model(
    product_id: str,
    requested_model: str | None,
    detected_model: str | None,
) -> str:
    validate_requested_model(product_id, requested_model)
    binding = binding_for(product_id)
    expected_runtime = tuple(binding["runtime_models"])
    if detected_model is None:
        if binding["runtime_detection_required"]:
            raise RuntimeError(
                f"{product_id} did not report its effective runtime model; "
                f"expected one of {expected_runtime}"
            )
    elif detected_model.casefold() not in {model.casefold() for model in expected_runtime}:
        raise RuntimeError(
            f"{product_id} reported runtime model {detected_model!r}; "
            f"expected one of {expected_runtime}"
        )
    return CANONICAL_PRODUCT_MODEL
