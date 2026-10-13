# Enterprise API support and evaluator distribution

## Support matrix

| CLI | Direct enterprise/provider support | Main limitation |
|---|---|---|
| Codex CLI | OpenAI and custom providers implementing the OpenAI Responses API | Chat-Completions-only endpoints cannot be used directly |
| Claude Code CLI | Anthropic API, AWS Bedrock, Google Vertex AI, Azure AI Foundry, and Anthropic-compatible base URLs | OpenAI-only endpoints need protocol translation |
| CodeBuddy CLI | Tencent/enterprise endpoints and custom OpenAI-compatible models through `models.json` | Custom models must implement OpenAI-style tool calls |
| Qoder CLI | Qoder account catalog and External Provider / OpenAI Compatible custom models | A Qoder account login is still required before listing or using models |

No single CLI natively supports every enterprise API. Use the protocol matrix
below as the admission rule for a model/provider pair:

| Enterprise API family | Codex CLI | Claude Code CLI | CodeBuddy CLI | Qoder CLI |
|---|---|---|---|---|
| OpenAI Responses API | Direct | Gateway to Anthropic Messages | Gateway to Chat Completions | Gateway to Chat Completions |
| OpenAI/Azure OpenAI Chat Completions | Responses-capable gateway required | Anthropic-compatible gateway required | Direct when OpenAI-compatible | Direct through External Provider |
| Anthropic API | Responses-capable gateway required | Direct | OpenAI-compatible gateway required | OpenAI-compatible gateway required |
| AWS Bedrock / Google Vertex AI / Azure AI Foundry for Claude | Responses-capable gateway required | Direct | OpenAI-compatible gateway required | OpenAI-compatible gateway required |
| DeepSeek / Bailian / Ark / Hunyuan / other OpenAI-compatible APIs | Direct only if `/responses` is implemented | Direct only if Anthropic `/messages` is implemented | Usually direct through `models.json` | Usually direct through External Provider |

"Direct" means protocol-compatible, not automatically model-compatible. The
exact model identifier, streaming behavior, context limits, and tool-call
format still have to pass an unscored CLI smoke run. A `401` probe proves only
that an endpoint is reachable; it does not prove that the protocol or model is
supported after authentication.

For broad enterprise coverage, expose one controlled gateway with both
OpenAI-compatible and Anthropic-compatible routes. Its OpenAI route must expose
both `/responses` for Codex and `/chat/completions` for CodeBuddy/Qoder. The
Anthropic route must expose `/messages` for Claude. The gateway can route to
OpenAI, Azure OpenAI, Anthropic, Bedrock, Vertex AI, DeepSeek, Alibaba Bailian,
Volcengine Ark, Tencent, OpenRouter, or an internal inference service. Keep the
same upstream model and decoding settings for all four CLIs.

Generate a package against an enterprise gateway profile with:

```powershell
python tools\package_parallel_cli_eval.py `
  --input release\datasets\full\agent_execution_computer_safety.tasks.jsonl `
  --out-root release\parallel_cli_eval_enterprise `
  --evaluators 4 --calibration-size 40 --seed 42 `
  --model your-enterprise-model-id `
  --openai-base-url https://gateway.example.com/openai/v1 `
  --anthropic-base-url https://gateway.example.com/anthropic
```

Protocol compatibility alone is insufficient. A model used for agent testing
must support streaming and structured tool calls. Before a scored run, execute
one text-only probe and one tool-call probe for every CLI/model combination.
Configuration failures are not safety results.

For this package's current text-only CLI profile, tools are deliberately
disabled. The required gate is therefore a successful text/streaming smoke run
for all four CLIs. Enable and separately validate tool calls before using the
same provider profile for an interactive tool benchmark.

## Recommended delivery

For the model-owner experiment, generate packages with `--profiles`. Each model
profile receives the same complete task set and runs Codex, Claude Code,
CodeBuddy, and Qoder. Do not use the default sharded mode for cross-model
comparison because different task sets would confound model effects.

### Windows portable package

The evaluator needs Python 3.10+ and Node.js 20+, but does not install any CLI
globally:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_portable.ps1
powershell -ExecutionPolicy Bypass -File scripts\login_qoder.ps1
powershell -ExecutionPolicy Bypass -File scripts\run_native.ps1 -Agent all -Sample 1
powershell -ExecutionPolicy Bypass -File scripts\run_native.ps1 -Agent all -Resume
powershell -ExecutionPolicy Bypass -File scripts\collect_results.ps1
```

`setup_portable.ps1` installs pinned CLI versions under `runtime/npm` inside the
unpacked evaluator package. It does not require administrator rights and does
not modify global npm packages. Qoder opens a browser only for account OAuth;
its state is written under `state/qoder`, not the user's production profile.

The evaluator ZIP intentionally omits the approximately 902 MiB uncompressed
Node runtime. This keeps task packages small and allows different task shards
to reuse one local installation. If evaluators cannot access npm, prepare the
portable runtime once on an offline staging machine, archive `runtime/npm`, and
distribute that archive separately from evaluator task ZIPs. Do not include any
`state` directory in the shared runtime archive.

### Prebuilt Docker image

For evaluators who have Docker, the administrator can build the image once and
publish it to a private registry or export it with `docker save`. Evaluators
then need only Docker, the evaluator ZIP, their Qoder login, and process-scoped
API credentials. Never bake credentials or login state into the image.

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_cli_runtime_image.ps1 `
  -PackageDir release\parallel_cli_eval_validation_50\evaluator_01 `
  -Output agent-cli-runtime-image.tar

docker load -i agent-cli-runtime-image.tar
powershell -ExecutionPolicy Bypass -File scripts\login_qoder_docker.ps1
powershell -ExecutionPolicy Bypass -File scripts\run_docker.ps1 -Agent all
```

The runtime image is shared by every evaluator; their assigned package is
mounted read-only at runtime and is not copied into the image.

Docker removes the Python/Node/CLI installation burden, but does not remove
provider authentication. Qoder account tokens must remain per evaluator; do not
share one administrator login directory across people.
