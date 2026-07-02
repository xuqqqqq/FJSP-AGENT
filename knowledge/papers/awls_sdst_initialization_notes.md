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

Correct setup lookup shape inside this slot:

```python
from harness_agent.standard_fjsp import setup_time_between

prev_op = (index.node_to_job[last_node], index.node_to_op[last_node])
cur_op = (index.node_to_job[node], index.node_to_op[node])
setup = setup_time_between(index.instance, machine_id, prev_op, cur_op, index)
```

This slot is the body of `greedy_gt_init`, so replacement code must remain
indented inside the function.

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
- Two later setup-aware initialization candidates failed before evaluation:
  one imported `setup_time_between` from `examples.standard_fjsp_awls_solver`
  and called it with separate job/op integers, and another emitted unindented
  code at the start of the function-body slot.  Use
  `harness_agent.standard_fjsp.setup_time_between`, operation-key tuples, and
  preserve function-body indentation.
- After the setup API contract and indentation normalization were hardened, a
  legal setup-aware greedy dispatch initializer reached `oddla20` makespan
  `1046` under the current `critical + beta400/gamma40/theta5 + pct75`
  incumbent controls.  It reduced setup time to `1680` but worsened makespan
  from `1010`, so do not retry plain setup-aware greedy dispatch unless the
  hypothesis materially changes, for example by using a deterministic
  portfolio, regret/RCL selection, or a different post-init local-search
  interaction.
- A later failure-memory-guided candidate changed only the setup-aware greedy
  tie-breaker: it filtered near-best completion candidates, then chose the
  lowest setup and lowest machine-ready time.  It was legal but worsened
  `oddla20` from `1010` to `1310`.  Treat lexicographic low-setup tie-breaking
  inside the same append-style greedy initializer as a failed idea class, not a
  material improvement over the earlier setup-aware append attempts.
- A later worker tried to emit an empty proposal that simply reverted to the
  original setup-blind baseline with a generic risk note.  This is not a
  concrete slot-contract blocker and should be semantically repaired into a
  real hypothesis or rejected as no-op.
- After the empty-proposal guard was strengthened, DeepSeek generated a legal
  five-start SDST GRASP/RCL initialization portfolio with setup-aware append
  construction and internal best-makespan selection.  Under the current
  `critical + beta400/gamma40/theta5 + pct75` controls on `oddla20`, Core
  evaluation worsened from `1010` to `1039` and setup time from `1900` to
  `1960`; do not retry fixed small-RCL setup-aware append portfolios unless
  the hypothesis changes materially, for example by using true insertion
  positions, critical-tail estimates, or post-construction repair.
- A later tail-aware setup greedy candidate added a remaining-work suffix
  estimate to setup-aware append completion.  It was legal and reduced setup
  time to `1680`, but Core evaluation again worsened `oddla20` from `1010` to
  `1046`.  This reinforces that lowering aggregate setup time is not enough;
  future initialization hypotheses should target bottleneck-machine timing,
  true sequence insertion with precedence-safe recomputation, or leave
  initialization and shift effort to move evaluation/neighborhood control.
- A later bounded non-append insertion candidate tried to insert a newly
  released operation after the lowest-setup predecessor in an already committed
  machine sequence, then replayed that whole machine and rewrote global
  `job_ready` for already scheduled operations.  It crashed before evaluation
  with `ValueError: cycle detected in disjunctive graph` on `oddla20`.  Do not
  retry committed non-append insertion unless the proposal has an explicit
  acyclic/topological feasibility guard or only reorders an uncommitted
  temporary construction sequence.
- After the non-append guard was added, a legal static bottleneck initializer
  identified one machine by total minimum processing time, prioritized
  operations eligible on that bottleneck, and intentionally ignored setup
  times.  Core evaluation worsened `oddla20` from `1010` to `1029`, even
  though setup time fell from `1900` to `1730`.  Do not retry static
  single-bottleneck priority unless it is materially combined with dynamic
  readiness, critical-tail/regret pressure, or setup-aware feasibility rather
  than simply lowering setup/load diagnostics.
- Do not compare move/init mode constants with integers.
- Do not call `setup_time_between` with `current_op=None`.
- Import `setup_time_between` locally inside the slot before using it.

## Guardrails

- Only edit `awls_sdst_initialization`.
- Do not modify parser/evaluator/IO semantics.
- Do not change N7/NK scoring or zi policy in this stage.
