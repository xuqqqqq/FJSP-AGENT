# Job-Priority FJSP Semantics And Objectives

## Fixed instance contract

The standard FJSP body is followed by `K` and exactly `K` priority job IDs. The IDs are 0-based, strictly ascending, unique, and in range. The confirmed benchmark contract requires `K = ceil(job_count / 4)`. A parser must consume the whole tail; silently ignoring extra values changes the instance.

Priority is soft. It does not create a due date, release time, precedence arc, or privileged machine access. Therefore the feasible set is exactly the standard FJSP feasible set.

## Objective contract

For job completion `C_j` and priority set `P`, report:

- `makespan = max_j C_j`
- `priority_completion_time = max_{j in P} C_j`

Candidate ranking is strict lexicographic minimization of `(makespan, priority_completion_time)`. An improvement in the second value cannot compensate for any increase in makespan. The fixed evaluator recomputes both values from the complete schedule and rejects a mismatched declared priority metric.

## Implementation invariants

- Store the parsed priority set once and reuse it throughout construction, search, output, and diagnostics.
- Recompute completion from each priority job's final operation after a full legal decode.
- Keep ordinary standard-FJSP legality checks unchanged.
- Activation evidence should show the parsed IDs and both objective values, not only a boolean priority flag.
