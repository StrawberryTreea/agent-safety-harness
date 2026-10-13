# Project Positioning

## Purpose

This project builds our own agent-safety evaluation harness and benchmark suite.

External benchmark datasets are used as source material: task ideas, attack patterns, risk labels, tool surfaces, environment state, and scoring inspiration. They are not the final authority for our evaluation protocol.

## What We Are Building

The core deliverable is a unified harness that can evaluate agents under a consistent protocol:

- normalized task schema;
- configurable agent adapters;
- sandboxed computer/tool/service execution;
- trace and state-diff evidence;
- unified safety metrics;
- comparable reports across agents.

The resulting benchmark should be described as our own agent-safety benchmark, derived from curated external data sources.

## What We Are Not Building

We are not trying to reproduce every external benchmark environment exactly.

We should not block dataset usage just because the original simulator, scorer, or environment is heavy, unavailable, or domain-specific. Those components are useful only when we need official-result comparability.

## Default Dataset Integration Policy

For each external source:

1. Inspect the raw data format when available.
2. Extract the task intent, unsafe goal, allowed tools, state, secrets, trace, and labels.
3. Normalize records into our `TaskSpec`.
4. Run the task through our agent adapter and sandbox protocol.
5. Score results with our unified safety metrics.
6. Preserve source metadata for provenance.

Official simulator or scorer integration is optional and should be treated as a compatibility layer, not the default path.

## Naming

Use these terms consistently:

- `source dataset`: external benchmark or dataset used as input material.
- `our benchmark`: the curated, normalized task set we evaluate.
- `harness`: the execution, sandbox, logging, scoring, and reporting framework.
- `agent adapter`: the interface to a tested product or baseline agent.

Codex, Claude Code, Work Buddy, reference models, and CLI products are agent adapters. They are not benchmark datasets.
