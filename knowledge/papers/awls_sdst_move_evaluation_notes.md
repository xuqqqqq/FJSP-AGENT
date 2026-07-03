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
- A later worker candidate tried exact SDST scoring for every change-machine
  candidate by deep-copying the schedule, applying `Move(method, which, where)`,
  and ranking by `max(trial.end_time) + zi`.  It was legal but worsened
  `oddla20` from `1010` to `1032`, again with setup time `1840`.  Do not retry
  full exact scoring as the only move-evaluation change; it appears to steer
  the same short-budget search toward lower-setup but worse-makespan basins.
  If revisited, it needs a materially different gating rule such as applying
  exact scoring only to a small top-k set after the legacy proxy or combining
  it with a different candidate-generation/portfolio mechanism.
- A follow-up gated-exact proposal kept a function-local best proxy value,
  exact-scored only SDST candidates within `5%` of that proxy, and assigned a
  huge penalty to candidates outside the gate.  It was legal but worsened the
  current `critical + beta400/gamma40/theta5 + pct75` `oddla20` control line
  from `1010` to `1023`.  Do not retry proxy-ratio gates with function
  attribute state and outside-gate hard penalties as the main novelty; the
  gate can suppress useful change-machine moves even when it avoids the prior
  full-exact failure.
- After the accepted `sequence_length_biased_tenure` tabu-memory baseline,
  Core `oddla20` seed-0 under the 28s contract is `makespan=1002`, not the
  older `1010` line.  A later move-evaluation worker tried
  `setup_adjusted_nk_proxy`, adding destination setup arcs directly to the
  legacy change-machine proxy.  It failed before quality evaluation because it
  used `schedule.index.start_node`; `OperationIndex` has no `start_node`
  attribute.  Use module constant `START_NODE` for the source sentinel and
  `schedule.index.end_node` for the sink sentinel.  Do not retry this
  destination-setup proxy unchanged until the API is repaired and the
  hypothesis differs from simple setup insertion penalties.
- After the `START_NODE` guard was added, a legal follow-up worker candidate
  `critical_proximity_scaled_setup_penalty` used
  `critical_factor = min(1, base_proxy / makespan)` and scored
  `base_proxy + critical_factor * setup_sum`.  Core evaluation under the same
  `oddla20` seed-0 28s contract worsened the accepted `1002` incumbent to
  `1010`, with setup time rising from `1740` to `1910`.  Do not retry this
  critical-proximity setup-sum multiplier unchanged.  It also used
  `schedule.end_time[schedule.index.end_node]` as a makespan proxy; inside
  AWLS move-evaluation slots, use `schedule.makespan` or exact
  `trial.makespan` instead.
- A later hard-HUdata six-instance worker run proposed `setup_aware_path_proxy`,
  replacing the legacy NK proxy with a setup-aware forward/backward path
  estimate.  It failed before quality evaluation on all six instances with
  `TypeError: setup_time_between() takes from 4 to 5 positional arguments but 7
  were given` because the proposal passed raw `*_job, *_op, *_job, *_op`
  integers instead of operation-key tuples.  Do not retry this path-proxy
  implementation unless every setup lookup uses the canonical
  `setup_time_between(instance, machine_id, previous_op_tuple, current_op_tuple,
  schedule.index)` contract.
- A repaired follow-up worker candidate `insertion_setup_time_penalty` used the
  correct setup API and added a one-sided positive net insertion penalty
  (`penalty = max(new_setup - old_setup, 0)`) directly to the change-machine NK
  score.  It was legal on the hard HUdata-six 12s probe and improved `oddla09`
  from `1081` to `1059`, with setup time `980 -> 870`, but the aggregate
  worsened from `1304.17` to `1308.00` and average setup time increased from
  `1341.67` to `1383.33`.  Do not retry one-sided positive insertion setup
  penalties as the main novelty; they can help one instance while worsening
  the tight 20-job/5-machine hard group.
- Do not change parser/evaluator/IO semantics.
- Do not change N7 same-machine scoring until a separate slot is confirmed.
