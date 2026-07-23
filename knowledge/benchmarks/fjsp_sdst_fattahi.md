# FJSP-SDST Fattahi Instances

## Local Dataset

- Local path: `C:\Users\ASUS\Downloads\FJSP_SDST_Fattahi_instances\FJSP_SDST_Fattahi_instances`
- Files: `Fattahi_setup_01.fjs` through `Fattahi_setup_20.fjs`
- Example inspected: `Fattahi_setup_01.fjs`

## Source Note

The bundled `README.txt` says these are public FJSP instances with
sequence-dependent setup times from:

- `ai-for-decision-making-tue/Job_Shop_Scheduling_Benchmark_Environments_and_Instances`
- Dataset path: `data/fjsp_sdst/fattahi`
- Recommended citation: Reijnen, van Straaten, Bukhsh, and Zhang (2023),
  *Job Shop Scheduling Benchmark: Environments and Instances for Learning and
  Non-learning Methods*, arXiv:2308.12794.

## Format Observation

`Fattahi_setup_01.fjs` starts with the standard FJSP-style header and job
operation alternatives:

```text
2 2 2
2 2 1 25 2 37 2 1 32 2 24
2 2 1 45 2 65 2 1 21 2 65
```

It then includes a separate setup-time block. For this tiny case the inspected
block has eight rows:

```text
6 3 4 4
3 6 4 4
3 3 7 4
4 4 4 8
6 3 4 3
3 6 3 3
3 3 6 4
3 4 3 6
```

## Harness Implication

These files must be treated as a distinct `fjsp_sdst` / `planned_fjsp_sdst`
variant, not as current `standard_fjsp`. Sequence-dependent setup times alter
machine non-overlap and schedule-cost semantics, so they require:

- a parser that reads the additional setup-time matrix;
- an evaluator that inserts setup time based on the previous operation on each
  machine;
- a solution contract that states whether setup intervals are explicit in output
  or recomputed by the evaluator;
- separate Method Package components for setup-aware neighborhood moves and insertion scoring.

Until that adapter exists, use this dataset as a parser/evaluator design target
and smoke-test corpus only.
