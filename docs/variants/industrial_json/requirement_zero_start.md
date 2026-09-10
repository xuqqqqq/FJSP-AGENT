# Industrial JSON Independent Zero-Start Trial

This is a frozen experimental contract for Huawei-style JSON, not the standard
FJSP text format. Read `io.md` for the exact input, output and validation
semantics. All input tasks must have complete legal schedules, including work
finishing after the output horizon.

## Objectives

Use this strict lexicographic order:

1. Maximize `completed_weight_within_horizon`: sum of final product weights of
   valid tasks whose last operation finishes no later than
   `config.max_output_horizon`.
2. For equal completed weight, minimize `setup_count_positive`: positive setup
   transitions between adjacent ordinary operations on each machine over the
   entire schedule, including beyond the horizon.

Do not substitute makespan, total setup time, weighted tardiness or a weighted
sum. Due dates, task priorities and equipment preferences are input metadata,
not additional hard requirements.

## Legality

Select one input route per task and exactly one eligible machine per selected
operation. Enforce precedence, current time and releases, maintenance, directed
factory permissions, machine-to-machine transit, both Q-time bounds, ordinary
setup and non-overlap, and finite parallel-batch rules.

The fixed validator uses exact batch-family equality and minimum integer member
capacity, not fuzzy compatibility or fractional capacity. Ordinary and batch
machines must be disjoint in the input; mixed-use machine inputs are rejected.

Only Core may call the fixed validator. Solver code must be independently
generated from the documents and input. Do not import an old solver, validator,
harness internals or saved schedules. Do not remove tasks, change the input or
relax constraints to manufacture feasibility. Schema-only smoke is not proof of
legality.

## Independent Zero-Start Protocol

Full and None start from separate empty solver workspaces. No supplied or shared
solver foundation is available. Each arm has three total rounds; initial
generation, subsequent feasibility repair, and optimization all consume this
same budget. Failed planning also consumes its round. No additional uncounted
foundation repair or rescue is permitted.

Before a legal accepted solver exists, a round generates or repairs one complete
solver. After acceptance, remaining rounds use up to three Main-selected method
lanes from that arm's own frozen incumbent. There are no additional within-round
repair attempts. One arm's failure does not prevent the other arm from running.

Both arms receive identical documents, original instance, fixed evaluator,
model, seed and solver time budget. Full alone may receive the registered
domain knowledge and authorized implementation Skills. Outer arms run serially;
method lanes within an optimization round may run concurrently. Actual calls,
lane-specific runtime budgets, failures and wall time must be reported.

Compare final feasibility first. Compare objective values only when both final
results are legal. Report best observed legal candidates separately from accepted
returned results. A single instance/seed trial does not establish stable benefit.

All sampled releases precede current time, so this case does not exercise future
dynamic arrivals. Repeated machine visits are explicit operations, not a separate
loop-expansion field. Feature presence, selected-route coverage and implemented
algorithm activation are separate evidence.
