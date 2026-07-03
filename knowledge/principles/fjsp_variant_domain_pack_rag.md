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
algorithms, failed attempts, paper notes, repair rules, and slot rules belong
in domain packs, knowledge cards, skills, and context packets.

## Variant Intake Order

1. Identify the active IO contract from task docs, instance diagnostics, and
   evaluator files.
2. Name the variant constraints explicitly, such as setup matrix, lag bounds,
   machine calendars, batch capacity, transport times, route choice, release
   dates, due dates, or multiple objectives.
3. Confirm the evaluator owns legality and objective semantics before changing
   solver code.
4. Select knowledge by tags and selected slots; do not flood the worker with
   unrelated papers.
5. Require a natural-language rule/operator hypothesis before any candidate
   code.
6. Promote only by Core evaluator metrics and benchmark reports.

## Retrieval Rules

- Always retrieve benchmark scope and current capability cards for claims about
  solver quality.
- Retrieve the survey card for new industrial constraints:
  `knowledge/papers/fjsp_scene_survey_2025_10_17.md`.
- Retrieve slot-specific notes only for the selected slot, such as SDST
  initialization, move evaluation, zi features, tabu memory, or search control.
- Retrieve failed-attempt notes before proposing a similar operator, so the
  worker mutates or avoids repeated ideas.
- Treat LB/UB/BKS as reporting and gate-selection diagnostics only.

## Backend Boundary

The backend may load:

- domain-pack capability metadata;
- IO/evaluator invariant text;
- selected code-slot manifests;
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
- optional slot manifests for user-confirmed edit regions;
- smoke and performance benchmark ladders with LB/UB/BKS if available.

Keep the first milestone legality-focused. Quality work starts only after the
parser, evaluator, schedule schema, and smoke benchmarks are stable.
