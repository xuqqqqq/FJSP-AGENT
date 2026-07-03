# AWLS-SDST Tabu Memory Notes

## Why This Slot Matters

The current `oddla20` / `la20` incumbent is `makespan=1010` against UB `997`.
Many SDST-aware scoring, initialization, transition, portfolio, and weight
rules have failed to improve it.  One remaining search-control lever is tabu
memory itself: which local machine sequence is made tabu after an accepted
move, and how long it remains tabu.

The baseline uses the C++-style reverse/local sequence and a seeded random
tenure in `[tenure_min, tenure_max]`.  For SDST, moves that disturb high-setup
machine arcs may need a different bounded tenure or local sequence, but this
must stay inside the existing `SequenceTabuList` mechanism.

## Worker Directions

- Keep `add_move_tabu` as a small bookkeeping function.  It should compute one
  machine id, one local sequence, and call `tabu.add(machine_id, sequence,
  expires_at)` once.
- Preserve deterministic seeded behavior by using only `schedule.rng` if
  randomization is needed.
- If using setup information, use `setup_time_between` with operation-key
  tuples and the current machine id.  Setup can shape tenure length only; it
  must not become the objective.
- Prefer bounded tenure changes tied to move type, current criticality, or SDST
  setup pressure.  Do not add nested search, exact trials, or evaluator calls.
- Keep standard FJSP close to the baseline unless the hypothesis explicitly
  explains a general tabu-memory reason.

## Prior Failure Memory To Respect

- Direct scoring/setup-time reductions often lowered setup time but worsened
  makespan.  A tabu-memory rule should not simply maximize setup reduction.
- `criticality_biased_tenure` tied `oddla20` at `makespan=1010`: it kept the
  baseline local sequence but drew tenure from the upper half of
  `[tenure_min, tenure_max]` for critical moved operations and lower half for
  non-critical moves.  Do not retry criticality-only tenure splitting
  unchanged.
- `sdst_setup_change_biased_tenure` also tied `oddla20` at `makespan=1010`:
  it tried to bias tenure by absolute setup-change ratio.  The proposal used
  `has_sequence_dependent_setup()` and `schedule.index.operation_key`, which
  are not real APIs, so future setup-aware tabu-memory proposals must use
  `schedule.index.instance.has_sequence_dependent_setup` and the module-level
  `operation_key(schedule, node)`.
- `move_type_sequence_and_tenure_bias` was legal but worsened `oddla20` from
  `1010` to `1039`: it shortened FRONT/BACK tabu memory to only
  `[move.which, move.where]` or `[move.where, move.which]`, assigned shorter
  local-move tenure, and longer machine-change tenure.  Do not retry this
  short FRONT/BACK sequence plus move-type tenure split unchanged.
- `merit_based_tenure_with_sdst` failed at runtime before evaluation: it used
  `setup_time_between(...)` inside the slot without importing it from
  `harness_agent.standard_fjsp`, and it attempted `schedule.is_critical(...)`
  even though the real AWLS API is `schedule.is_critical_operation(node)`.
  Future setup-aware tabu memory proposals must import `setup_time_between`
  locally and use `schedule.is_critical_operation`.
- `criticality_proportional_tenure_with_expanded_sequence` was legal but
  worsened `oddla20` from `1010` to `1072`: it expanded the tabu sequence by
  adding adjacent predecessor/successor nodes and set tenure from the fraction
  of critical operations in that expanded sequence.  It reduced setup time from
  `1940` to `1720`, again without improving makespan.  Do not retry expanded
  tabu sequence plus criticality-fraction tenure unchanged.
- A later confirmed-slot worker candidate `sequence_length_biased_tenure` was
  promoted by Core on `oddla20` under the 28s incumbent contract after
  `global_sdst_cooldown_boost`: it preserved the baseline tabu sequence but
  added a bounded tenure bias proportional to `len(sequence) / 20`, capped by
  `tenure_max`.  Core improved makespan from `1007` to `1002` with the same
  `critical + beta400/gamma40/theta5 + pct75` AWLS controls.  Treat this as
  the current accepted tabu-memory pressure rule.
- A later main-tree repeat probe after `sequence_length_biased_tenure` showed
  that UB `997` is reachable but not stable under the same `oddla20` seed-0
  28s wall-clock contract: five evaluator-valid repeats produced makespans
  `997, 1002, 1002, 1002, 1002` with setup times `1720, 1740, 1740, 1740,
  1740`.  Use this as evidence that a one-shot UB hit is a useful search
  signal, not sufficient promotion proof for noisy AWLS/SDST changes.  Future
  worker loops should prefer repeat promotion checks or multi-seed evidence
  before claiming stable UB attainment.
- A follow-up continuation candidate `target_machine_tabu_for_change_moves`
  moved change-machine tabu records from the source machine to the target
  insertion machine and used midpoint deterministic tenure for change moves.
  It was legal but regressed the 28s `oddla20` Core line from `1002` to
  `1010`, so do not retry target-machine change-move tabu with midpoint tenure
  unchanged.
- A later hard-HUdata `oddla12/oddla14/oddla15` worker run proposed
  `critical_setup_pressure_tenure`, blending `schedule.is_critical_operation`
  with adjacent setup ratios while preserving the baseline tabu sequence.  It
  failed before quality evaluation on all three instances because it imported
  `operation_key` from `harness_agent.standard_fjsp`; `operation_key` is a
  module-level helper in `examples.standard_fjsp_awls_solver`, not a
  `standard_fjsp` export.  The same code also used nonexistent `move.duration`;
  `Move` has only `method`, `which`, and `where`.  Use
  `schedule.index.duration(move.which, schedule.on_machine[move.which])` for
  moved-operation processing time.
- The guarded retry `setup_delta_criticality_tenure` was promoted by Core on
  the hard subset `oddla12/oddla14/oddla15`: average makespan improved from
  `1332.67` to `1327.00`, with uneven effects (`oddla12` worsened while
  `oddla14` and `oddla15` improved).  A full HUdata20 seed-0 30s run was legal
  on all 20 instances and moved average makespan only from `1014.85` to
  `1014.50` (`avg_gap_pct` `6.9276` to `6.8607`): 11 instances improved, 2 tied,
  and 7 worsened.  Largest gains were `oddla02 -36` and `oddla14 -27`; largest
  regressions were `oddla13 +32` and `oddla16 +32`.  Treat this as a weak,
  uneven search-control signal, not a stable global SDST breakthrough.
- A follow-up hard-HUdata worker run on
  `oddla09/oddla11/oddla12/oddla13/oddla14/oddla15` generated
  `critical_sdst_capped_tenure_jitter`.  It preserves the existing local tabu
  sequence and changes only tenure: critical moves map normalized setup delta
  into the bounded `[tenure_min, tenure_max]` range with small seeded jitter,
  while non-critical moves retain baseline random tenure.  The Core worker-loop
  repeat gate promoted it on the 6-instance 12s probe (`1303.33 -> 1297.17`).
  Full HUdata20 seed-0 30s candidate-worktree reruns were legal and averaged
  `1013.25` then `1013.10`, compared with the previous saved mainline
  `1014.50` and a fresh mainline rerun `1016.05`.  Effects remain uneven
  (`oddla13/15/16/19` improved while `oddla02/05/11` regressed), so treat this
  as another weak positive tabu-memory signal rather than a stable UB-reaching
  method.
- A continuation hard-HUdata six-instance 12s worker run proposed
  `load_adaptive_tabu_sequence_and_tenure`.  It tried to compute source-machine
  load with `schedule.machine_head[machine_id]` and total operations with
  `schedule.jobs`.  The proposal failed before quality evaluation on all six
  instances with `AttributeError: 'AwlsSchedule' object has no attribute
  'machine_head'`; `AwlsSchedule` also has no `jobs` attribute.  Do not retry
  load-adaptive tabu rules that scan nonexistent `machine_head`/`jobs`
  structure.  If load or sequence context is needed inside this slot, derive it
  from `candidate_tabu_sequence_parts(...)`, `candidate_tabu_sequence(...)`,
  `schedule.machine_predecessor`, `schedule.machine_successor`,
  `schedule.on_machine`, and `schedule.index.instance` fields that actually
  exist.
- Fixed best-reset transition rules worsened `oddla20` to `1033` and `1023`;
  do not emulate restart/backtrack behavior inside tabu memory.
- Portfolio seed remapping, multi-scramble restarts, and best-lane reruns tied
  `1010`; changing tabu tenure should be a genuine memory rule, not a hidden
  portfolio or reseeding trick.
- Over-pruning move generation has badly worsened quality.  Tabu memory should
  not block all moves by adding broad global sequences or mutating the tabu
  table outside `tabu.add`.
- Do not describe `sequence_length_biased_tenure` as a stable `997` solution.
  The stable short-contract line observed in repeated runs is still `1002`,
  even though one repeat reached the UB.

## Guardrails

- Only edit `awls_sdst_tabu_memory`.
- Do not call `find_move`, `tabu_search`, `solve_awls`, `solve_awls_single`,
  evaluator code, parser code, `apply_move`, subprocesses, file IO, network, or
  environment variables.
- Do not mutate schedule structure, machine sequences, predecessor/successor
  links, `on_machine`, start/end times, or makespan.
- Do not mutate `tabu.items` directly.  Use exactly one `tabu.add(...)` call.
- Do not change move generation, candidate scoring, parser, evaluator, CLI, or
  benchmark semantics.
- If using setup lookup, include `from harness_agent.standard_fjsp import
  setup_time_between` inside the slot.  The solver does not expose
  `setup_time_between` as a global name here.
- Do not import `operation_key` from `harness_agent.standard_fjsp`; call the
  solver module-level `operation_key(schedule, node)` directly.
- Do not use `move.duration`; `Move` only exposes `method`, `which`, and
  `where`.
- Do not use `schedule.machine_head` or `schedule.jobs`; these fields do not
  exist on `AwlsSchedule`.  Reuse `candidate_tabu_sequence_parts(...)`,
  `candidate_tabu_sequence(...)`, and predecessor/successor lists instead.
- Use `schedule.is_critical_operation(node)`, not `schedule.is_critical(node)`.
