# Qoder CLI DeepSeek Route Audit

Updated: 2026-08-11

## Required route

Formal evaluation requires all of the following:

- CLI version: `1.0.48`
- requested/runtime model ID: `model_1786147854337_zp7v4vd`
- provider: `deepseek`
- provider model: `deepseek-v4-flash-pg`
- BYOK type: `pg`
- canonical model: `deepseek-v4-flash`
- runner-supplied `DEEPSEEK_API_KEY` recorded only as a boolean marker
- at least one observed custom-model request
- every Qoder control-plane generation line paired with custom-model request evidence
- no built-in quota-use evidence

Configuration evidence alone is insufficient. A result without observed custom-pool request
evidence, or with unpaired generation traffic, is invalid and contributes `0/0` to ASR.

## Version probes

| CLI version | Startup result | Route result | Qualification |
|---|---|---|---|
| 1.0.48 | Starts | Isolated DeepSeek BYOK custom pool; observed request evidence and no built-in quota use | Qualified |
| 1.1.13 | Starts | Uses the managed Qoder route; no direct DeepSeek inference observed | Rejected |
| 1.1.14 | Windows process exits `0xC0000409` during external-provider startup | No inference request | Rejected |
| 1.1.15 | Starts, then exits `42` | Rejects `deepseek/deepseek-v4-flash`; lists only `Lite` and `Qwen3.8-Max`; no direct DeepSeek inference | Rejected |
| 1.1.16 | Windows process exits `0xC0000409` during external-provider startup | No inference request | Rejected |
| 1.1.17 | Windows process exits `0xC0000409` during external-provider startup | No inference request | Rejected |

Later CLI versions were rejected because they did not satisfy the API-only route gate.
The formal profile is pinned to the older qualified `1.0.48` package and its npm integrity
hash. Historical 1.1.13 artifacts remain preserved for audit but are not accepted by the
current release profile.

## Evidence handling

Probe artifacts are stored under `.codex_tmp/federated_asr_runs/` and are intentionally
excluded from portable releases. Portable releases include this sanitized audit only. No API
keys, authorization headers, request bodies, or machine-specific temporary paths are included.
