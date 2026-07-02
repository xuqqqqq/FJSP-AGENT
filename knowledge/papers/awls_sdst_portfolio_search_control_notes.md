# AWLS-SDST Portfolio Search-Control Notes

## Why This Slot Matters

The current `oddla20` / `la20` short-budget incumbent is
`critical + beta400/gamma40/theta5 + critical_block_exhaustive_pct75`, reaching
`makespan=1010` against UB `997`.  Several scoring and candidate-generation
edits legally reduced setup time but worsened makespan, so the next high-value
lever is controlled search allocation: which seeded lanes run, how much time
they receive, and when a portfolio should stop or continue.

The fixed Core evaluator remains the authority.  Score is `-makespan`; LB/UB
are diagnostics only.

## Measured Evidence

- A direct seed/init grid around the incumbent did not beat `1010`.  Best ties
  were `mixed` seeds `0` and `6`, and `greedy` seed `7`; many other lanes were
  worse.
- The portfolio `0:mixed:1:6,6:mixed:1:6,7:greedy:1:6` tied `1010`.  It is a
  useful baseline but should not be repeated unchanged.
- The portfolio `2:mixed:1,3:random:1` paired with a simple critical formula
  worsened to `1024`.
- Longer or more exhaustive runs are not automatically better.  A longer
  pct-20 run worsened relative to shorter pct-20/pct-50 probes.
- `same_machine_eval=cpp-fast` under the incumbent worsened to `1039`, so a
  portfolio round should not spend its only novelty on that switch.
- A confirmed-slot worker candidate tried two-phase portfolio control: reserve
  `10%` or at least `2s`, run a proportional broad scan, then spend remaining
  time on the best lane with doubled restarts.  It was legal but tied
  `1010 -> 1010` on `oddla20` under the incumbent controls, so do not retry
  best-lane exploitation unchanged.

## Worker Directions

Use these as hypotheses, not as manual patches:

- Try materially different lane budget allocation, such as giving the first
  strong incumbent lane a short confirmation budget and reserving more time for
  lanes with different init modes.
- Try deterministic early-stop rules only when they preserve at least one full
  lane evaluation and leave lane diagnostics auditable.
- Try tie-breaking among equal makespans by keeping the lane with better future
  search statistics, but never promote lower setup time over makespan.
- Prefer lane sets not already measured: avoid exact repeats of
  `0:mixed:1:6,6:mixed:1:6,7:greedy:1:6` and `2:mixed:1,3:random:1`.
- If a candidate uses the same incumbent lanes, it must differ in a real search
  control rule such as budget scaling, lane order, or deterministic early stop.
- If revisiting two-phase exploitation, the novelty must be materially
  different from "scan all lanes, rerun current best with doubled restarts" and
  should explain why the prior tie would be broken.

## Guardrails

- Only edit `awls_sdst_portfolio_search_control`.
- Keep parser, evaluator, CLI argument names, solution schema, and benchmark
  score semantics fixed.
- Do not inspect LB/UB files or evaluator reports inside the solver.
- Keep lane execution bounded and deterministic through existing seeded AWLS
  calls.
- Preserve per-lane summaries so Core can audit which lane produced the result.
