---
id: fjsp-reentrant-search-adaptation
type: reference
title: Re-entrant FJSP Search Adaptation
tags: [fjsp, reentrant_aware_search, bottleneck, critical_path, stnv, memetic, cp_sat]
status: active
---

# Re-entrant FJSP Search Adaptation

Expansion preserves standard FJSP legality but changes search geometry. Repeated loop bodies amplify machine-load concentration, create repeated visits to the same machine groups, and lengthen job chains. Ordinary FJSP search remains valid, but it should see expanded identities and repeated-pass pressure.

## Compatible Mechanisms

- Construction: combine earliest feasible gap and load balance with remaining-work pressure. A bounded STNV-style signal can favor an operation whose job will revisit a heavily loaded machine soon, reducing future bottleneck starvation. Keep this as one portfolio rule, not a universal dispatch law.
- Local search: recompute the expanded disjunctive critical path and target critical machine blocks containing loop-body visits. Couple reassignment to alternative machines with insertion/swap and full re-decode; moving one pass must not silently force the other passes to follow it.
- Population/memetic: encode every expanded operation distinctly, use precedence-preserving order operators, assignment mutation, diversity control, and bounded critical local improvement.
- Exact hybrid: full CP-SAT is reasonable for small/low-flexibility expanded instances. On larger instances, restrict assignment/order changes around critical repeated visits, seed from the incumbent, and preserve a heuristic fallback.

## Research Basis

The supplied papers support a portfolio rather than one mandatory algorithm. Chen et al., *Re-entrant flexible scheduling: Models, algorithms and applications* (2015), surveys exact methods, dispatching rules, constructive and improvement heuristics, and hybrids. Chen et al., *Dynamic state-dependent dispatching for wafer fabrication* (International Journal of Production Research, 2004, DOI 10.1080/00207540410001721736), motivates dynamic bottleneck classification, STNV, and look-ahead starvation avoidance. The supplied CP/MIP and ant-colony papers support exact and population lanes.

Additional 2024-2026 research confirms the same mix: Mlekusch and Hartl combine constraint programming with a hybrid genetic algorithm and critical-path blocks for a dual-resource re-entrant flexible flow shop (DOI 10.1080/00207543.2024.2392198); Yuan et al. study reinforcement-learning-guided metaheuristics for re-entrant flow shops (DOI 10.1049/cim2.70029); Zhang et al. use integrated construction and critical-path search for re-entrant/skippable hybrid flow shops (DOI 10.1080/00207543.2026.2708152). These models are not identical to this benchmark, so only their compatible search mechanisms are transferred.

Avoid importing online-factory WIP rules, batch semantics, skippable operations, dual resources, or stochastic rework into this static single-loop contract.
