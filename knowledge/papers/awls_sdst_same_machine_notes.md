# AWLS-SDST Same-Machine N7 Notes

## Why This Slot Matters

The default AWLS worker-loop path uses `same_machine_eval=stable`, so
`same_machine_evaluate_stable` is the active same-machine N7 scoring function.
The earlier NK/change-machine slot attempts did not improve `oddla20`; that
suggests the next useful leverage is the same-machine critical-block order,
where setup changes are frequent and directly affect local machine arcs.

## Worker Directions

Use these as hypotheses, not as a manual patch:

- Setup-aware local propagation: when rebuilding the moved local segment, include
  `setup_time_between(prev, current)` in machine-ready and tail-ready terms.
- Exact SDST candidate scoring: compute the legacy score first, then for SDST
  clone the schedule, apply the same-machine move, and use `trial.makespan` plus
  a small tie-breaker.  Catch `ValueError`/`KeyError` locally.
- Hybrid scoring: preserve the current stable evaluator for standard FJSP and
  only add setup-aware terms when `instance.has_sequence_dependent_setup` is
  true.

## Prior Failure Memory

- Do not compare move methods with integers.  Same-machine methods are string
  constants `FRONT` and `BACK`.
- Do not call `setup_time_between` with `current_op=None`.
- Import `setup_time_between` locally inside the slot before using it.
- Use `trial.makespan` after `trial.apply_move(move)` for exact scoring.

## Guardrails

- Only edit `awls_sdst_same_machine_evaluation`.
- Do not modify parser/evaluator/IO semantics.
- Do not change NK/change-machine scoring or zi policy in this stage.
