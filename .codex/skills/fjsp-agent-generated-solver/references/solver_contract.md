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

The bytes written to `--output` must be the declared JSON object.  Do not
write a bare schedule list such as `json.dump(best_schedule, f)` and then claim
`declared_output_schema`; the standard evaluator expects an object containing a
`schedule` array.

Reading the file is not enough.  A proposal that calls `read_text().split()` or
`json.load(...)` and then hardcodes `op_info = {(0, 0): ...}`, a fixed
`machine_sequences`, or a fixed one-operation schedule has not implemented the
active IO parser.

## Standard FJSP Packed-Line Parser Rule

For standard FJSP text instances such as Dauzere/DP/BA/BR/HU, each physical job
line usually packs all operations for that job.  The coding agent must parse the
job line with a token cursor:

- consume `operation_count` once at the start of the job line;
- for each operation, consume `candidate_count`;
- then consume exactly `2 * candidate_count` tokens as
  `(machine_id, processing_time)` pairs;
- advance the operation cursor within the same job line, not the file-line
  cursor.

Do not implement a parser that reads one new physical line for each operation.
That anti-pattern can compile and pass shallow self-checks while failing
Dauzere/DP-style packed job-line instances.  The `active_io_parser` evidence
should name the cursor variables or loops that consume all packed operation
tokens from the active input.

## Standard FJSP Machine-ID Base Rule

Public standard FJSP benchmark families are not uniform about machine numbering.
Some files use 0-based machine ids and others use 1-based machine ids.  The
coding agent must not subtract 1 while reading each candidate pair by habit.

Use this parser shape:

- collect every raw machine id while reading candidate pairs;
- after parsing all raw ids, set `machine_base = 0` only when
  `min(raw_ids) >= 0` and `max(raw_ids) < machine_count`;
- set `machine_base = 1` only when `min(raw_ids) >= 1` and
  `max(raw_ids) <= machine_count`;
- raise a parser error if neither condition holds;
- normalize exactly once when building the eligible-machine map used by the
  decoder and output writer.

This avoids two common failures: `machine -1 out of range` on 0-based data and
`machine 0 out of range` on 1-based data that was normalized twice.

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

Randomization must happen after scoring the ready-operation/machine candidates.
Choosing one ready operation and then calling `rng.choice(eligible)` over its
machines is not an operation-level ready-list constructor, because it does not
compare the ready operations and eligible machines under job/machine readiness.

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

When decoding `assignment + machine_sequences`, do not replay each machine list
in machine-major order.  Use a progress loop: only schedule the next operation
on a machine when its job predecessor is already scheduled; if no operation can
progress, reject the candidate as infeasible.

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
Do not define evidence-only helpers: parser, decoder, schedule-builder, and
validation/self-check helpers must be called by the runnable solver flow before
the solution is written.

## Runtime Contract

The solver must finish comfortably under the harness timeout:

- Accept `--time-limit-sec` from the evaluator command. Use one absolute
  deadline derived from that argument; never hardcode the Core timeout.
- Core reserves exit headroom, but the solver must also stop candidate
  generation early enough to validate and serialize the incumbent.
- Bound restarts, local-search iterations, candidate windows, and neighborhood
  scans. Check the deadline inside every nested operation, machine, and
  insertion-position loop, not only in the outer search loop.
- Prefer critical/bottleneck subsets before all-pairs scans.
- Apply moves to a clone/snapshot and commit only after complete decode and
  validation. Failed moves must not leave the current state partially mutated.
- Bound predecessor/successor traversals by a visited set or operation count.
- If smoke evaluation times out, treat the method as infeasible even if the
  idea is plausible.

## Evolution Rule

During improvement rounds:

- Preserve the latest promoted parser, representation, constructive skeleton,
  and legality repair unless loop feedback names them as the failure source.
- Change one bounded rule/operator at a time.
- Add helper files only when they reduce patch risk; keep imports standalone.
- Do not replace the whole existing solver after an incumbent exists.
