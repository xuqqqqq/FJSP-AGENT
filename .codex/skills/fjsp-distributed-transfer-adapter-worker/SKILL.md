---
name: fjsp-distributed-transfer-adapter-worker
description: 为 DFJSPT 实现工厂/机器联合分配、转移时间、负载与能耗目标。
---

# Distributed FJSP With Transfers Adapter

## Contract

- Parse the five-line DFM header and grouped candidates; input identifiers are 1-based, output identifiers 0-based. Machine IDs remain global across factory groups after conversion and must not be renumbered within each factory.
- Transfer delay is 60 across factories, 30 for same-factory different-machine, and 0 only when both factory and machine are identical. In code, compare factory IDs before machine IDs; equal numeric machine IDs in different factories are different resources and still require the cross-factory delay.
- Evaluate lexicographically: makespan, max factory workload, total energy consumption.
- Output every operation with both `factory_id` and `machine_id`.
- The supplied paper uses Pareto ranking, while this runtime emits one fixed lexicographic winner. A Pareto archive may support internal diversity, but weighted sums or arbitrary archive selection may not replace the Core objective order.

## Implementation

1. Parse candidate groups structurally. For each operation, `candidate_count` is the total across all `F` factories. Enumerate compositions `(g_1,...,g_F)` where every `g_f` lies within the header's per-factory min/max and their sum is `candidate_count`. For factory `f`, require the first option to be `f machine duration energy`, then read exactly `g_f-1` options as `machine duration energy`. Validate the next factory marker and backtrack across remaining operations when more than one composition is possible. Accept a backtracking branch only when it parses every declared operation and consumes the entire job row; reject trailing tokens. Validate every raw machine ID against `1..F*machines_per_factory` before converting it to global 0-based form.
2. Never guess whether a token is a factory marker from its numeric size. Processing times and energies can be small, while machine IDs are globally numbered and can exceed the machines-per-factory value.
3. Represent each assignment as a `(factory,machine)` pair. Treat the explicit candidate-group factory marker as authoritative; machine values alone do not define factory blocks. Never collapse or locally renumber resources from different factories.
4. Preserve duplicate candidate entries. Select and validate an option by `(factory,machine,actual_duration)`, not only `(factory,machine)`; when multiple input tuples have the same three values, use the last matching tuple deterministically for unit energy.
5. Decode job arcs by comparing factory IDs before machine IDs, and resource arcs within each complete factory-machine pair.
6. Track processing energy `duration * unit_energy` and transfer energy `delay * 6` incrementally, then fully recompute accepted candidates from the selected legal option tuples. A nonempty self-check error list is a failed candidate and must never be emitted or retained as a legal incumbent.
7. Search across factory reassignment, machine reassignment, critical order, and balanced-load moves; preserve one independent incumbent.
8. Report per-objective deltas and a transfer legality audit. Do not claim CP-SAT unless the exact model actually ran.

## Method Guidance

- For `population_memetic`, keep coupled OS/FA/MA decisions, use GLR-style global/local/random initialization, precedence-preserving OS variation, option-valid assignment variation, and bounded local search. Treat the paper's 60/30/10 initialization split as a tunable hypothesis, not a hardcoded answer.
- For `coupled_local_search`, implement the mechanisms behind LSO_SP, LSO_MPT, and LSO_RTT: critical-block sequence moves, faster eligible factory-machine replacement, and transfer-reducing replacement. Every move must be fully re-decoded before acceptance.
- For `constructive_search`, use multiple transfer/load-aware starts; do not label a single greedy dispatch as memetic search.
- For `exact_hybrid`, model the active three-objective runtime contract or a clearly bounded repair neighborhood and retain the legal heuristic incumbent on timeout.

Read the distributed semantics/search cards and assigned Method Package before editing.
