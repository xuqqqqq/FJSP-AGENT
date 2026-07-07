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
line.  It records local evaluator-backed lessons from recent `oddla20` worker
loops so the worker does not relearn the same basic structure every run.

This card is prompt memory, not a hardcoded solver.  Score remains
`-makespan`; LB/UB/BKS are diagnostics only.

## Local Evidence

Recent web worker-loop artifacts:

- `outputs/web_runs/20260707_152030_4827124b`: agent-generated baseline
  `3817`, promoted to `1138` after operation-level earliest-finish dispatch,
  multi-start tie exploration, and a composite finish/setup score.
- `outputs/web_runs/20260707_160522_64c9d0ee`: agent-generated baseline
  `3817`, promoted to `1096`; the final promoted solver was setup-aware
  operation-level earliest-start dispatch with random tie-breaking across
  seeds.
- `outputs/web_runs/20260707_180845_6335f433`: stronger baseline `1146`,
  promoted to `1096` by adding multi-start restarts.
- `outputs/web_runs/20260707_195235_36182a23`: baseline `3817`, promoted to
  `1733` through feasible relocation/tabu/multi-start local search, but it did
  not recover the stronger operation-level EST multi-start skeleton.
- `outputs/web_runs/20260708_005553_12189369`: baseline repair promoted to
  `1131` by recovering setup-aware operation-level dispatch, then to `1102`.
  Rounds 7-10, 12, 17, and 18 repeatedly tried insertion/all-pair local search
  and failed at runtime because the new decoder mixed global operation ids,
  `(job, op)` pairs, and schedule dictionaries in `machine_sequences`/`op_info`.
  A later multi-seed wrapper was legal but tied the incumbent at `1102`.

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

Observed local lesson: weak job-order greedy baselines around `3817` improved
dramatically once the worker moved to operation-level setup-aware dispatch and
multi-start exploration.  Complex local search on top of a weak job-order
baseline can still remain far from the stronger skeleton.

## What Not To Forget

When a run has already found a strong constructive skeleton, do not replace it
with an unrelated solver or a weaker job-order policy.  Improve by adding a
bounded operator around the incumbent:

- Preserve setup-aware operation-level dispatch, multi-start, and seeded
  tie-breaking unless loop feedback shows they are the direct failure source.
- Add local search as a post-processing or per-restart intensification layer.
- Keep the incumbent schedule if the local search fails, times out, or produces
  no strict makespan improvement.

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
