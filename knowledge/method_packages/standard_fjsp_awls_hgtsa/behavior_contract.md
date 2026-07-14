# AWLS/HGTSA Behavioral Contract

The Coding Agent and semantic reviewer should verify behavior, not names.

## Decoder

- A machine sequence containing an operation whose job predecessor is not yet
  complete must wait for progress; machine-major replay is invalid.
- If a full pass schedules no operation while unscheduled operations remain,
  decoding must fail without replacing the incumbent.
- Every accepted decoded state must contain each required operation exactly
  once on an eligible machine with the selected processing duration.

## Critical Graph And Blocks

- Criticality must be derived from forward and backward timing propagation on
  the currently decoded disjunctive graph.
- A critical machine block contains consecutive operations connected by tight
  machine arcs. Merely choosing operations on the busiest machine is not a
  critical-block neighborhood.

## Neighborhoods

- Same-machine moves must modify explicit machine order and be decoded before
  comparison.
- Alternative-machine moves must remove the operation from its old sequence,
  insert it into an eligible target sequence, and decode the complete state.
- K-insertion must evaluate more than an adjacent swap and retain only complete
  legal candidates.

## Tabu And Aspiration

- The tabu attribute stored after an accepted move must describe the inverse
  move needed to return to the previous state.
- For machine reassignment, the inverse attribute uses the old machine and old
  insertion context, not the new machine sequence.
- A tabu candidate may be accepted only through an explicit aspiration rule
  against the global best objective.
- Current state and global-best state must be separate objects or immutable
  snapshots.

## Adaptive Search And Diversification

- Weight or `zi` updates must influence reachable move scoring or selection.
- Stagnation must trigger a bounded diversification mechanism such as weighted
  perturbation, restart, or controlled randomization.
- Diversification must preserve the global best and seeded reproducibility.

## Runtime

- The solver must obey one shared deadline across construction, search, and
  validation.
- Worker-side validation is limited to compilation and one fixed-seed short
  smoke. Multi-seed and formal benchmark runs belong to Core.
