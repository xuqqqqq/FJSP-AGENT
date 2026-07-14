# Standard FJSP Text Format

## Source

- Format explanation: [Hexaly Flexible Job Shop Problem template](https://www.hexaly.com/templates/flexible-job-shop-problem-fjsp)
- Public instances: [FJSPLib](https://scheduleopt.github.io/benchmarks/fjsplib)

## Format Summary

The common standard FJSP text format starts with:

```text
number_of_jobs number_of_machines average_or_max_number_of_compatible_machines
```

Each job line then contains:

```text
number_of_operations
for each operation:
  number_of_compatible_machines
  repeated machine_id processing_time pairs
```

## Token Cursor Parsing Rule

In Dauzere/DP/BA/BR/HU-style standard FJSP files, one physical job line packs
all operations for that job.  The line starts with `number_of_operations`, and
the remaining tokens on that same line describe every operation in sequence.

Parse with a token cursor, not with one physical input line per operation:

1. Read the header tokens:
   `number_of_jobs number_of_machines average_or_max_number_of_compatible_machines`.
2. For each subsequent non-empty job line, create a token cursor over that job
   line.
3. Consume `operation_count`.
4. Repeat `operation_count` times:
   consume `candidate_count`, then consume exactly `2 * candidate_count`
   tokens as `(machine_id, processing_time)` pairs.
5. Validate that the cursor ends at the job-line boundary, or use one global
   cursor over the entire file and validate that every declared operation and
   candidate pair was consumed.

Do not assume that operation 0, operation 1, and operation 2 appear on separate
physical lines.  A parser that increments a file-line index inside the
operation loop will fail packed job-line instances even if it works on a toy
file.

Machine indices may be 0-based or 1-based depending on the dataset.  The parser
should normalize them internally and validate the range.

## Parser Anti-Patterns

- Bad: after reading a job line's operation count, run
  `for op_id in range(operation_count)` and read `lines[idx]` for each
  operation.
- Bad: derive `operation_count` from the number of remaining physical lines.
- Good: keep `idx` or `pos` as a token cursor inside the current job line and
  consume candidate counts plus machine-duration pairs.
- Good: after parsing, check that the number of generated `(job_id, op_id)`
  records equals the sum of all declared operation counts.

## Impact on FJSP Harness Agent

The standard-FJSP adapter must:

- parse both 0-based and 1-based machine ids;
- check all operations are scheduled exactly once;
- check selected machines are valid candidates;
- check job precedence;
- check machine non-overlap;
- compute makespan.
