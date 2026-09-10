# Industrial JSON Full-Only Implementation Trial

Use the exact input, output and validation semantics in `io.md`. Generate an
independent solver from an empty workspace, the requirements, input facts and
authorized Full domain knowledge and Skills. No prior solver, baseline code,
saved schedule, validator code or harness internals may be imported.

The strict lexicographic objective is first to maximize
`completed_weight_within_horizon`, then to minimize `setup_count_positive` over
the entire schedule. Do not replace this order with a weighted sum, makespan,
total setup duration or tardiness. All tasks must be scheduled completely,
including tasks finishing beyond the output horizon.

Select one input route per task and one eligible machine per selected operation.
Enforce precedence, current time and releases, machine calendars, directed
factory permissions, machine-to-machine transit, lower and upper Q-time bounds,
ordinary setup and non-overlap, and finite parallel-batch rules. Batch families
must match exactly and capacity uses the minimum integer member capacity.
Ordinary and batch machine sets must remain disjoint. Input data and constraints
are frozen. Due dates, task priorities and equipment preferences are metadata,
not additional hard requirements.

Only Core calls the fixed industrial validator. Worker may compile repeatedly
within its model/runtime budget and run up to three bounded solver smoke checks,
each with a 60-second solver limit. Industrial smoke validates the output schema;
it is not proof of scheduling legality. Use those runs for independent checks
implemented in the generated solver, error diagnosis and repairs. Report the
checks actually performed and their results honestly.

This experiment runs Full only. Each Worker call receives up to 16 requested
edit steps, 64 total model steps and 1800 seconds of implementation time. Core
evaluation keeps the fixed 60-second solver budget and seed 0. Each generation,
feasibility-repair or optimization round consumes one of at most 20 total rounds;
failed planning and failed implementation also count. There are no additional
uncounted within-round repairs or rescue attempts.

Before acceptance, each round generates or repairs one complete solver using
only this trial's own previous failed candidate. After the first accepted legal
solver, attempt two optimization rounds, subject to the same total 20-round cap,
then stop. An optimization round may run up to three Main-selected method lanes
concurrently from the accepted incumbent. No other outer experiment runs in
parallel. Preserve the incumbent if an attempted improvement fails.

Record code delivery, actual model steps, checks, failures, formal feasibility,
objective values, calls and time. This is a single-arm implementation experiment
with expanded budgets. Old None results used smaller budgets and must not be
presented as a fair paired ablation or evidence of a stable Full quality gain.
