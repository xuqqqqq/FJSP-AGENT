# Decoder And Neighborhood Guidance

Use this reference for agent-generated FJSP-SDST solvers and other variants
where sequencing moves must be decoded back into a complete feasible schedule.

## Decoder Responsibilities

A decoder maps a representation into scheduled operations. For SDST, a robust
representation is:

- `assignment[(job_id, op_id)] = machine_id`
- `machine_sequences[machine_id] = [(job_id, op_id), ...]`

The decoder must rebuild start/end times from scratch:

- An operation can start only after its job predecessor ends.
- An operation can start only after the previous operation on the same machine
  ends plus setup time, if setup exists.
- The decoded schedule must contain every operation exactly once.
- A deadlock, cycle, missing operation, duplicate operation, or ineligible
  machine assignment means the candidate is infeasible and must be skipped.

For SDST, setup is a sequencing effect on a machine. Reducing total setup time
is only a tie-breaker or diagnostic; makespan remains the primary objective
unless the task contract says otherwise.

## Local Neighborhood Rules

Use neighborhoods that modify a complete representation and then decode:

- same-machine adjacent swap around critical or high-finish operations;
- bounded same-machine insertion/relocation within a small window;
- alternate-machine reassignment for operations whose current machine is
  bottlenecked, followed by insertion into a bounded target-machine window;
- small destroy-repair that removes a few operations and reinserts them with
  the same decoder.

Avoid broad all-pairs scans in the first version. They often timeout before the
agent learns anything.

## Candidate Move Filter

Before evaluating a move:

- check that moved operations keep eligible machines;
- avoid moving an operation before its job predecessor if the decoder cannot
  prove feasibility;
- prefer operations on the critical path, latest finishing machine, or
  bottleneck machine;
- cap positions or sampled moves per iteration.

After decoding:

- compare decoded makespan against the current best;
- keep the incumbent on no improvement;
- keep a small move memory only if undo loops are observed;
- do not accept a worse schedule unless the method explicitly implements a
  bounded stochastic search and still respects runtime.

## Common Failure Modes

Reject or repair these patterns before evaluator time is spent:

- local search returns a partial schedule and the caller accepts it;
- failed decoder returns `[]`, `None`, or makespan `0`;
- machine sequence stores `(job, op)` pairs but helper code treats entries as
  dictionaries;
- helper code swaps or inserts operations without checking machine eligibility;
- insertion/VNS/SA loops scan all operations and all positions without an
  iteration or time cap;
- a helper file imports `harness_agent` from a standalone solver runtime;
- a top-level helper is inserted inside `main()` or another function;
- a repair replaces a promoted constructive skeleton instead of patching around
  it.

## Self-Check Before Returning A Proposal

The worker should be able to answer yes to each question:

- Does the solver parse the active IO contract and no other assumed format?
- Does every operation appear exactly once in the output?
- Are all machine IDs internal and output IDs consistent with evaluator
  expectations?
- Does every operation use an eligible machine and correct processing time?
- Does every operation respect job precedence?
- Does every machine sequence respect non-overlap and setup time?
- Does any local search keep the incumbent when a neighbor is infeasible?
- Is there a clear iteration/time cap?
- Is the change a single rule/operator mutation around the promoted incumbent?

