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
- Correct setup lookup shape:

```python
from harness_agent.standard_fjsp import setup_time_between

prev_op = (schedule.index.node_to_job[prev_node], schedule.index.node_to_op[prev_node])
cur_op = (schedule.index.node_to_job[cur_node], schedule.index.node_to_op[cur_node])
setup = setup_time_between(schedule.index.instance, machine_id, prev_op, cur_op, schedule.index)
```

There is no `schedule.setup_time(...)` helper and no
`schedule.index.setup_time(...)` helper.

## Prior Failure Memory

- Do not compare move methods with integers.  Same-machine methods are string
  constants `FRONT` and `BACK`.
- Do not call `setup_time_between` with `current_op=None`.
- Import `setup_time_between` locally inside the slot before using it.
- Use `trial.makespan` after `trial.apply_move(move)` for exact scoring.
- Two same-machine slot attempts failed at runtime because they called
  nonexistent APIs: `schedule.setup_time(...)` and
  `schedule.index.setup_time(...)`.  Do not retry those forms.
- After the setup API contract was hardened, a legal setup-aware local
  propagation candidate reached `oddla20` makespan `1014` under the incumbent
  `critical + beta400/gamma40/theta5 + pct75` baseline.  It reduced setup time
  from `1900` to `1660`, but still worsened makespan from `1010` to `1014`.
  Do not optimize same-machine scoring for setup-time reduction alone; the
  evaluator objective is still makespan.
- A later worker-loop round repeated the same setup-aware forward/backward
  local propagation idea instead of the requested exact clone/apply scoring.
  It reproduced the legal `1014` result with setup time `1660`.  Treat
  same-machine setup propagation without `schedule.clone()`,
  `trial.apply_move(move)`, and `trial.makespan` as a known failed idea class;
  the platform should semantically repair that proposal before evaluation.
- After the platform made that warning blocking, semantic repair produced a
  true exact same-machine trial candidate using `schedule.clone()`,
  `trial.apply_move(move)`, and `trial.makespan + 0.001 * legacy`.  Core
  evaluation tied `oddla20` at `1010` with setup time `1850`, so exact local
  N7 makespan scoring alone is not enough to beat the incumbent under the
  current short-budget controls.
- If manually adding setup-aware forward/backward local propagation is too
  fragile, prefer exact local scoring:

```python
legacy = ...  # current stable score
if not schedule.index.instance.has_sequence_dependent_setup:
    return legacy
try:
    trial = schedule.clone()
    trial.apply_move(move)
    return float(trial.makespan) + 0.001 * legacy
except (ValueError, KeyError, IndexError):
    return legacy
```

## Guardrails

- Only edit `awls_sdst_same_machine_evaluation`.
- Do not modify parser/evaluator/IO semantics.
- Do not change NK/change-machine scoring or zi policy in this stage.
