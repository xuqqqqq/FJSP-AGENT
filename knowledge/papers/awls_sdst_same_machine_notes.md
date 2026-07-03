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
- A later guarded same-machine slot run again proposed the same pure exact
  trial pattern (`trial.makespan + 0.001 * legacy`) despite being asked for a
  hybrid tail/setup tie-breaker.  Core evaluation again tied `oddla20` at
  `1010` with setup time `1850`.  Treat this exact-trial-only pattern as a
  repeated failed idea class; if exact trial is used again, it needs a
  materially different bounded tie-breaker or gating rule.
- A later exact-trial candidate replaced the legacy tie-breaker with
  `0.001 * total_block_setup`, computing the setup sum inside the moved local
  block after `trial.apply_move(move)`.  It was legal but again tied
  `oddla20` at `1010`; setup time dropped to `1840`, but Core promotion still
  requires makespan improvement.  Do not retry exact trial with only a
  setup-time/block-setup tie-breaker unchanged; a future same-machine
  hypothesis needs a real gating rule, critical-tail pressure, or move-locality
  mechanism.
- A later critical-gated exact-trial/flow-time candidate passed proposal audit
  but failed at runtime before evaluation because it used nonexistent
  `move.node`.  `Move` only has `method`, `which`, and `where`; use
  `move.which` for the moved operation.  The same candidate also used
  `schedule.end_time[schedule.index.end_node]` as makespan in the gate; use
  `schedule.makespan` or `trial.makespan` instead.
- After those API guardrails were added, a guarded retry again proposed
  setup-aware local R/Q propagation without exact trial and was blocked before
  evaluation.  Its semantic repair fallback was only a legacy-ratio gate
  (`legacy <= 1.1 * schedule.makespan`) around the already-failed pure exact
  trial score `trial.makespan + 0.001 * legacy`; treat that as a repeat of the
  pure-exact idea class, not as a materially different same-machine operator.
- A later `exact_trial_criticality_gate` candidate legally ran but tied
  `oddla20` at `1010`.  It used `trial.makespan` as the score and added a
  `0.1 * (trial_makespan - schedule.makespan)` penalty only when `move.which`
  looked non-critical via `end_time + backward_path_length < makespan` and the
  trial worsened makespan.  This light non-critical worsening gate did not
  improve over pure exact trial; do not retry it unchanged as the main same
  machine novelty.
- After `global_sdst_cooldown_boost` was accepted in the weight-update slot, a
  same-machine worker tried `exact_clone_apply_same_machine_scoring` on the
  stable `1010` short line, but it failed before quality evaluation because it
  imported nonexistent `AwlsTrial` from `harness_agent.standard_fjsp`.  The real
  exact-trial API in this solver is `trial = schedule.clone()`,
  `trial.apply_move(move)`, and `trial.makespan`; do not use `AwlsTrial`.
- On the hard HUdata subset `oddla12/oddla14/oddla20`, a legal
  `exact_trial_with_estimation_error_correction` candidate worsened the Core
  objective.  It used the stable same-machine estimate, then returned
  `exact + 0.1 * (exact - stable_value)` after `trial.apply_move(move)`.
  Baseline average makespan was `1208.0` and candidate average makespan was
  `1248.0`; average gap worsened from `9.43%` to `13.24%`.  `oddla20` worsened
  from `1014` to `1027`.  Lower or changed setup totals did not imply makespan
  improvement.  Do not retry exact trial with only estimator-error correction
  such as `exact + k * (exact - stable/legacy)` or `trial_makespan + k *
  estimator_error` unchanged.
- On a representative HUdata probe `oddla02/oddla13/oddla14/oddla16/oddla20`,
  baseline under the current `critical + beta400/gamma40/theta5 + pct75`
  controls averaged `1110.6`.  A worker then proposed
  `same_machine_exact_with_locality_tiebreaker`: for every SDST same-machine
  candidate it cloned the schedule, applied the move, returned
  `trial.makespan`, and used `0.0001 * distance` as an equal-makespan locality
  tie-breaker.  The code was syntactically legal, but the Core candidate
  benchmark exceeded the outer 30-minute shell limit with three 30s solver
  subprocesses still running, showing that unbounded exact clone/apply inside
  every N7 score can burn the evaluation budget.  The platform now treats this
  as `same_machine_unbounded_exact_trial_scoring`.  Future exact-trial use
  must be bounded by a prior stable estimate, critical-tail/locality gate,
  explicit small budget, or move-selection top-k stage before evaluator time is
  spent.
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

This exact snippet is now failure memory, not a recommended next standalone
candidate; reuse only as part of a materially different hybrid rule.

## Guardrails

- Only edit `awls_sdst_same_machine_evaluation`.
- Do not modify parser/evaluator/IO semantics.
- Do not change NK/change-machine scoring or zi policy in this stage.
- Do not retry pure exact trial, setup-only exact tie-breaks, or the light
  non-critical worsening exact gate.  Do not retry exact-trial estimator-error
  correction as the main novelty.  Future same-machine attempts need a
  materially different critical-tail, locality, or acceptance-pressure signal.
