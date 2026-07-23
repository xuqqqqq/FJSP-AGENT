---
id: fjsp-variant-domain-pack-rag
type: principle
title: FJSP Variant Domain-Pack and RAG Contract
tags: [fjsp, variants, domain-pack, rag, io-contract, evaluator, skill]
status: active
---

# FJSP Variant Domain-Pack and RAG Contract

## Purpose

Use this card when a worker faces standard FJSP or a variant such as SDST,
time-lag/no-wait FJSP, machine unavailability, batching, transportation,
reentrant routes, dynamic arrivals, or multi-objective scheduling.

The platform backend should stay problem-family generic. Variant-specific
algorithms, paper notes, and repair rules belong in Domain Packs, knowledge
cards, Skills, Method Packages, and bounded Worker Assignments. Run-specific
failed attempts belong in `experiment_memory/`, not stable method guidance.

## Variant Intake Order

1. Identify the active IO contract from task docs, instance diagnostics, and
   evaluator files.
2. Name the variant constraints explicitly, such as setup matrix, lag bounds,
   machine calendars, batch capacity, transport times, route choice, release
   dates, due dates, or multiple objectives.
3. Confirm the evaluator owns legality and objective semantics before changing
   solver code.
4. Select knowledge by tags and one Method Package; do not flood the Worker
   with unrelated papers.
5. Require a natural-language rule/operator hypothesis before any candidate
   code.
6. Promote only by Core evaluator metrics and benchmark reports.

## Retrieval Rules

- Always retrieve benchmark scope for claims about solver quality. Read a dated
  capability snapshot only when the task explicitly asks for that audit date.
- Retrieve the survey card for new industrial constraints:
  `knowledge/references/general_fjsp/fjsp_scene_survey_2025_10_17.md`.
- Retrieve only the stable references and contracts attached to the selected
  Method Package.
- Replay failed-attempt notes only through Main's explicit experience-memory
  path. Never place `experiment_memory/` in default Worker RAG.
- Treat LB/UB/BKS as reporting and gate-selection diagnostics only.

## Evidence Hygiene

- Method, Skill, and package guidance should describe reusable mechanisms,
  invariants, and failure modes rather than instance-specific target makespans,
  copied schedules, or seed-specific answers.
- Numerical makespans, LB/UB/BKS, and per-instance gaps belong in benchmark or
  capability or experiment reports. They are Main-side diagnostics for gates
  and comparisons, not Worker solver inputs.
- When promoting an experiment into a knowledge card, normalize concrete
  outcomes into method lessons such as "operation-level setup-aware dispatch
  helped" or "representation-mixing local search failed".
- Keep artifact paths only as audit breadcrumbs.  Do not ask a worker to
  reproduce a previous artifact's exact score or solution.

## Backend Boundary

The backend may load:

- domain-pack capability metadata;
- IO/evaluator invariant text;
- selected Method Package metadata and contract paths;
- knowledge-card paths and snippets;
- benchmark bounds for reporting.

The backend must not hardcode:

- a specific FJSP neighborhood as a platform rule;
- SDST-specific setup formulas inside generic orchestration;
- variant-specific parser assumptions without a confirmed IO contract;
- promotion logic that optimizes against LB/UB instead of the declared
  objective.

## Variant Pack Checklist

For a new FJSP variant, add or update a domain pack with:

- supported variant names and aliases;
- canonical objectives and optional diagnostic metrics;
- IO contract notes;
- evaluator invariants;
- solver entrypoints or adapter entrypoints;
- knowledge tags mapped to cards;
- optional Method Packages with complete implementation contracts;
- smoke and performance benchmark ladders with LB/UB/BKS if available.

Keep the first milestone legality-focused. Quality work starts only after the
parser, evaluator, schedule schema, and smoke benchmarks are stable.
