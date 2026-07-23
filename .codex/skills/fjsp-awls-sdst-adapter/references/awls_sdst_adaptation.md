# AWLS-SDST Adaptation Notes

## Execution Modes

- AWLS method reference: `knowledge/method_packages/standard_fjsp_awls_hgtsa/reference_solver.py`.
- Platform reference validation may use `harness_agent.domains.io`; generic orchestration must never import the method reference.
- A standalone agent-generated solver must implement the active IO-derived parser and setup query inside the generated artifact. It must not import `harness_agent` or evaluator internals.
- In both modes, the frozen evaluator is the legality oracle and setup intervals must follow the active IO contract.

## Known Failure

An AWLS implementation becomes invalid for SDST when it propagates machine arcs as:

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

## Component Design Guidance

Keep the complete method coupled, but give each component explicit inputs and outputs:

- setup-aware graph time propagation and schedule output;
- setup-aware same-machine and change-machine move evaluation;
- adaptive scoring features and update policy after legality is established.

Do not let a worker:

- create a new SDST parser;
- alter `standard_fjsp_evaluator.py`;
- alter solution JSON schema;
- hide setup intervals in output records;
- bypass Core evaluator errors.

## Benchmark Ladder

1. Compile: `python -m compileall knowledge/method_packages/standard_fjsp_awls_hgtsa/reference_solver.py harness_agent/domains/io.py`.
2. Standard FJSP smoke: Brandimarte Mk01 with AWLS.
3. SDST legality smoke: one small active-task instance, fixed seed, short time limit.
4. SDST quality probe: a bounded structurally representative subset with external LB/UB only for reporting.
5. Broader benchmark evaluation only after smoke legality is stable.
