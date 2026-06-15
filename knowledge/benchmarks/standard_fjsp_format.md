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

Machine indices may be 0-based or 1-based depending on the dataset.  The parser
should normalize them internally and validate the range.

## Impact on FJSP Harness Agent

The standard-FJSP adapter must:

- parse both 0-based and 1-based machine ids;
- check all operations are scheduled exactly once;
- check selected machines are valid candidates;
- check job precedence;
- check machine non-overlap;
- compute makespan.

