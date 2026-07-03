---
name: fjsp-variant-domain-pack
description: Use when adding, adapting, or reviewing FJSP problem-family variants such as SDST, time-lag/no-wait, machine unavailability, batching, transportation, reentrant routes, dynamic arrivals, or multi-objective FJSP; guides domain-pack/RAG/skill/slot design while keeping solver algorithms out of generic backend orchestration.
---

# FJSP Variant Domain Pack

Use this skill to keep FJSP-variant support platform-first and contract-gated.

## Workflow

1. Read `knowledge/principles/fjsp_variant_domain_pack_rag.md`.
2. Read the active task IO/evaluator docs and instance diagnostics.
3. If the request concerns benchmark claims, read
   `knowledge/benchmarks/fjsp_benchmark_scope.md` and
   `knowledge/papers/fjsp_agent_current_capability_20260704.md`.
4. If the request introduces industrial or non-standard constraints, read
   `knowledge/papers/fjsp_scene_survey_2025_10_17.md`.
5. Identify variant constraints before proposing code: setup, lag/no-wait,
   calendars, batching, transport, routes, releases, due dates, or objective
   changes.
6. Choose knowledge cards by domain-pack tags and selected slots. Keep the
   worker prompt compact and slot-local.
7. Require a natural-language rule/operator hypothesis before worker code.
8. Promote only by Core evaluator results.

## Backend Boundary

- Put variant algorithm knowledge in domain packs, knowledge cards, skills,
  slot manifests, and worker context.
- Keep generic backend code limited to loading contracts, diagnostics,
  domain-pack metadata, selected slots, knowledge-card snippets, and benchmark
  reports.
- Do not hardcode SDST, no-wait, batching, transport, or other variant
  heuristics into generic orchestration.
- Do not change parser/evaluator semantics unless the user confirms a new IO
  contract.
- Treat LB/UB/BKS as diagnostics, not solver inputs.

## Minimum Variant Pack

For a new FJSP variant, add or update:

- supported variants and aliases;
- IO contract notes and evaluator invariants;
- canonical objectives and optional diagnostics;
- solver or adapter entrypoints;
- knowledge tags and cards;
- optional selected code-slot manifests;
- smoke and performance benchmark ladders.

First prove legality and IO stability. Then improve makespan or other declared
objectives under the fixed evaluator.
