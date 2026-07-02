# AWLS-SDST Adaptive Weight Update Notes

## Why This Slot Matters

The `awls_sdst_zi_features` and AWLS-ZI formula rounds exposed setup-aware
features, but the incumbent `oddla20` run still uses `zi_policy=critical` and
plateaus at `makespan=1010`.  Formula attempts that multiplied `base` by
critical setup ratios tied or worsened the short-budget search.  A different
lever is the adaptive operation-weight update after each accepted move:
`op_weight` and `op_cooldown` decide which operations receive future zi
perturbation pressure inside N7/NK scoring.

This slot should change search pressure only.  It must not create moves, alter
machine sequences, change evaluator semantics, or use setup time as the
objective.

## Worker Directions

- Preserve makespan pressure.  Use `previous_makespan`, `current_makespan`, and
  `best_makespan_before` as the primary signals for whether perturbation should
  increase, cool down, or reset.
- For SDST only, consider bounded adjacent-setup pressure for `moved_node` and
  nearby machine predecessors/successors, but use it as a small pressure signal
  rather than a replacement objective.
- Prefer mechanisms that change cooldown/weight dynamics rather than another
  formula that simply scales `base` by `is_critical * setup_*_ratio`.
- Keep standard FJSP behavior close to the current update when
  `schedule.index.instance.has_sequence_dependent_setup` is false.
- If setup lookup is needed, use:

```python
prev_op = operation_key(schedule, predecessor)
cur_op = operation_key(schedule, moved_node)
setup = setup_time_between(schedule.index.instance, machine_id, prev_op, cur_op, schedule.index)
```

## Prior Failure Memory To Respect

- Pure critical multipliers and small cooldown/critical variants tied or
  worsened `oddla20`; do not retry only increasing moved critical operations.
- `sdst_cooldown_damping` tied `oddla20` at `makespan=1010`: on SDST
  instances it reduced cooldown accumulation for non-critical operations when
  makespan stalled, using only the binary SDST flag and no setup topology.
- `sdst_fast_cooling_noncritical` also tied `oddla20` at `makespan=1010`: it
  inverted the previous idea by cooling non-critical operations faster on SDST
  stalls and cooling all operations faster after improvement.
- Setup-ratio formulas that multiply `base` by critical setup pressure mostly
  tied or worsened, including adjacent, successor, predecessor, and
  backward-gated variants.
- Lower setup time alone has repeatedly failed to improve makespan in
  move-evaluation, same-machine, and initialization experiments.
- `same_machine_eval=cpp-fast` worsened `1010 -> 1039`; do not combine this
  slot with that switch as the sole novelty.
- Reset after a new best makespan is important.  Do not leave stale high weights
  active after `current_makespan < best_makespan_before` unless the hypothesis
  explicitly explains a safe bounded alternative.

## Guardrails

- Only edit `awls_sdst_weight_update`.
- Mutate only `schedule.op_weight` and `schedule.op_cooldown` for real operation
  nodes.
- Do not call `schedule.apply_move`, `trial.apply_move`, `find_move`,
  `tabu_search`, `solve_awls`, evaluator code, subprocesses, file IO, or
  random APIs.
- Do not mutate `machine_sequences`, job/machine predecessor/successor links,
  `on_machine`, `start_time`, `end_time`, `makespan`, parser, evaluator,
  solution schema, CLI, or benchmark semantics.
