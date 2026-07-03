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

- True second-best-machine regret: for each ready operation, compute candidate
  machine costs, keep `best_machine_cost` and `second_best_machine_cost`, then
  use `regret = second_best_machine_cost - best_machine_cost` together with
  setup-aware completion and remaining tail pressure.  A variable named
  `regret` is not enough; it must compare the best and second-best machine
  choices.
- Assignment-then-sequencing: choose machines from setup-aware projected load,
  regret, and tail pressure, then sequence with the existing deterministic
  ready-operation loop.  This is safer than committing arbitrary insertion
  cycles.
- Bounded non-append insertion: only if the worker uses a real
  `AwlsSchedule(...)`, `topological_sort`, or `validate_standard_schedule`
  guard before returning the sequence.
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
- A subsequent non-append insertion/projection candidate simulated every
  insertion position, claimed "acyclic forward propagation", then committed
  `seq.insert(...)` and only recomputed that machine's local end times.  Core
  evaluation again failed before metrics with `ValueError: cycle detected in
  disjunctive graph`.  Textual acyclic claims are not enough: committed
  non-append insertion must use a real `AwlsSchedule(...)`, `topological_sort`,
  or `validate_standard_schedule` feasibility guard before returning.
- After the non-append guard was added, a legal static bottleneck initializer
  identified one machine by total minimum processing time, prioritized
  operations eligible on that bottleneck, and intentionally ignored setup
  times.  Core evaluation worsened `oddla20` from `1010` to `1029`, even
  though setup time fell from `1900` to `1730`.  Do not retry static
  single-bottleneck priority unless it is materially combined with dynamic
  readiness, critical-tail/regret pressure, or setup-aware feasibility rather
  than simply lowering setup/load diagnostics.
- A later initialization/regret prompt still produced append-only setup-aware
  completion variants with low-setup tie-breaks and no real second-best-machine
  regret.  Core blocked them before application with
  `initialization_retries_append_only_setup_completion`,
  `initialization_retries_low_setup_tiebreak`, and
  `unrepaired_must_repair_warning`.  Do not treat these as useful new
  candidates; the worker must implement actual best/second-best regret,
  assignment-then-sequencing, or topology-guarded insertion.
- After the second-best regret detector was widened, DeepSeek generated a legal
  `regret_driven_setup_aware_dispatch` initializer that selected the ready
  operation with maximum `second_best_comp - best_comp`, assigned it to the
  best setup-aware append machine, and tie-broke by best completion.  Core
  evaluation on `oddla20` worsened makespan from `1010` to `1066`, although
  setup time fell from `1940` to `1710`.  Do not retry maximum-regret
  append-only dispatch as the primary initialization rule; any future regret
  attempt must materially combine regret with critical-tail/bottleneck timing,
  post-construction repair, or topology-guarded insertion rather than merely
  choosing the highest regret ready operation.
- A later legal `regret_biased_setup_aware_dispatch` initializer computed true
  second-best-machine regret for every ready operation, then used append-only
  roulette/weighted-random selection by `(regret + 1)` plus idle-machine bonus.
  Core evaluation tied `oddla20` at `1010` while only reducing setup time from
  `1940` to `1910`, so it was rolled back.  Do not retry append-only
  second-best-regret roulette/weighted-random dispatch unless it adds a real
  critical-tail/bottleneck term, post-construction repair, assignment-then-
  sequencing phase, or topology-guarded insertion mechanism.
- A subsequent `sdst_regret_tail_ratio_init` initializer was legal but worsened
  `oddla20` from `1010` to `1138`.  It selected ready operations by
  `remaining_work / earliest_completion`, used true second-best regret only as
  a tie-breaker, and still appended to the chosen machine's tail.  Do not retry
  append-only remaining-work/earliest-completion tail-ratio dispatch; a future
  tail idea must change the machine sequence topology, add a real bottleneck
  repair phase, or use topologically guarded insertion.
- A later `regret_based_sdst_dispatching` variant again used append-only
  maximum second-best regret, this time with `op_priorities`, `max_regret`, and
  `best_machine` variables plus a non-SDST branch.  It was legal but worsened
  `oddla20` from `1010` to `1120`, even though setup time dropped sharply from
  `1940` to `1590`.  This reinforces that lower total setup is not the
  objective; do not retry classic max-regret append dispatch under renamed
  variables.
- A topology-required prompt still produced a renamed append-only priority
  formula instead of real topology repair.  Future rounds that ask for
  topology, repair, non-append insertion, or assignment-then-sequencing must be
  enforced structurally: require `AwlsSchedule(...).topological_sort()`, a
  guarded insert/swap, or a separate sequencing/repair phase in code before
  evaluation.
- A later topology/repair-focused worker run did not reach Core quality
  evaluation.  The first proposal again collapsed to append-only setup-aware
  regret/tail dispatch and was rejected by the semantic gate
  (`initialization_retries_append_only_setup_completion` and
  `initialization_missing_required_topology_or_repair`).  The second proposal
  used an invalid nested `replace_slot_block` schema and API from another
  project (`self.index`, `instance.n_jobs`, `instance.ops`,
  `instance.sds_data`) instead of the real `greedy_gt_init(index, rng, ...)`
  function-body contract.  Future repair prompts must explicitly name the real
  inputs: `index.instance.job_count`, `index.instance.machine_count`,
  `index.job_to_nodes`, `index.candidates[node]`,
  `index.duration(node, machine_id)`, `index.node_to_job[node]`, and
  `index.node_to_op[node]`.
- After the repair/API guidance was clarified, DeepSeek produced a legal
  `regret_priority_dispatch_with_setup` initializer.  It computed
  setup-aware best/second-best completion for each ready operation, selected
  maximum regret, and still appended `chosen_node` to `sequences[chosen_machine]`.
  Core evaluation on `oddla20` worsened the incumbent from `1010` to `1033`.
  Treat append-only maximum-regret dispatch as failed regardless of variable
  names (`node`, `chosen_node`, `best_machine`, or `chosen_machine`); it must
  not pass as a materially new initialization idea without a real topology,
  repair, non-append insertion, or separate sequencing phase.
- After `global_sdst_cooldown_boost`, a topology-required prompt produced
  `regret_driven_bounded_insertion`, but semantic repair rejected it before
  evaluator execution.  The proposal described non-append insertion and a
  same-job ordering guard, but did not contain a real
  `AwlsSchedule(...).topological_sort()`, `validate_standard_schedule(...)`,
  or equivalent feasibility check, and the repair still lacked acceptable
  `second_best - best` regret evidence in code.  Do not rely on textual
  "cycle guard" claims or same-job-only ordering as a topology proof.
- A later hard HUdata-six 12s worker produced a legal
  `setup_regret_tail_init` initializer: for each ready operation it computed
  setup-aware candidate-machine completion, added remaining minimum job tail,
  computed true `second_best_score - best_score`, then selected by
  `(regret, tail)` while still appending the operation to the chosen machine.
  Core evaluation tied the incumbent aggregate exactly at `1297.17` average
  makespan, so it was rolled back.  Do not retry append-only
  setup-regret-tail dispatch as the main initialization novelty; future
  initialization attempts still need topology-guarded insertion, repair,
  assignment-then-sequencing, or a distinct bottleneck/local-search mechanism.
- Do not compare move/init mode constants with integers.
- Do not call `setup_time_between` with `current_op=None`.
- Import `setup_time_between` locally inside the slot before using it.

## Guardrails

- Only edit `awls_sdst_initialization`.
- Do not modify parser/evaluator/IO semantics.
- Do not change N7/NK scoring or zi policy in this stage.
