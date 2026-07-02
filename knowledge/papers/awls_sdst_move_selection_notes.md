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
- More exhaustive or longer is not automatically better; previous longer or
  more exhaustive probes tied or worsened.
- Do not use nonexistent APIs such as `schedule.setup_time`,
  `schedule.index.setup_time`, or `schedule.index.durations`.

## Guardrails

- Only edit `awls_sdst_move_selection`.
- Do not alter candidate generation, parser, evaluator, CLI arguments, solution
  schema, or benchmark score semantics.
- Do not append to `all_moves`, `ranked_moves`, or `best_moves` except by
  preserving existing flow; this slot should select, not generate.
- Do not read files, LB/UB tables, evaluator outputs, environment variables, or
  network resources.
