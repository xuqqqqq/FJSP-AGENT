# AWLS-SDST Literature Direction Notes

## Purpose

This card turns FJSP-SDST literature directions into worker-facing hypotheses
for the current AWLS slot system.  It is not a manual patch plan: solver
changes must still be produced by the worker inside one confirmed slot and
promoted only by Core evaluator makespan improvement.

The current `oddla20` / `la20` incumbent is `makespan=1010` against UB `997`.
Many small setup-time, zi, cooldown, reset, portfolio, and tabu-tenure changes
have tied or worsened.  Future proposals should therefore be materially
different from local setup-time minimization or parameter-only perturbation.

## Literature-Backed Directions For Existing Slots

### Critical-Block Structure

Use AWLS critical blocks as the main search object.  Candidate ideas:

- Move boundary operations across a critical block edge instead of scanning only
  fixed near-critical windows.
- Try one or two alternate-machine insertions for a critical operation whose
  current machine is bottlenecked.
- Use non-improving local filters before expensive exact checks, but keep the
  incumbent broad same-machine/change-machine traversal alive.

Relevant slots: `awls_sdst_neighborhood_selection`,
`awls_sdst_move_selection`, `awls_sdst_same_machine_evaluation`.

Avoid repeating: near-critical 0.99 filters, +/-3 or +/-10 windows, latest-block
top-k pruning, and change-machine generation only when same-machine moves are
empty.

### Regret And Insertion Construction

Construction should account for the second-best alternative, not just the best
append completion.  Candidate ideas:

- For a ready operation, estimate best and second-best machine insertion/append
  costs; prioritize high regret when delaying the best slot is costly.
- Combine completion, remaining job tail, machine load, and setup delta instead
  of pure least-setup.
- If non-append insertion is attempted, use a real topological or
  `AwlsSchedule` feasibility guard before committing.

Relevant slot: `awls_sdst_initialization`.

Avoid repeating: append-only setup-aware earliest completion, low-setup
tie-breaks, fixed small RCL portfolios, static single-bottleneck priority, and
committed non-append insertion without a real acyclic guard.

### Assignment-Then-Sequencing Moves

Several FJSP-SDST algorithms combine machine reassignment for critical
operations with sequencing repair.  Candidate ideas:

- For a critical operation with alternate machines, generate a bounded
  assignment move first, then evaluate insertion positions on the target
  machine with setup-aware timing.
- Keep makespan as the primary score.  Setup-time change can only decide ties or
  prune clearly non-promising moves.
- Reuse existing AWLS clone/apply exact checks instead of rewriting schedule
  feasibility.

Relevant slots: `awls_sdst_neighborhood_selection`,
`awls_sdst_move_evaluation`, `awls_sdst_move_selection`.

Avoid repeating: exact local N7 makespan alone, setup-block tie-breaker alone,
and setup-propagation approximations without exact trial evidence.

### Adaptive Neighborhood Selection

NS4S-style ideas are useful only if they choose among real neighborhoods, not if
they become another zi multiplier.  Candidate ideas:

- Maintain bounded preference among existing move families: same-machine block
  boundary moves, alternate-machine critical moves, and exact top-k candidates.
- Update preference only from Core-like makespan progress signals available in
  scope, not from LB/UB or evaluator files.
- If a slot cannot keep enough candidates alive, prefer no change over
  over-pruning.

Relevant slots: `awls_sdst_move_selection`, `awls_sdst_weight_update`,
`awls_sdst_search_transition`.

Avoid repeating: cooldown-only SDST flags, fixed best resets, criticality-only
tenure splitting, and formulas that only multiply by `is_critical`.

## Prompt Cautions

- HUdata setup is sequence-dependent by machine and job pair; use
  `setup_time_between(...)` and module-level `operation_key(schedule, node)`.
- `has_sequence_dependent_setup` is a property, not a function.
- `OperationIndex` has `duration(node, machine_id)` and no `durations` field.
- Score remains `-makespan`; lower setup time alone has repeatedly failed to
  predict promotion.
- A proposal that changes only seeds, lane order, cooldown rate, critical
  multiplier, or tenure split needs especially strong novelty evidence.
