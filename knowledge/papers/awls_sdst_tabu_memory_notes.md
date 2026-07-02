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
- Fixed best-reset transition rules worsened `oddla20` to `1033` and `1023`;
  do not emulate restart/backtrack behavior inside tabu memory.
- Portfolio seed remapping, multi-scramble restarts, and best-lane reruns tied
  `1010`; changing tabu tenure should be a genuine memory rule, not a hidden
  portfolio or reseeding trick.
- Over-pruning move generation has badly worsened quality.  Tabu memory should
  not block all moves by adding broad global sequences or mutating the tabu
  table outside `tabu.add`.

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
