# AWLS-SDST Adaptation Notes

## Current Code Facts

- AWLS entrypoint: `examples/standard_fjsp_awls_solver.py`.
- Existing SDST parser/evaluator: `harness_agent.standard_fjsp`.
- `parse_standard_fjsp` already detects Fattahi operation-pair and HUdata job-pair setup tails.
- `setup_time_between(instance, machine_id, previous_op, current_op, op_index)` is the canonical setup query.
- `validate_standard_schedule` is the fixed legality oracle.

## Known Failure

Direct AWLS on HUdata SDST can produce invalid schedules because AWLS currently propagates machine arcs as:

```text
start(current) >= end(previous_on_machine)
```

For SDST this must be:

```text
start(current) >= end(previous_on_machine) + setup_time(machine, previous, current)
```

Therefore the first valid AWLS-SDST adaptation must update AWLS internal time propagation before tuning N7/NK or zi.

## AWLS Mechanisms Worth Preserving

- Critical-path and critical-block selection.
- N7/N8-style same-machine critical-block moves.
- NK/RK/LK change-machine insertion windows.
- Sequence tabu and aspiration.
- Adaptive operation weights and `zi` perturbation.

## Slot Design Guidance

Prefer narrow slots with explicit inputs/outputs:

- `awls_sdst_time_propagation`: helper logic used by `AwlsSchedule.update_time`.
- `awls_sdst_move_evaluation`: setup-aware additions to same-machine and change-machine approximate scores.
- `awls_sdst_zi_policy`: formula or function using setup-aware features after legality is established.

Do not let a worker:

- create a new SDST parser;
- alter `standard_fjsp_evaluator.py`;
- alter solution JSON schema;
- hide setup intervals in output records;
- bypass Core evaluator errors.

## Benchmark Ladder

1. Compile: `python -m compileall examples/standard_fjsp_awls_solver.py harness_agent/standard_fjsp.py`.
2. Standard FJSP smoke: Brandimarte Mk01 with AWLS.
3. SDST legality smoke: HUdata `oddla20.txt` seed 0, short time limit.
4. SDST quality probe: `oddla18.txt, oddla20.txt` with best-known CSV.
5. Full HUdata only after smoke legality is stable.
