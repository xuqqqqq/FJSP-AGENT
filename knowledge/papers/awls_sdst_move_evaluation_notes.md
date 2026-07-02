# AWLS-SDST Move Evaluation Notes

## Why The Current Makespan Is Poor

Stage-1 AWLS-SDST timing made schedules legal, but the search still ranks many
change-machine NK moves with the original processing-time AWLS proxy.  On SDST
instances this can select a move whose processing tail looks shorter while the
destination sequence adds expensive setup arcs.

The fixed Core evaluator remains the authority: score is `-makespan`; LB/UB are
diagnostics only.

## Worker Directions

Use these directions as hypotheses, not as a manual patch:

- Setup-delta scoring: compare setup arcs removed from the source machine and
  setup arcs added around the insertion target on the destination machine.
- Setup-aware exact candidate scoring: clone the schedule, apply the candidate
  move, and score by setup-aware `trial.makespan` when the candidate count and
  time budget are small enough.
- Hybrid scoring: keep the existing AWLS R/Q proxy for standard FJSP, and add a
  setup penalty or exact SDST override only when
  `instance.has_sequence_dependent_setup` is true.
- Avoid pure least-setup greediness; it can harm critical-path progress.  The
  move score still needs to reflect makespan or tail risk.

## Guardrails

- Only edit `awls_sdst_move_evaluation`.
- Reuse `setup_time_between`; do not parse setup matrices.
- Import `setup_time_between` locally inside the slot before using it.  A prior
  candidate failed at runtime with `NameError: setup_time_between is not
  defined`.
- `method` is a string constant (`CHANGE_MACHINE_FRONT` or
  `CHANGE_MACHINE_BACK`), not an integer.  A previous candidate compared it with
  `2/3` and therefore skipped the intended branch.
- Do not call `setup_time_between` with `current_op=None`.  A previous candidate
  crashed when a moved operation had no machine successor.  Missing source or
  target edges should contribute zero setup for that edge.
- A robust next hypothesis is exact SDST candidate scoring inside the slot:
  compute the legacy approximate score first, then for SDST only clone the
  schedule, apply `Move(method, which, where)`, and use `trial.makespan` plus a
  small legacy tie-breaker.  Catch `ValueError`/`KeyError` and fall back to the
  legacy score or a very poor score.
- Use `trial.makespan` after `trial.apply_move(...)`.  A prior exact-scoring
  candidate incorrectly used `trial.end_time[trial.index.end_node]`; AWLS does
  not maintain the synthetic end node's end time as the schedule makespan.
- After this slot was exposed as `awls_sdst_move_evaluation`, a legal
  failure-memory-guided candidate added a conservative `0.5 * setup_delta`
  penalty to the legacy NK proxy.  On `oddla20` under the current
  `critical + beta400/gamma40/theta5 + pct75` incumbent controls, it worsened
  makespan from `1010` to `1030` while reducing setup time from `1900` to
  `1840`.  Do not retry a simple linear setup-delta penalty as the only change;
  lower setup time alone is not improvement evidence.
- Do not change parser/evaluator/IO semantics.
- Do not change N7 same-machine scoring until a separate slot is confirmed.
