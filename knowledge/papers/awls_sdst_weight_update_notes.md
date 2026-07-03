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
- A later worker proposed `sdst_cooldown_accel`, accelerating moved-node
  cooldown when adjacent setup ratio was high.  The platform blocked it before
  evaluation because it reached into schedule structure (`machine_sequences`,
  `on_machine`) and used nonexistent APIs (`schedule.operation_key`,
  `schedule.index.setup_time_between`).  Future weight-update proposals should
  either stay with in-scope signals (`moved_node`, criticality, makespan
  progress, existing op_weight/op_cooldown) or use the documented module-level
  `operation_key(schedule, node)` / `setup_time_between(...)` pattern without
  mutating or depending on machine-sequence structure.
- A later `sdst_guided_cooldown_penalty` proposal described adding extra
  cooldown to all high-setup non-critical operations, but semantic repair could
  not produce an acceptable slot replacement.  The proposal was rejected before
  evaluator execution with no changed files.  Future rounds must return one
  concrete `replace_slot_block` edit, and they should not depend on scanning or
  rewriting schedule topology merely to penalize globally high-setup nodes.
- A confirmed-slot worker candidate `global_sdst_cooldown_boost` was promoted
  on the `oddla20` Core worker-loop contract with
  `restarts=2`, `cycles=1000`, `iterations=10000`, `time_limit_sec=28`,
  `init=mixed`, `critical + beta400/gamma40/theta5 + pct75`, and no
  portfolio lanes.  It uses only the SDST boolean flag: the moved node cools
  one step faster, while other non-critical nodes gain one less cooldown on
  SDST instances.  Core promotion saw `1039 -> 997`, but manual reruns showed
  wall-clock sensitivity: the same no-portfolio contract reproduced
  `1039 -> 1031/1022`, and the same portfolio lanes
  `0:mixed:1:6,5:greedy:1:6,10:random:1:6,13:mixed:1:6` reproduced
  `1030 -> 1023`.  The old `20 cycles / 300 iterations / 20s` short line
  remained `1010 -> 1010`.  Treat this as a useful accepted pressure rule, not
  as a stable proof that `997` is always reached.

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
- Do not return only a natural-language hypothesis.  The worker must emit one
  concrete slot replacement or a concrete contract blocker in `risk_notes`.
- Do not cite the one-shot `997` result without the rerun caveat; independent
  verification showed strict improvement on the 28s worker contract but not a
  stable UB hit on every run.
