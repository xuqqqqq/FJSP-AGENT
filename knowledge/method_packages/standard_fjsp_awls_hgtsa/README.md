# Standard FJSP AWLS/HGTSA Method Package

## Purpose

This package is an instance-independent implementation reference for a
standard FJSP solver generated from an active requirement and IO contract. It
is method knowledge, not a pre-solved schedule and not backend orchestration
code.

The Coding Agent should adapt the package to the active solver CLI and output
schema while preserving the package's executable search structure. It must not
copy benchmark-specific scores, schedules, machine orders, or target values.

## Required Structure

1. Parse every job, operation, eligible machine, and processing duration from
   the active input.
2. Build a legal constructive state with operation assignment and explicit
   machine sequences.
3. Decode a state with job-precedence and machine-precedence propagation. A
   partial or deadlocked state is infeasible and cannot replace the incumbent.
4. Extract exact critical paths and tight critical machine blocks from the
   decoded schedule.
5. Generate bounded same-machine critical-block moves and alternative-machine
   reassignment/insertion moves.
6. Apply reverse-move tabu attributes, aspiration against the global best, and
   separate current/global-best states.
7. Update operation weights or equivalent search pressure only after accepted
   moves, and retain explicit diversification when the search stalls.
8. Decode and validate every candidate before objective comparison.
9. Preserve the best complete legal schedule on timeout or failed decoding.

## Assets

- `reference_solver.py`: complete Python method reference. The generated solver
  may adapt its structures and functions, but must still follow the active IO
  and evaluator contract.
- `behavior_contract.md`: behavioral checks that distinguish a real method
  implementation from function names or comments.
- `standard_fjsp_algorithm_semantic_review_contract.md`: promotion-time method
  semantics used by the independent reviewer.

## Adaptation Boundary

The reference implementation may use repository parser/solution helpers for
its own legacy CLI. A standalone agent-generated solver must replace those
imports with code allowed by the active solver contract. Algorithm structures
belong to this package; parser/evaluator authority remains with the active IO
document and Core evaluator.
