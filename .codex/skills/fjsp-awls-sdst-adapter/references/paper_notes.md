# Paper Notes For AWLS-SDST Agents

Keep this as compact prompt evidence, not as proof of implementation.

## AWLS / HGTSA Family

- AWLS-style FJSP search uses a disjunctive graph schedule, critical path/block neighborhoods, tabu memory, and adaptive operation weights.
- N7/N8 same-machine moves relocate operations around critical blocks.
- NK or RK/LK change-machine insertion narrows target positions using head/tail information instead of scanning every insertion.
- `zi` perturbs approximate move scores using operation weights and cooldowns; it is useful only after the underlying timing model is correct.

## FJSP-SDST Literature Pattern

- Sequence-dependent setup time occupies machine capacity between consecutive operations on the same machine.
- A move that looks good by processing time alone may be bad after setup insertion/removal cost.
- Setup-aware local search should update both head and tail timing with setup arcs.
- For HUdata job-pair setup, setup depends on previous job and current job on a machine; for Fattahi operation-pair setup, it depends on previous operation and current operation.

## NS4S / IJCAI 2025 Context

- NS4S reports FJSP-SDST experiments on SDST-HUdata, 20 instances.
- The paper's experimental setting states a 30 second cutoff for FJSP-SDST.
- Use their UB/BKS table only as evaluation reference; do not modify evaluator semantics to match paper tables.
