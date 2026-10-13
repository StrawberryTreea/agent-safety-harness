# Project conventions

This repository is the primary code and design workspace for LLM and Agent safety evaluation and test-set evolution. Make new changes here; sibling copies are migration sources.

- Start with `docs/README.md`, `protocol/evaluation_tracks.json` and `pipeline.py inventory`. Reuse each track's native executor, schemas and scorer.
- LLM QA, source-derived Agent execution, full-product native evaluation, and offline trace judging have separate populations and metrics. Preserve native directions and denominator units.
- The integrated project is V1 (1.0.0), recorded in VERSION. Do not inherit retired project release labels or corpus counts. Third-party versions and immutable sampling provenance remain independent.
- The shipped LLM protocol binds a three-row reference fixture. Real experiments must freeze their own input path/count/hash and scorer/model identity; canonical data defaults to data/canonical.jsonl with data/stats.json. This is not an Agent prerequisite.
- `unknown`, API/environment errors and `invalid` are not safety passes. Attack-only tasks cannot contribute task success without a task-success rule.
- Capture public runtime tasks before scoring with private rules. Do not expose private oracles through product prompts or generated public tasks.
- A failure reason is an observed signal. Verify its cause against traces and environment evidence before describing a mechanism.
- Generated plans are unaccepted. Dataset admission requires replay, benign solvability, private scoring, provenance/license, deduplication, diversity, holdout isolation and review evidence.
- Keep raw datasets, credentials, virtual environments and run outputs outside Git. Use `AGENT_EVAL_DATA_ROOT`, command-line overrides and environment variable credentials. Do not overwrite a nonempty output directory without an explicit overwrite option.
- Mock fixtures, source indices and source mappings do not establish live product or official benchmark qualification.
- Run `python scripts/check_repo.py` for collaborator handoff; use `--docs-only` for early link checks. Phase6–8 are reserved and have no bundled execution command. For changes, run relevant tests and a local smoke when execution contracts change. Source integration tests may skip only when their named external assets are absent; report skips and remaining qualification gaps.

When working with PDFs, reuse existing MinerU artifacts first. Prefer MinerU for scans, image-heavy layouts or poor local extraction; user-level `MINERU_ACCESS_KEY` and `MINERU_SECRET_KEY` are available. Text-friendly PDFs may use local extraction.
