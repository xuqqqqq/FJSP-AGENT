---
id: standard-fjsp-algorithm-semantic-review-contract
type: operator-contract
title: Standard FJSP Algorithm Semantic Review Contract
tags: [fjsp, semantic-review, tabu-search, critical-path, critical-block, n8, k-insertion]
status: curated
---

# Standard FJSP Algorithm Semantic Review Contract

This contract reviews whether generated code implements the claimed search
method, not merely whether matching function names exist. It is instance-
agnostic and must not contain benchmark-specific target values or schedules.

## Current State And Global Best

A metaheuristic that accepts non-improving moves must keep two different
states:

```python
current_assignment, current_sequences, current_schedule = initial_state
best_assignment, best_sequences, best_schedule = clone_state(initial_state)
best_value = makespan(best_schedule)

current_assignment, current_sequences, current_schedule = accepted_trial
if trial_value < best_value:
    best_assignment, best_sequences = clone_state(current_assignment, current_sequences)
    best_schedule = list(current_schedule)
    best_value = trial_value

return best_assignment, best_sequences, best_schedule
```

The search must return the global best state, not the last visited current
state. Overwriting variables named `best_*` with equal or worse candidates
violates incumbent preservation even if an outer evaluator later rolls back the
whole solver candidate.

## Reverse-Move Tabu Attribute

After accepting a move, tabu memory must record the attribute that would undo
that move. Recording only the forward destination usually does not prevent an
immediate reversal.

Examples:

```text
change machine A -> B: store reverse attribute (operation, A)
swap ... a,b ... -> ... b,a ...: store reverse attribute (machine, b, a)
insert old_pos -> new_pos: store the position or local arc attribute that restores old_pos
```

The next iteration must test candidate moves against the same attribute
representation that was stored. Add a behavioral test that accepts one move,
generates its inverse, and proves the inverse remains tabu until tenure expiry.

## Aspiration

A tabu candidate may be admitted only when the fully decoded candidate strictly
improves the global best objective. Aspiration compares against `best_value`,
not merely the current state. A claimed tabu loop that accepts only strict
improvements is still hill climbing and cannot use tabu memory to cross a local
optimum.

## Exact Critical Path

Build the active disjunctive DAG from job-precedence arcs and adjacent
machine-sequence arcs. Compute earliest starts in topological order and compute
tail lengths or latest starts in full reverse topological order. A fixed small
number of relaxation passes is not an exact critical-path algorithm for an
arbitrary operation count.

An operation is critical only when its total slack is zero. A machine critical
block is a maximal sequence of critical operations connected by tight machine
arcs; adjacent operations with idle time between them must not be merged into
the same critical block.

Required tests:

1. A synthetic DAG with a critical chain longer than two alternating job and
   machine arcs.
2. A schedule containing non-critical operations adjacent to critical ones.
3. A machine sequence containing two zero-slack operations separated by idle
   time; they must not form one tight critical block.

## N7, N8, And K-Insertion Fidelity

Do not treat arbitrary swaps or unrestricted same-machine insertion as proof of
a named N7/N8 implementation. The implementation must state which critical
block endpoints or feasibility bounds define the neighborhood.

For alternate-machine insertion, derive a bounded feasible target interval from
job predecessor/successor timing and target-machine sequence structure before
full decode. Full decode remains authoritative, but semantic review should flag
an implementation that claims a structured neighborhood while only sampling
unrelated random positions.

## Runtime And Stage Contribution

Candidate application must be transactional. Search may compute an approximate
move score on `current`, but it must apply the selected move to a clone or a
restorable snapshot, rebuild all links/times, and commit only after complete
decode succeeds. Catching a cycle/decode exception after mutating `current`
without rollback is a blocking semantic error because later traversal and tabu
state no longer describe a legal schedule.

The solver must accept the evaluator-provided time limit and use one absolute
deadline. Deadline evidence must appear inside nested candidate loops
(operation, eligible machine, target position), not only around the outer tabu
iteration. Machine-link traversals require a visited or operation-count bound,
and materialized candidate lists require an explicit shortlist/window/cap.

Every search stage should expose enough counters or timing to report:

```text
input makespan
evaluated move count
feasible move count
accepted move count
best makespan after stage
elapsed time
```

Use these facts to detect dead stages that are present in source but receive no
runtime budget. Full re-decoding is a safe baseline, but large neighborhoods
should add candidate bounds, cheap filters, or incremental evaluation before
claiming scalability.

## Multi-Seed Robustness

A single promoted seed proves one evaluator-backed improvement, not method
stability. Reusable experience should describe the method and its verified
invariants. Promotion under stochastic search should use repeated or multi-seed
evidence before the method is promoted from candidate lesson to validated
knowledge.

## Semantic Review Decision

Use `repair_required` only when a finding cites both concrete candidate source
lines and an exact knowledge-contract statement. If source or contract evidence
is missing, emit a warning instead of blocking promotion.

## Reviewed Reusable Failure Patterns

The following source patterns require explicit behavioral verification:

1. A tabu loop tests a forward move signature and stores that same signature
   after acceptance. This does not prove that the inverse move is forbidden.
2. A non-improving search updates one state tuple and returns it without a
   separate cloned global-best tuple.
3. Criticality is propagated with a fixed small number of relaxation passes
   independent of graph size.
4. A latest-finishing or near-makespan window is named an exact critical block
   without zero-slack and tight-arc evidence.
5. A move mutates machine sequences, catches a failed update/decode, and
   continues without restoring the previous state.
6. A solver checks time only between outer iterations while one neighborhood
   enumeration can run past the entire budget.

These are method-level failure patterns only. Reusable memory must retain the
invariant and required test, never an instance score, operation order, or solved
schedule.
