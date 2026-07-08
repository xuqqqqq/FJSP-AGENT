---
id: fjsp-sdst-agent-generated-search-memory-20260707
type: local-experiment-memory
title: FJSP-SDST Agent-Generated Solver Search Memory 2026-07-07
tags: [fjsp, sdst, agent-generated-solver, local-search, dispatch-rule, memory]
status: active
---

# FJSP-SDST Agent-Generated Solver Search Memory

## Purpose

Use this card when a worker is asked to create or improve an
agent-generated FJSP-SDST solver rather than adapting the existing AWLS slot
line.  It records method-level evaluator-backed lessons from recent
FJSP-SDST worker loops so the worker does not relearn the same basic
structure every run.

This card is prompt memory, not a hardcoded solver.  Score remains
`-makespan`; LB/UB/BKS are diagnostics only.  Do not copy any previous
schedule, seed result, or instance-specific target value from this card.

## Local Method Evidence

Recent worker-loop artifacts produced the following reusable method lessons:

- Job-by-job greedy construction is a weak default for SDST-HUdata-like cases
  because it can schedule too many operations from one job before comparing
  other ready jobs.
- Operation-level setup-aware dispatch is the first structure to recover:
  maintain one ready next operation per unfinished job, compare all ready
  operation and machine choices, and include the setup induced by the current
  last job on the candidate machine.
- Earliest-start or earliest-finish dispatch becomes more useful when combined
  with seeded random tie-breaking, small RCL choice, or multi-start restarts,
  because the method explores different operation interleavings without
  changing the parser/evaluator contract.
- Feasible relocation/tabu/local-search layers can still be weak if they are
  built on top of a job-order greedy skeleton.  Recover the operation-level
  setup-aware constructive skeleton before adding complex neighborhoods.
- Insertion/all-pair local search is risky unless the decoder keeps one
  operation representation end to end.  Past failed attempts mixed global
  operation ids, `(job, op)` pairs, and schedule dictionaries in
  `machine_sequences`/`op_info`.
- A multi-seed wrapper alone can be legal but may only replay the same search
  basin.  Treat it as a diversification wrapper around a useful construction
  or neighborhood, not as the whole improvement idea.
- Recent agent-generated SDST loops showed that legality repairs can dominate
  the early search.  Once Core promotes a machine-id representation repair, do
  not remove or invert that repair in the next round.  Improve around it by
  changing dispatch, restart, or neighborhood logic while preserving valid
  machine indices and setup lookup consistency.
- A reusable constructive pattern is: normalize raw machine identifiers into a
  contiguous internal machine-index space, keep setup lookup aligned with that
  internal representation, then run setup-aware operation-level dispatch with a
  small RCL and enough seeded restarts to explore interleavings.  This is a
  method lesson, not an instruction to target a particular instance score.
- Earliest-start dispatch can be a useful alternative to earliest-finish once
  legality is stable, because it front-loads ready operations while still
  accounting for sequence-dependent setup.  Treat it as a promoted constructive
  skeleton when Core validates it; later rounds should add bounded local search
  or focused perturbations rather than reverting parser/indexing assumptions.

Treat these as local learning signals.  They do not prove a global algorithmic
ranking, but they show which structures the current worker should preserve or
recover before trying more complex neighborhoods.

## What To Preserve Or Recover First

If the current agent-generated baseline is job-by-job greedy or schedules most
operations of one job before considering other ready jobs, first mutate it into
an operation-level list scheduler:

- Maintain the set of ready next operations, one per unfinished job.
- For every ready operation, evaluate eligible machines using job ready time,
  machine available time, and SDST setup from the last job on that machine.
- Select by earliest feasible start or earliest finish; keep seeded random
  tie-breaking or a small RCL so multiple seeds/restarts explore different
  operation interleavings.
- Run multi-start restarts and return the best complete valid schedule.

Observed local lesson: weak job-order greedy baselines improved once the worker
moved to operation-level setup-aware dispatch and multi-start exploration.
Complex local search on top of a weak job-order baseline can still remain far
from the stronger skeleton.

## What Not To Forget

When a run has already found a strong constructive skeleton, do not replace it
with an unrelated solver or a weaker job-order policy.  Improve by adding a
bounded operator around the incumbent:

- Preserve setup-aware operation-level dispatch, multi-start, and seeded
  tie-breaking unless loop feedback shows they are the direct failure source.
- Preserve Core-promoted machine-id normalization or offset mapping unless the
  next proposal provides an explicit ablation with a legality-preserving
  fallback.  Removing normalization after it fixed evaluator legality usually
  reintroduces out-of-range machine indices or "machine is not a candidate"
  failures.
- Add local search as a post-processing or per-restart intensification layer.
- Keep the incumbent schedule if the local search fails, times out, or produces
  no strict makespan improvement.

## Reusable Method Template From Local Solvers

Local non-agent SDST solvers in this workspace show a method shape that is
worth translating into agent-generated code as ideas, not copied source:

- Represent a complete solution as an operation-to-machine assignment plus one
  ordered operation sequence per machine.
- Decode that representation with a forward/topological feasibility pass:
  every operation start must respect its job predecessor and the previous
  operation on the same machine, including sequence-dependent setup.
- Build initial complete schedules with setup-aware operation-level dispatch or
  randomized greedy multi-start, then keep the best complete valid schedule.
- Improve only by complete-schedule moves: same-machine relocation/insertion
  around critical or high-finish operations, alternate-machine insertion for
  eligible operations, and small destroy-repair moves that decode back into all
  operations.
- Use tabu or short-term move memory to avoid undo loops.  Use aspiration only
  when a fully decoded candidate strictly improves the incumbent makespan.
- Score candidates by decoded makespan first.  Setup reduction, machine load,
  bottleneck pressure, or critical-tail features are tie-breakers, not primary
  objectives.

For an agent-generated solver, this template should become a compact decoder
and one bounded neighborhood.  Do not attempt to port the full AWLS code in one
proposal; first create a legal fixed-sequence decoder, then add one local move
operator with operation-coverage checks.

## Local Search Quality Contract

Good FJSP-SDST local search must be feasibility-preserving before it is
objective-improving:

- Candidate schedules must contain exactly the same `(job_id, op_id)` set as
  the incumbent schedule.
- Any decoder deadlock, precedence cycle, missing operation, or partial
  schedule is infeasible.  Skip it; never score it as makespan `0`.
- Decode fixed machine sequences with a topological/forward feasibility guard
  that respects both job precedence and machine order with setup arcs.
- Bound the neighborhood and runtime.  A local search that times out in the
  one-seed smoke gate is not useful even if the idea is plausible.
- Prefer critical-machine or critical-block candidates before scanning every
  operation on every machine.

Risk patterns already observed:

- Returning `[]` or a partial schedule from a decoder and comparing it as
  `0` makespan.
- Reordering machine sequences without propagating precedence to downstream
  operations.
- Mixing operation representations inside local-search decoders.  If
  `machine_sequences` stores `(job, op)` pairs, every trial move and
  `op_info` lookup must use that exact shape; do not mix in global operation
  ids or full schedule dictionaries.  Recent bad proposals crashed with
  `TypeError` such as "cannot unpack non-iterable int object" and "tuple
  indices must be integers or slices, not str".
- Long inline helper functions inserted after `def main():`, which breaks
  Python structure.  Use `insert_before` for top-level helpers or create a
  small helper file plus a compact import/call patch.
- Long guessed `text_replace.old` blocks.  If the exact block is uncertain,
  use a small stable anchor, `insert_before`/`insert_after`, or a helper file.

## Promising Next Directions

After recovering the strong constructive skeleton, prefer one of:

- Critical machine/block adjacent swap or insertion with a complete decoder.
- Bounded same-machine insertion that only tests precedence-feasible positions
  and keeps the best complete schedule.
- Critical-operation alternate-machine reassignment followed by greedy
  setup-aware insertion on the target machine.
- Small destroy-repair or RCL perturbation followed by the existing multi-start
  constructive rule, with operation coverage checked before acceptance.

Avoid spending many rounds on pure tie-break tweaks once makespan is already
stable, unless the tweak is tied to a clear local failure mode from
`loop_feedback`.
