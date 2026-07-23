# Standard FJSP AWLS/HGTSA Method Package

## Purpose

This package is an instance-independent implementation reference for a
standard FJSP solver generated from an active requirement and IO contract. It
is method knowledge, not a pre-solved schedule and not backend orchestration
code.

## Applicability And Retrieval Stage

This package is a second-stage implementation candidate, not a first-stage
default. It should become visible only after the Main Agent has diagnosed a
standard-FJSP direction dominated by coupled assignment/sequence improvement,
critical-path local search, tabu search, or adaptive diversification.

This package is advisory method knowledge, not a prescribed implementation.
The Main Agent may select the complete package when the instance, incumbent,
and budget support a coherent AWLS/HGTSA direction, or use individual assets as
references for a smaller or hybrid direction. A recommendation to stage the
work incrementally is not a prohibition on selecting the complete method.

Prefer this package when a legal incumbent or complete baseline state can be
represented by assignment plus explicit machine sequences, and the available
budget can support repeated decode-and-evaluate moves. Do not select it merely
because the package is the most detailed asset in the knowledge base.

Do not prefer it when the immediate task is only to create a minimal legal
baseline, when the selected direction is a CP-SAT/exact model, or when an
active variant invalidates the standard decoder and move semantics.

The Coding Agent should reason from the package and independently adapt,
combine, simplify, or reimplement its ideas for the active solver CLI, state
representation, instance characteristics, and output schema. The reference
source is optional study material, not the required answer. If the Agent claims
the complete package, the resulting behavior must preserve its coherent
executable search semantics; this fidelity requirement does not require source
copying. It must not copy benchmark-specific scores, schedules, machine orders,
or target values.

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
  may study, adapt, combine, simplify, or replace its structures and functions,
  but must still follow the active IO and evaluator contract. Direct source
  transplantation is neither required nor preferred.
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
