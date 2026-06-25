---
id: paper-fjsp-scene-survey-2025-10-17
type: survey
title: FJSP 场景调研报告 2025-10-17
tags: [fjsp, survey, industrial-variants, constraints, aluminum-rolling, semiconductor, dynamic-fjsp, multi-objective, llm-heuristics]
source: knowledge/local_papers/raw/FJSP场景调研报告10-17.pdf
status: local_report_indexed
---

# FJSP 场景调研报告 2025-10-17

## Source

- Local PDF: `knowledge/local_papers/raw/FJSP场景调研报告10-17.pdf`
- Pages: 144
- Imported from local WeChat file on 2026-06-25.

## Relevant Idea

This report is a broad FJSP scene and variant survey rather than a single
algorithm paper. It is useful as the first local map for moving the harness from
standard benchmark FJSP toward industrial variants.

The report covers:

- industrial scenarios: aluminum coil rolling and semiconductor manufacturing;
- classic FJSP modeling: machine assignment, operation sequencing, mathematical
  model, and disjunctive graph representation;
- objective variants: makespan, conventional/non-conventional criteria,
  multi-objective optimization, energy, quality, tardiness, and robustness;
- constraint variants: time lag/no-wait, machine unavailability, batching,
  setup times, transportation, reentrance, and alternative/complex routes;
- solver families: exact methods, dispatching/constructive heuristics,
  single-solution metaheuristics, population metaheuristics, hybrid
  metaheuristics, RL/DRL, graph/attention models, and LLM heuristic evolution.

## Platform Impact

The report reinforces that the platform should treat "standard FJSP" as only
one problem family capability, not the final scope.

Implications for `fjsp_harness_agent`:

- Problem-family cards should grow into variant cards with explicit constraint
  capabilities, not just names. Candidate variant tags include `time_lag_fjsp`,
  `machine_unavailability_fjsp`, `batching_fjsp`, `setup_time_fjsp`,
  `transportation_fjsp`, `reentrant_fjsp`, `alternative_route_fjsp`,
  `dynamic_fjsp`, and `multi_objective_fjsp`.
- Slot manifests should support variant-specific slots. Examples: lag-aware
  decoder repair, no-wait block moves, maintenance-window insertion, batch
  formation, setup-aware sequence scoring, transport-aware move scoring,
  reentrance cycle checks, and route-choice neighborhoods.
- Context packets should carry the active variant constraints and forbid workers
  from changing parser/evaluator semantics unless the user confirms a new IO
  contract.
- Knowledge selection should be tag-driven. This survey should be retrieved
  whenever the task asks for FJSP variants, industrial constraints, dynamic
  scheduling, or LLM/RL-assisted heuristic evolution.
- Evaluation should remain contract-gated. For variants, the platform needs new
  evaluators/validators before worker code evolution can be trusted.

## Useful Variant Map

| Variant/constraint | Why it matters | Harness adaptation target |
| --- | --- | --- |
| Time lag / no-wait | Common in semiconductor and high-temperature processes; makes naive schedules infeasible. | Parser/evaluator fields for min/max lag; decoder repair and no-wait block slots. |
| Machine unavailability | Maintenance and outages fragment machine calendars. | Machine calendar validator; availability-aware insertion slot. |
| Batching | Common in semiconductor and process industries; adds batch formation decisions. | Batch schema; batch compatibility/capacity evaluator; batch neighborhood slots. |
| Setup times | Sequence-dependent setup changes move scoring and machine sequence evaluation. | Setup matrix input contract; setup-aware objective and neighborhood slots. |
| Transportation | Cross-machine or cross-factory logistics affect start times and objective value. | Transport-time schema; transport-aware decoder/evaluator. |
| Reentrance | Semiconductor and rolling flows revisit machines; increases cycle/resource contention. | Reentrance-aware graph model and cycle checks. |
| Alternative routes | Jobs may have multiple process routes, not only alternative machines. | Route-choice layer in state representation and route-switch neighborhoods. |
| Dynamic/multi-objective FJSP | Real production has new jobs, failures, energy, quality, and tardiness objectives. | Rolling contract updates, multi-objective evaluator keys, and policy/RL hooks. |

## How To Use This Card

Use this card when planning:

- a new problem-family capability card;
- a new slot manifest for an industrial FJSP variant;
- a RAG query for FJSP constraints beyond standard public benchmarks;
- future skill design for FJSP-specific heuristic evolution.

Do not use this survey card as direct proof that a solver implementation is
correct. It is a design and scoping source; correctness still comes from the
active task contract and evaluator.

