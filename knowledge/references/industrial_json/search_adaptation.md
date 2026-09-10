---
id: industrial-json-finite-batch-search-adaptation
type: implementation_reference
title: Industrial JSON Feasibility and Search Adaptation
tags: [fjsp, industrial_json, constructive_search, coupled_local_search, exact_hybrid, population_memetic]
status: experimental
---

# Industrial JSON Search Adaptation

This knowledge is for Full only. The user-approved IO and validator semantics override generic method Skills. It supplies reusable reasoning, not solver source or historical answers.

## Foundation and Feasibility

Keep the selected route, machine assignment, ordinary-machine orders and batch groups explicit. Decode all tasks, including after the scoring horizon. The reference baseline must not be copied from an old algorithm. Use tuple operation identity across parsing, route changes, constraints and calendar output.

Q-time upper bounds couple both ends of an arc. Placing an earlier operation greedily and only delaying its successor can destroy feasibility. For fixed routes, assignments, batches and machine order, build lower/upper difference constraints and propagate both directions; reject positive cycles/inconsistent bounds. Calendar conflicts may require shifting a coupled chain or batch rather than just one operation. Alternatives include a correctly reified CP model. Choose your own implementation and keep it within the approved method.

Singleton batches are a safe initial representation, not evidence of an implemented batching optimization. Check every member before merging same-family operations; shared finish changes downstream Q-time as well as capacity and machine occupancy. Preserve the original feasible schedule until the merged candidate is fully validated.

## Method Choices

- Constructive search: compare ready operation/machine/route choices under transport, ordinary setup, calendar and Q-time. Maintain a complete feasible fallback. Use diverse weight-density or horizon-completion priorities; do not let a local priority discard upper bounds.
- Coupled local search: modify a route, machine insertion, ordinary sequence or batch membership in a cloned state; completely re-decode before acceptance. Prioritize jobs near the horizon and costly positive setup arcs. Optimize the objective tuple, not makespan alone.
- Exact hybrid: use optional route/machine intervals, required precedence and Q-time implications, disjoint ordinary machine intervals, explicit calendar exclusions, and real batch modeling or a declared fixed batch partition. Ordinary sequence-dependent setup requires successor-dependent constraints, not a constant setup on every pair. A frozen-sequence or frozen-assignment CP repair is a restricted model; its OPTIMAL status is not global optimality.
- Population/memetic: encode route and assignment/order choices consistently, maintain more than one distinct feasible individual, repair offspring under all industrial constraints, and retain an independent best feasible incumbent. A repeatedly called inherited solver without real population evolution is not population activation.

## Objective and Evidence

Compare (completed_weight, -positive_setup_count) lexicographically. Protect incumbent feasibility and objective. Avoid rounding weights during internal comparisons; Core reports weight to three decimal places. Record actual method calls, iterations, feasible candidates, and accepted changes. CP evidence must include whether it was really called, model size and status. Separate candidate legality, selected-route feature coverage, and method activation.

Do not use the old solver, validator internals, historical schedules, benchmark names or target scores. Generic Skills that describe standard text IO or makespan do not supersede this industrial contract.
