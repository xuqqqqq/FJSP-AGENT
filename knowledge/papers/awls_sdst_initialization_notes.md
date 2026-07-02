# AWLS-SDST Initialization Notes

## Why This Slot Matters

The legal AWLS-SDST baseline on `oddla20` is still far from the expected quality.
N7 and NK scoring slots produced legal candidates but did not improve makespan.
That points to the initial machine assignment and machine sequence construction
as a stronger lever: local search may be starting in a poor basin.

The current `greedy_gt_init` selects by processing-time completion:

```text
completion = max(job_ready[job], machine_ready[machine]) + duration
```

For SDST it should consider:

```text
completion = max(job_ready[job], machine_ready[machine] + setup(last_on_machine, op)) + duration
```

## Worker Directions

Use these as hypotheses, not as a manual patch:

- Setup-aware earliest completion: include setup from the last operation on the
  candidate machine before scoring `(node, machine)`.
- Setup/load balance: break ties by lower setup, lower projected machine load,
  then lower completion.
- Deterministic portfolio behavior: preserve seeded random tie-breaking by using
  `rng.choice` among a bounded near-best filtered set.
- Avoid pure least-setup greediness because it may delay critical jobs.

## Prior Failure Memory

- NK exact scoring with `trial.makespan` was legal but did not beat `oddla20`
  baseline `1202`.
- N7 setup-aware local propagation lowered setup time in a fast run but did not
  lower makespan.
- A setup-aware initialization candidate crashed because it passed AWLS node ids
  directly to `setup_time_between`.  Always convert a node to
  `(index.node_to_job[node], index.node_to_op[node])` first.
- A follow-up candidate converted operation keys but crashed on
  `index.op_index`.  `OperationIndex` itself implements
  `__getitem__((job_id, op_id))`, so pass `index` as the fifth
  `setup_time_between(..., op_index)` argument.
- A later setup-aware append initializer was legal but worsened the fast
  `oddla20` run from `1245` to `1320`.  Do not retry plain
  `machine_ready + setup(last_on_machine, current)` append scoring unless it
  is materially changed, for example by using a portfolio or post-init
  improvement evidence.
- Do not compare move/init mode constants with integers.
- Do not call `setup_time_between` with `current_op=None`.
- Import `setup_time_between` locally inside the slot before using it.

## Guardrails

- Only edit `awls_sdst_initialization`.
- Do not modify parser/evaluator/IO semantics.
- Do not change N7/NK scoring or zi policy in this stage.
