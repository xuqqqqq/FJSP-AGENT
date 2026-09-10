# Industrial JSON IO and Fixed Validator Semantics

## Input

The root has objects `time`, `config`, `eqp`, `task`, `transition`, `setup`.

- `time.current_time` is an absolute minute coordinate; `time.current_date_time` is its calendar timestamp. Convert a minute coordinate t to `current_date_time + timedelta(minutes=t-current_time)`.
- `config.max_output_horizon` is an absolute minute cutoff, not a duration to add to current time. `cal_threads` is an input preference, not permission to exceed the experiment's runtime resources.
- `eqp[M].factory_info` names the factory. `eqp_down_interval` contains closed minute windows [a,b]. The fixed validator internally uses [a,b+1) and has a one-minute end-edge tolerance: starting at b is permitted if processing continues to at least b+1. Avoiding the whole [a,b+1) interval is always safe.
- `task[J].earliest_ava_time` is the release; every operation starts at or after `max(current_time,release)`. `final_product_weight` is counted once when the entire task finishes by the cutoff. `task_delivery_time` and `task_priority` do not add hard constraints here.
- `task[J].process_path[R]` is one alternative full route. Choose exactly one R per J, without splicing routes. Its `process_list` keys are string operation sequences ordered numerically. Repeated machine visits remain different operations, identified by (J,R,SEQ).
- Each process has `is_batch_type`, `diff_factory_info`, and `eqp_list`. The process-level batch flag is authoritative. `eqp_list[M].process_time` is its processing duration on M; M must exist in root eqp. Smaller `priority` is a preference, not a mandatory machine choice.
- Between consecutive operations, permit a factory change only if the PREVIOUS operation's `diff_factory_info` includes the directed [from_factory,to_factory] pair. Same-factory assignments need no permission. Additionally impose start_next >= finish_previous + `transition[previous_machine][next_machine]`; a missing entry is zero, including same-factory transfers.
- For every `qtime_info` row, locate `start_process_seq` and `end_process_seq` in the selected route. Anchor events are independently selected by `start_process_type` and `end_process_type` (`start` or `end`). The difference end_event - start_event must satisfy each non-null `min_process_interval` / `max_process_interval`. Zero is a real bound; null means absent. Do not discard nonadjacent edges or upper bounds.
- Sparse `setup` keys encode Python tuple strings `(task_id,path_id,process_seq)`. Parse tuple keys safely, for example with `ast.literal_eval`, never eval. An absent arc has value zero. Between adjacent ORDINARY operations on one machine enforce start_next >= finish_previous + setup[from][to]. The first operation has no setup. Never conflate setup duration with positive setup count.

## Finite Parallel Batches

A process with `is_batch_type=true` has `batch_family`. The fixed validator compares family lists as exact tuples of strings, including literal '*' characters, not wildcard matching.

On a candidate machine M, member capacity is `max(1,int(float(eqp_list[M].curr_batch_size)))`. A batch is the group sharing identical machine, start and finish. All members must have equal family; group size must not exceed the minimum member capacity. Its duration is the maximum member processing duration. A singleton batch is legal. Different batches on one machine cannot overlap. The fixed validator does not apply ordinary-operation setup arcs between batches.

Every member still obeys its own release, route precedence, transport, Q-time and maintenance conditions using the common batch start and finish. A complete route must include every selected operation; cannot skip a batch operation or replace it with unlimited parallel capacity.

## Output

Standalone CLI: `--input PATH --output PATH --seed INT --time-limit-sec FLOAT`. Write UTF-8 JSON:

```json
{
  "task": {
    "INPUT_TASK_ID": {
      "process_path": {
        "INPUT_PROCESS_SEQ": {
          "temp_machine_id": "INPUT_MACHINE_ID",
          "path_id": "INPUT_ROUTE_ID",
          "process_start_time": "2025/04/28 08:00:00",
          "process_finish_time": "2025/04/28 09:00:00"
        }
      }
    }
  },
  "diagnostics": {}
}
```

IDs must come from the input. Dates accept slash or dash separators; use whole minutes. The example is only structural, not an instance answer. No standard `schedule` array or declared makespan is required. Optional top-level diagnostics can record actual searches, CP model/status/calls and accepted improvements; never invent execution counters.

## Evaluation

Core calls the frozen industrial finite-batch validator in an isolated cache directory. A result is valid only when the validator exits successfully, reports zero errors, and all input tasks have complete valid output. Metrics from invalid output cannot be promoted. Worker smoke checks the JSON schema only; it does not establish feasibility.
