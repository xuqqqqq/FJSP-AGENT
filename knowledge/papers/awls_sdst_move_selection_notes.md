# AWLS-SDST Move Selection Notes

## Why This Slot Matters

The `awls_sdst_neighborhood_selection`, same-machine scoring, change-machine
scoring, zi, initialization, and portfolio slots have repeatedly produced legal
but non-improving `oddla20` candidates around the `1010` incumbent.  A narrower
next lever is the final move-selection layer inside `find_move`: candidates
have already passed closure-based legality and tabu filtering, but the solver
still decides whether to exact-recheck ranked moves, how to break approximate
ties, and when to diversify from `best_moves` to `all_moves`.

This slot should not create new moves.  It should select among already generated
move keys and keep Core evaluator promotion as the only quality authority.

## Worker Directions

- Preserve makespan as the primary objective.  Setup time may only be a bounded
  tie-breaker after exact makespan or approximate value.
- Keep exact checks bounded by `exact_select_top_k`, `ranked_moves`, `best_moves`,
  or a small deterministic subset of `all_moves`; do not run a local search loop
  inside move selection.
- If using exact evaluation, clone first: `trial = schedule.clone()`, then
  `trial.apply_move(Move(*move_key))`, then use `trial.makespan`.
- Consider SDST-specific tie-breakers only after legality and makespan pressure
  are preserved, for example preferring exact-equal moves that reduce local
  setup arcs while keeping the same exact makespan.
- Keep standard FJSP behavior close to baseline when
  `schedule.index.instance.has_sequence_dependent_setup` is false.

## Prior Failure Memory To Respect

- Lower setup time alone has often worsened makespan in neighborhood and
  same-machine experiments; do not promote setup over makespan.
- Exact same-machine trial scored as `trial.makespan + 0.001 * legacy` tied
  `1010`, and exact setup-only tie-breaks also tied.  A move-selection change
  must differ materially from those scoring-slot failures.
- Move-selection `min(3, len(best_moves))` exact rechecks tied `oddla20` at
  `1010` in two forms: only when `best_value > schedule.makespan`, and
  unconditionally when `ranked_moves` exact evaluation did not run.  Do not
  repeat that small best-moves exact-recheck pattern unchanged.
- A later move-selection worker proposal was legal and evaluator-backed but
  worsened `oddla20` from `1010` to `1030`.  It combined exact top-k setup
  tie-breaking with random noise in ranked values, a 10% skip of non-improving
  exact moves, and a 5% unconditional random `all_moves` escape.  Do not retry
  random-noise ranking or unconditional random escape as the main novelty.
- That same proposal attempted global setup scanning with an invalid
  `setup_time_between(sched.index, op1, op2)` shape and raw schedule nodes.
  Any setup lookup in this slot must use operation-key tuples and the canonical
  five-argument call
  `setup_time_between(schedule.index.instance, machine_id, previous_op, current_op, schedule.index)`.
- More exhaustive or longer is not automatically better; previous longer or
  more exhaustive probes tied or worsened.
- Do not use nonexistent APIs such as `schedule.setup_time`,
  `schedule.index.setup_time`, or `schedule.index.durations`.
- A later deterministic bottleneck tie-break proposal failed at runtime before
  quality evaluation.  It assumed `AwlsSchedule` exposes `schedule.operations`
  records with `.machine/.end` fields and unpacked move keys as
  `(move_type, op_key, target_machine)` with a string literal
  `"change_machine"`.  In this AWLS solver, move keys are
  `(method, which_node, where_node)` over integer graph nodes, and methods are
  constants `FRONT`, `BACK`, `CHANGE_MACHINE_FRONT`, and
  `CHANGE_MACHINE_BACK`.  Use `machine_sequences`, `on_machine`,
  `machine_predecessor/successor`, `end_time`, `backward_path_length`, and
  `makespan`; do not invent decoded operation records inside this slot.
- A follow-up exact-recheck plus global setup-sum tie-breaker also failed at
  runtime before quality evaluation because it used
  `idx.node_to_operation_key[node]`.  `OperationIndex` does not expose that
  mapping; this solver provides module-level `operation_key(schedule, node)`.
  When setup tie-breaks need operation keys, call `operation_key(schedule,
  node)` and skip sentinel/non-real nodes that return `None`.

## Guardrails

- Only edit `awls_sdst_move_selection`.
- Do not alter candidate generation, parser, evaluator, CLI arguments, solution
  schema, or benchmark score semantics.
- Do not append to `all_moves`, `ranked_moves`, or `best_moves` except by
  preserving existing flow; this slot should select, not generate.
- Do not use `schedule.operations` or treat `move_key` as an operation-key
  tuple; selection receives AWLS node ids, not decoded `(job_id, op_id)`
  records.
- Do not use `schedule.index.node_to_operation_key` or
  `idx.node_to_operation_key`; use `operation_key(schedule, node)`.
- Do not read files, LB/UB tables, evaluator outputs, environment variables, or
  network resources.
