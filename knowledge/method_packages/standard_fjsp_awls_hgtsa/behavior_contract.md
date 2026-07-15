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
- Move application must be transactional. Apply each candidate to a clone or
  snapshot, rebuild links/times, and commit only after decoding succeeds. An
  exception, cycle, partial decode, or timeout must leave `current` and `best`
  unchanged; catching an error after mutating `current` is invalid.
- Any traversal of machine predecessor/successor links must use a visited set
  or an operation-count bound so a damaged candidate cannot grow an unbounded
  path or tabu key.

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

- The generated CLI must accept `--time-limit-sec`; Core passes a value below
  its process timeout so serialization and process exit retain headroom.
- The solver must obey one shared deadline across construction, candidate
  generation, search, validation, serialization, and every portfolio lane.
- Check the deadline inside nested operation/machine/insertion-position loops,
  not only between restarts or outer tabu iterations. A single neighborhood
  scan must be interruptible.
- Bound candidate materialization with explicit shortlists/windows/caps. Do not
  build unbounded all-pairs move lists before checking the deadline.
- Worker-side validation is limited to compilation and one fixed-seed short
  smoke of at most 3 seconds. Do not retry a failed worker smoke or replace it
  with ad hoc inline search loops. Multi-seed and formal benchmark runs belong
  to Core.
