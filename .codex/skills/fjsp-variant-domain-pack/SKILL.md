---
name: fjsp-variant-domain-pack
description: Use when adding, adapting, or reviewing FJSP problem-family variants such as SDST, time-lag/no-wait, machine unavailability, batching, transportation, reentrant routes, dynamic arrivals, or multi-objective FJSP; guides Domain Pack, RAG, Skill, Method Package, and evaluator-contract design while keeping solver algorithms out of generic backend orchestration.
---

# FJSP Variant Domain Pack

Use this skill to keep FJSP-variant support platform-first and contract-gated.

## Workflow

1. Read `knowledge/principles/fjsp_variant_domain_pack_rag.md`.
2. Read the active task IO/evaluator docs and instance diagnostics.
3. If the request concerns benchmark claims, read
   `knowledge/benchmarks/fjsp_benchmark_scope.md`. Read
   `knowledge/capabilities/fjsp_agent_current_capability_20260704.md` only for
   an explicit dated capability audit.
4. If the request concerns agent-generated FJSP-SDST solver evolution rather
   rather than an existing method-asset adaptation, read
   `knowledge/references/sdst/awls_sdst_agent_generated_transfer_notes.md`,
   then use `$fjsp-agent-generated-solver` for standalone-solver legality and
   neighborhood guidance.
5. If the request introduces industrial or non-standard constraints, read
   `knowledge/references/general_fjsp/fjsp_scene_survey_2025_10_17.md`.
6. Identify variant constraints before proposing code: setup, lag/no-wait,
   calendars, batching, transport, routes, releases, due dates, or objective
   changes.
7. Choose knowledge cards by Domain Pack tags and one selected Method Package.
   Keep the Worker Assignment compact and direction-local.
8. Require a natural-language rule/operator hypothesis before worker code.
9. Promote only by Core evaluator results.

## Backend Boundary

- Put variant algorithm knowledge in Domain Packs, knowledge cards, Skills,
  Method Packages, and Worker Assignments.
- Keep generic backend code limited to loading contracts, diagnostics,
  Domain Pack metadata, selected packages, knowledge-card snippets, and benchmark
  reports.
- For standalone agent-generated solvers, keep decoder and neighborhood
  patterns in skills or knowledge references. Do not put reusable solver code
  in generic backend orchestration.
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
- optional Method Packages with implementation and behavior contracts;
- smoke and performance benchmark ladders.

First prove legality and IO stability. Then improve makespan or other declared
objectives under the fixed evaluator.
