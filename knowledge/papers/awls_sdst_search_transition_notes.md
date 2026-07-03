# AWLS-SDST Search Transition Notes

## Why This Slot Matters

The current `oddla20` / `la20` incumbent reaches `makespan=1010` against UB
`997`, but many SDST-aware scoring, initialization, portfolio, and cooldown
changes only tie or worsen that value.  The tabu-search loop still uses a very
simple state transition: after a legal move is applied, update operation
weights, then clone `current` into `best` only when makespan strictly improves.

This slot exposes that transition point.  It is a search-control lever, not a
license to rewrite move generation or validation.  Good hypotheses should
change plateau handling, intensification, or bounded backtracking while keeping
Core evaluator makespan as the only promotion authority.

## Worker Directions

- Preserve the invariant that `best` is the lowest makespan seen in this
  `tabu_search` call.
- Consider deterministic plateau handling for SDST, for example cloning `best`
  back into `current` after a bounded number of non-improving moves, or
  recording plateau counters in `stats` that future portfolio labels can audit.
- If using randomness, use only the in-scope seeded `current.rng` and keep the
  rule bounded.
- Prefer rules tied to makespan progress and iteration count.  Setup time can
  appear only as a diagnostic idea outside this slot; do not optimize setup
  time instead of makespan.
- Keep the default behavior for standard FJSP close to baseline unless the
  hypothesis explicitly explains why the transition rule is general.

## Prior Failure Memory To Respect

- Pure zi/weight/cooldown tweaks around the `1010` incumbent have repeatedly
  tied or worsened.  A transition proposal should not be just another critical
  multiplier or cooldown-rate change hidden in this slot.
- `bounded_plateau_reset` worsened `oddla20` from `1010` to `1033`: it counted
  consecutive moves with `current.makespan > best.makespan` and reset
  `current = best.clone()` after `100` such steps.  It reduced setup time to
  `1740`, but Core promotion is makespan-only.
- `bounded_interval_plateau_reset` also worsened `oddla20` from `1010` to
  `1023`: it reset to best after `200` iterations without improvement, capped
  at three resets per `tabu_search` call.  Do not retry fixed-interval or
  consecutive-worsening best resets unchanged.
- `probabilistic_plateau_restart` legally tied `oddla20` at `1010`: it tracked
  `stats['plateau_steps']`, then after 50 non-improving steps used
  `current.rng.random() < 0.2` to reset `current = best.clone()`, capped at
  five resets.  Do not spend another round on best-clone plateau restarts that
  only vary the threshold, probability, or reset cap.
- After `global_sdst_cooldown_boost`, `rng_perturbed_plateau_reset` also tied
  `oddla20` at `1010`: it reset `current = best.clone()` after 50
  non-improving steps, capped at five resets, and consumed one seeded
  `current.rng.random()` after cloning to perturb the future trajectory.  Setup
  time dropped from `1890` to `1870`, but makespan did not improve.  Do not
  retry best-clone plateau restarts whose main novelty is only rng
  perturbation, stats guarding, or threshold/cap adjustment.
- `degradation_threshold_reset` worsened `oddla20` from `1010` to `1180`: it
  reset `current = best.clone()` whenever `current.makespan > 1.01 *
  best.makespan`.  Although setup time fell from `1940` to `1700`, the
  makespan degradation was severe.  Do not retry relative-makespan degradation
  best resets such as 1%, 2%, or 5% gap thresholds.
- Portfolio best-lane reruns, seed remapping, multi-scramble restarts, and
  setup-ratio best-lane exploitation all tied `1010`; do not reproduce those
  as an in-loop transition without a materially different state rule.
- Lower aggregate setup time alone has not predicted makespan improvement in
  same-machine scoring, move evaluation, initialization, and neighborhood
  selection.  Do not accept or preserve worse makespan because setup time is
  lower.
- Over-pruning the search, such as gating change-machine moves behind empty
  same-machine candidates, collapsed selected moves and worsened quality.  A
  transition rule should not break the active move stream by returning early or
  ending the loop.

## Guardrails

- Only edit `awls_sdst_search_transition`.
- Do not call `find_move`, `tabu_search`, `solve_awls`, `solve_awls_single`,
  evaluator code, parser code, subprocesses, file IO, network, or environment
  variables.
- Do not call `current.apply_move`, `trial.apply_move`, or `add_move_tabu` in
  this slot.
- Do not directly mutate machine sequences, predecessor/successor links,
  `on_machine`, start/end times, or makespan.  Assign whole
  `AwlsSchedule.clone()` results only.
- Do not promote a worse makespan into `best`.
- `stats` is optional in `tabu_search`; guard all stats writes with
  `if stats is not None:`.
