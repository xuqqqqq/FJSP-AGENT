# Standalone FJSP Solver Contract

This reference is for a coding agent writing solver code from requirement and
IO documents. It is not backend code and must not be copied as a fixed platform
algorithm.

## Required Solver Shape

Build a standalone entrypoint that:

- parses only the active input format described by the IO document;
- emits only the declared solution schema;
- uses no evaluator internals and no `harness_agent` imports;
- accepts the configured `--input`, `--output`, and `--seed` interface unless
  the contract says otherwise;
- returns exactly one scheduled record for every operation.

Do not submit only an internal helper function.  A generated solver proposal
must include the runnable script surface that Core will execute:

- an active parser such as `parse_instance(...)` or equivalent that reads the
  instance file and derives every job, operation, candidate machine, processing
  time, and active variant datum from that file;
- a `main()` or equivalent CLI path that reads `--input`, writes `--output`,
  and respects the configured seed;
- JSON output with the declared format, schedule array, and the required
  `job_id`, `op_id`, `machine_id`, `start`, and `end` fields.

Reading the file is not enough.  A proposal that calls `read_text().split()` or
`json.load(...)` and then hardcodes `op_info = {(0, 0): ...}`, a fixed
`machine_sequences`, or a fixed one-operation schedule has not implemented the
active IO parser.

## Representation Rule

Choose one operation identity and keep it end to end:

- Prefer `(job_id, op_id)` for agent-generated solvers.
- Store assigned machine separately from operation identity.
- Store machine sequences as lists of operation identities, not schedule dicts
  mixed with ids.
- Build `op_info[(job_id, op_id)]` or equivalent once and use the same key type
  everywhere.

Do not mix global operation ids, `(job_id, op_id)` pairs, schedule dictionaries,
and raw parser offsets inside one decoder path.

## Constructive Baseline Standard

For standard FJSP and FJSP-SDST, a weak job-by-job greedy is not enough. The
first legal baseline should usually be an operation-level list scheduler:

- Maintain one ready next operation per unfinished job.
- For each ready operation, evaluate every eligible machine.
- Use job ready time, machine ready time, processing time, and any active
  variant timing effect.
- For SDST, include setup from the previous scheduled job/operation on the
  candidate machine.
- Use seeded tie-breaking, randomized assignment, RCL, or multi-start to explore
  different interleavings.
- Keep the best complete valid schedule found by decoded makespan.

This is a method shape, not a fixed formula. Adapt scoring to the active
variant and objective.

For solver self-checks, cite this constructor as
`operation_level_ready_list_constructor`.  Evidence should name the data
structure that stores ready operations, the loop over eligible machines, and
the seeded tie-break, RCL, restart, or multi-start rule.  Do not claim this
capability for a fixed job-by-job sweep.

## Candidate Acceptance

A generated solver may use internal self-checks, but Core evaluator output is
the only promotion authority. Internally:

- Reject any partial schedule.
- Reject duplicate or missing operations.
- Reject machine assignments not listed as eligible in the parsed instance.
- Reject or repair any output interval where `end - start` differs from the
  processing time of the selected eligible machine.
- Reject candidates that violate precedence or machine non-overlap.
- Never score an empty failed decode as makespan `0`.
- Keep incumbent schedule when a trial move cannot be decoded.

## Structured Self-Check Evidence

When returning a structured `solver_contract_self_check`, every implemented
capability must cite function, variable, or guard symbols that appear in the
submitted code.  The narrative fields must do the same:

- `representation` should name the operation-key and assignment/sequence data
  structures used by the code;
- `decoder` should name the function that rebuilds and rejects complete
  candidates;
- `variant_handling` should name the active timing, capacity, calendar, or
  objective guard for each active variant;
- `runtime_bounds` should name the iteration, restart, window, or deadline
  controls;
- `incumbent_preservation` should name the best/current schedule variables or
  candidate-failure branch that keeps the incumbent.

Do not use these fields for strategy prose that has no matching source anchor.

## Runtime Contract

The solver must finish comfortably under the harness timeout:

- Bound restarts, local-search iterations, candidate windows, and neighborhood
  scans.
- Prefer critical/bottleneck subsets before all-pairs scans.
- Add an internal time guard when adding local search.
- If smoke evaluation times out, treat the method as infeasible even if the
  idea is plausible.

## Evolution Rule

During improvement rounds:

- Preserve the latest promoted parser, representation, constructive skeleton,
  and legality repair unless loop feedback names them as the failure source.
- Change one bounded rule/operator at a time.
- Add helper files only when they reduce patch risk; keep imports standalone.
- Do not replace the whole existing solver after an incumbent exists.
