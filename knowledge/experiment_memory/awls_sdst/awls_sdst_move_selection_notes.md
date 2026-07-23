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
- After the node conversion contract was fixed, the worker generated a legal
  `exact_setup_tie_breaker`: exact-check top-k `ranked_moves`, key by
  `(trial.makespan, total_setup_sum, approx_value, move_key)` using
  `operation_key(schedule, node)` and `setup_time_between(...)`, remove the
  3% random all-moves escape, and otherwise keep random choice among
  `best_moves`.  Core evaluation tied `oddla20` at `1010`.  Do not retry a
  full machine-sequence/global setup-sum tie-break as the main novelty; it is
  now legal but non-improving under the current incumbent controls.
- After `global_sdst_cooldown_boost` was absorbed, a move-selection worker tried
  `local_setup_tie_breaker`: exact top-k by `trial.makespan`, adjacent setup arc
  tie-break, and deterministic `best_moves` fallback.  It failed before quality
  evaluation because it called `trial.on_machine.get(...)` and
  `trial.machine_predecessor.get(...)`.  These AWLS schedule fields are lists,
  not dictionaries; use indexed access such as `trial.on_machine[node]` and
  treat predecessor/successor sentinels as `-1`, not `None`.
- A repaired `machine_local_setup_tie_break` still failed before quality
  evaluation because it set `machine_id = move_key[2]` and then indexed
  `trial.machine_sequences[machine_id]`.  The move key is
  `(method, which_node, where_node)`; `where_node` is a graph node, not a
  machine id.  After applying `Move(*move_key)` to a clone, derive machine id
  from list state such as `trial.on_machine[move.which]`.
- After the accepted `critical_sdst_capped_tenure_jitter` tabu-memory rule, a
  move-selection worker proposed `deterministic_same_machine_preference`: keep
  exact top-k if present, then sort `best_moves` deterministically with
  same-machine methods before change-machine methods, remove the baseline
  random choice/3% random escape, and exact-check a sorted first-three fallback
  only when `best_moves` is empty.  It was legal but worsened the hard
  HUdata-six 12s probe from `1297.17` to `1304.83`.  `oddla09` improved
  (`1081 -> 1062`) but the aggregate regressed, suggesting deterministic
  same-machine-first exploitation reduces useful diversification.  Do not
  retry same-machine-first deterministic sorting or removing the random escape
  as the main novelty unchanged.
- A later `criticality_tie_break_exact_selection` proposal failed before
  quality evaluation because it treated AWLS schedule list fields as callable
  or dictionary-like objects: `sched.machine_sequences.items()`,
  `sched.machine_successor(last_node)`, `sched.job_successor(last_node)`,
  `sched.job_predecessor(curr)`, and `sched.machine_predecessor(curr)`.
  `machine_sequences` is a list to scan with `enumerate(...)`, and
  predecessor/successor links are lists to read with `[node]` while treating
  the missing sentinel as `-1`, not `None`.

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
- Do not call `.get(...)` on `on_machine`, `machine_predecessor`,
  `machine_successor`, `job_predecessor`, or `job_successor`; they are lists.
- Do not call `.items()` on `machine_sequences`; scan it with
  `enumerate(schedule.machine_sequences)`.
- Do not call predecessor/successor lists as functions such as
  `job_successor(node)`; use indexed access such as `job_successor[node]`.
- Do not treat `move_key[2]` as a machine id; it is `where_node`.
- Do not retry exact top-k selection whose main novelty is a global setup-sum
  tie-break after exact makespan; the legal version tied `oddla20` at `1010`.
- Do not retry deterministic same-machine-first sorting that removes random
  tie-breaking/escape without a stronger exact or acceptance-pressure signal.
- Do not read files, LB/UB tables, evaluator outputs, environment variables, or
  network resources.
