# Industrial JSON Finite-Batch Trial

This is a frozen experimental contract for Huawei-style JSON, not the standard FJSP text format.
Read `io.md` for the exact input, output and validation semantics. All input tasks must have complete legal schedules, including work finishing after the output horizon.

## Objectives

The user confirmed this strict lexicographic order on 2026-09-07:

1. Maximize `completed_weight_within_horizon`: sum of final product weights of valid tasks whose last operation finishes no later than `config.max_output_horizon`.
2. For equal completed weight, minimize `setup_count_positive`: count positive setup transitions between adjacent ordinary operations on each machine, over the entire schedule, including beyond the horizon.

Do not substitute makespan, total setup time, weighted tardiness or a weighted sum. Makespan is not the declared objective. Due dates, task priorities and equipment preferences are input metadata, not additional hard requirements. The original industrial description's third objective (on-time completion rate) is outside this user-confirmed two-objective trial.

## Legality

Select one input route per task and exactly one eligible machine per selected operation. Enforce operation precedence, current time and releases, machine maintenance, directed factory permissions, machine-to-machine transit, both Q-time bounds, ordinary-machine setup and non-overlap, and finite parallel-batch rules.

The fixed validator follows exact batch-family equality and the minimum integer member capacity. This is not the original description's fuzzy compatibility or fractional capacity rule. Ordinary and batch machines must be disjoint in the input; the adapter rejects mixed-use machine instances because the fixed validator does not validate their cross-type overlaps.

Only Core may call the fixed validator. Solver code must be independently generated from these documents and the input. Do not import any old solver, validator, harness internals or saved schedules. Do not remove tasks, change the input, or relax the fixed constraints to manufacture feasibility.

## Trial Protocol

Full and None use identical documents, original JSON, fixed evaluator, model, seed, solver budget, direction rounds and candidate counts. Full alone may receive method knowledge and authorized implementation Skills. A single neutral Agent-generated legal foundation is frozen for both sides when available; if foundation generation fails, report the failure, not a fabricated Full/None comparison.

The folder does not exercise future dynamic arrivals: all sampled releases precede current time. Repeated eligible machines are explicit operation visits, not a separate loop-expansion field. Input feature presence, actual selected-route coverage and algorithm-mechanism activation must be reported separately.
