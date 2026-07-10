---
id: agent-generated-variant-quality-contracts
type: principle
title: Agent-Generated FJSP Variant Quality Contracts
tags: [fjsp, variants, agent_generated_solver, io-contract, self-check]
status: active
---

# Agent-Generated FJSP Variant Quality Contracts

## Purpose

Use this card when the coding worker writes or evolves a standalone
agent-generated FJSP solver from requirement and IO documents.

The backend should not provide solver algorithms.  It should provide the
worker with the active feature contract and require code evidence that the
generated solver implements and validates the relevant constraints.

## Active Feature Intake

Read the task description, IO contract, evaluator protocol, and instance
diagnostics before choosing a method.  Treat parsed diagnostics as stronger
evidence than broad RAG cards that merely list supported variants.

Only implement a variant feature when it is active in the current context.
Do not add SDST, no-wait, calendar, batching, transport, release-date, due-date,
or multi-objective assumptions to a standard FJSP instance.

## Evidence Contract

For every generated solver, cite concrete source symbols for:

- standalone `--input`, `--output`, `--seed` CLI;
- active parser for all jobs, operations, candidate machines, durations, and
  active variant data;
- stable operation identity across parser, construction, decode, search, and
  output;
- complete coverage, duplicate rejection, machine eligibility, duration
  equality, precedence, non-overlap, runtime bounds, and incumbent preservation.

The `solver_contract_self_check` narrative fields are evidence fields, not
free-form strategy notes. `representation`, `decoder`, `variant_handling`,
`runtime_bounds`, and `incumbent_preservation` should each name source symbols
from the submitted solver. If a field only describes an intention and no cited
symbol appears in the code, repair the code or the self-check before running
objective evaluation.

For active variants, add evidence for the matching constraint:

- `sequence_dependent_setup`: setup on adjacent same-machine arcs and full
  decode before comparing a sequence move;
- `no_wait`: each successor starts exactly at predecessor completion;
- `time_lag`: min/max lag bounds are applied between predecessor completion and
  successor start;
- `machine_calendar`: scheduled intervals fit availability and avoid
  unavailable windows;
- `batching`: batch capacity and compatibility are checked;
- `transportation`: travel/transport time contributes to successor readiness;
- `release_dates`: no operation/job starts before its parsed release time;
- `due_dates`: due-date, lateness, or tardiness terms are computed when part of
  the declared objective;
- `multi_objective`: candidate comparison follows the declared weights,
  priority order, or Pareto rule.

## Repair Priority

If review reports missing parser, representation, constructor, decoder, or
variant evidence, repair that structural gap before adding a new local-search
idea.  A heuristic improvement that cannot pass its own active-feature
self-check should not reach Core evaluator time.

Do not copy previous exact schedules or target makespans into the solver.
Preserve reusable method lessons only, such as "operation-level ready-list
construction should include active variant timing before tie-break scoring."
