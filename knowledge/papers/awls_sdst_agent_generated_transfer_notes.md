---
id: awls-sdst-agent-generated-transfer-notes
type: method-transfer
title: AWLS-SDST Method Transfer For Agent-Generated Solvers
tags: [fjsp, sdst, awls, agent-generated-solver, agent-generated-transfer, method-transfer, critical-block, tabu-search, zi]
status: active
---

# AWLS-SDST Method Transfer For Agent-Generated Solvers

## Purpose

Use this card when a coding worker must create or evolve a standalone
agent-generated FJSP-SDST solver from requirement and IO documents.

This is method guidance distilled from local AWLS-derived solvers. It is not a
solver implementation, not a complete code template, and not a benchmark target.
Do not copy local solver source, prior schedules, or instance-specific scores.
The worker should translate the ideas into small, auditable code changes under
the active IO and evaluator contract.

## Transfer Boundary

For agent-generated solvers, transfer AWLS as algorithm structure:

- complete representation: operation-to-machine assignment plus ordered
  operation sequence per machine;
- setup-aware decoder: every trial move must decode to all operations while
  respecting job precedence and machine setup arcs;
- critical-path and critical-block focus: generate local moves around operations
  that can affect makespan, not arbitrary all-pairs moves;
- bounded candidate windows: use AWLS RK/LK-style windows or critical-machine
  windows to reduce target insertion positions;
- tabu and aspiration: remember recently disrupted local sequences, but allow a
  tabu move if a fully decoded candidate strictly improves the incumbent;
- adaptive perturbation: use `zi`-like weights only as candidate ranking
  pressure after legality and useful neighborhoods exist.

Do not transfer AWLS as platform backend code. Do not import evaluator internals
or `harness_agent` helpers from an `examples/agent_generated*.py` runtime.

## Recommended Build Order

1. Recover a legal operation-level, setup-aware multi-start constructor.
2. Convert the best schedule into a stable representation:
   `assignment[(job_id, op_id)]` and `machine_sequences[machine_id]`.
3. Add one decoder that rebuilds start/end times from this representation and
   rejects partial, cyclic, duplicate, missing, or ineligible candidates.
4. Extract critical operations and machine critical blocks from the decoded
   incumbent.
5. Add one same-machine critical-block operator: adjacent swap, boundary move,
   or bounded insertion. Score only after full decode.
6. Add one alternate-machine insertion operator for critical operations with
   alternative eligible machines. Limit target positions by an RK/LK-like
   window or a small setup-aware insertion window, then full-decode candidates.
7. Add short-term tabu memory only after the move generator can repeatedly
   produce legal improving or tying candidates.
8. Add `zi`-style adaptive ranking only after critical-block and change-machine
   neighborhoods exist. `zi` should perturb candidate order, not replace
   makespan as the objective.

## Critical-Block Neighborhood Shape

AWLS-style same-machine search should not scan every pair first. A generated
solver can use this compact pattern:

- identify operations on at least one critical path or on the latest-finishing
  machine;
- group consecutive critical operations on the same machine into blocks;
- try moves at block boundaries before trying broad random relocation;
- for each candidate sequence, decode the whole schedule and require identical
  operation coverage;
- accept only strict makespan improvement unless a bounded stochastic search is
  explicitly implemented.

Setup time is part of the machine arc. Lower setup time alone is only a
tie-breaker or filter; it is not proof of improvement.

## RK/LK-Style Change-Machine Insertion

For an operation with an alternate eligible machine, avoid inserting into every
position on the target machine. A standalone solver can approximate AWLS RK/LK
windows with information available after decoding:

- predecessor readiness: the moved operation cannot start before its job
  predecessor completes;
- successor tail pressure: moves that delay a job successor on the critical
  suffix are riskier;
- target machine sequence: positions near operations whose end time is after
  predecessor readiness are usually more relevant than positions far earlier;
- fallback positions: keep a few boundary positions so the window does not
  become empty or over-pruned.

Use the window only to select candidates. The acceptance score must be the
fully decoded makespan under setup-aware machine arcs.

## Head/Tail And Proxy Scoring

AWLS uses head/tail information to rank candidate moves efficiently. In an
agent-generated solver, keep this conservative:

- use earliest start/end and optional remaining-job tail estimates to order a
  small top-k candidate list;
- use setup delta, bottleneck load, or critical-tail pressure as secondary
  ranking features;
- never promote a proxy score directly over decoded makespan;
- if a proxy disagrees with decoded quality repeatedly, preserve the decoder and
  adjust the candidate filter, not the evaluator contract.

## Tabu And Aspiration

Tabu memory is useful only when move quality is already reasonable.

- Store a short key for the affected local sequence, moved operation, source
  machine, and target machine.
- Keep tenure small and bounded by instance size or iteration count.
- Skip a tabu candidate unless full decode proves it strictly improves the
  incumbent makespan.
- Do not use tabu to accept partial schedules, lower setup-only candidates, or
  candidates that fail operation coverage.

## `zi`-Style Adaptive Pressure

The useful part of AWLS `zi` is adaptive pressure during stagnation, not a magic
formula. For generated solvers:

- features may include criticality, recent move frequency, bottleneck machine,
  setup-heavy adjacent arcs, and stagnation count;
- apply the perturbation to candidate ordering among legal candidate moves;
- decay or reset pressure after improvement;
- keep makespan primary and keep full-decoded acceptance.

Avoid changing only a constant multiplier or critical flag after the search has
plateaued. Without a real neighborhood, `zi` becomes random tie-breaking.

## Failure Patterns To Avoid

- Porting the full AWLS solver in one proposal.
- Rewriting the parser, evaluator, solution schema, or benchmark semantics.
- Importing backend `harness_agent` modules from standalone generated solver
  files.
- Replacing a promoted constructive skeleton instead of adding a bounded move
  around it.
- Mixing operation ids, `(job_id, op_id)` pairs, and schedule dictionaries in
  one decoder.
- Returning `[]`, `None`, or a partial schedule and scoring it as makespan `0`.
- Treating LB/UB/BKS, prior run scores, or prior solution files as solver
  inputs.
- Optimizing total setup time as the primary objective when the contract says
  makespan.

## Worker Self-Check

Before submitting a proposal, the worker should be able to say:

- which AWLS idea is being transferred and which existing incumbent mechanism
  is preserved;
- which representation the decoder accepts and returns;
- how the candidate window is bounded;
- how full operation coverage is verified after every trial move;
- why the change is one incremental operator rather than a solver rewrite;
- how Core can ablate the operator without changing IO or evaluator semantics.
