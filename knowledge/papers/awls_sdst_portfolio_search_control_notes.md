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
- A later confirmed-slot worker candidate used distinct lanes
  `0:mixed,6:mixed,7:greedy,9:random` and weighted multi-lane deepening after
  a short probe.  It legally tied `1010`: lanes `0:mixed`, `6:mixed`, and
  `7:greedy` all reached `1010`, while `9:random` reached `1224`; weighted
  second-phase reruns of the tied lanes also stayed at `1010`.  Do not retry
  proportional or exponential deepening over the same tied lane family unless
  another search-control mechanism changes the move budget or accepts a
  different incumbent lane.
- A later diverse-lane portfolio run over
  `0:mixed,2:greedy,4:random,8:mixed` let DeepSeek assign per-lane
  beta/gamma/pct perturbations from a small grid:
  `cfg0=(400,40,75)`, `cfg1=(350,35,60)`, `cfg2=(450,45,80)`,
  `cfg3=(400,40,50)`.  Core evaluation tied the incumbent at `1010` because
  lane `0:mixed/cfg0` remained best; the other lanes reached `1086`, `1290`,
  and `1075`.  This suggests incumbent-adjacent per-lane parameter
  dissimilarity by itself is not enough to beat `la20`.
- A setup-aware AWLS-ZI round proposed `critical + pct80` with lanes
  `6:mixed:1:6,7:greedy:1:6,3:random:1:6`.  It worsened `oddla20` from
  `1010` to `1026`; the selected lane was `7:greedy` at `1026`, while
  `6:mixed` reached `1322` and `3:random` reached `1031`.  Do not retry this
  lane string unchanged under the same incumbent controls.
- A follow-up setup-aware formula portfolio round worsened two more lane
  families:
  - `2:random:1:6,5:greedy:1:6,8:mixed:1:6` with a backward/setup-next formula
    reached `1042` (`2:random=1042`, `5:greedy=1135`, `8:mixed=1051`).
  - `1:greedy:1:6,3:random:1:6,7:mixed:1:6` with a setup-prev critical formula
    reached `1099` (`1:greedy=1099`, `3:random=1111`, `7:mixed=1158`).
  Do not retry these lane strings unchanged with setup-ratio zi formulas.
- A confirmed portfolio-search-control slot run over
  `0:mixed:1:8,4:greedy:1:8,8:random:1:8` changed only the deterministic
  effective lane seed mapping to
  `(lane.seed + seed * STRIDE + idx * 7919) % 10000`.  It legally tied
  `oddla20` at `1010` and did not beat the incumbent.  Do not retry
  seed-mapping-only perturbations unless paired with a real lane budget,
  ordering, early-stop, or tie-breaking mechanism.

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
- Do not spend a future portfolio round only on small per-lane perturbations of
  `beta/gamma/critical_block_exhaustive_pct` around the incumbent unless it is
  paired with a different lane set, construction profile, or move-budget rule;
  the tested diverse-lane parameter grid tied at `1010`.
- Do not retry `6:mixed:1:6,7:greedy:1:6,3:random:1:6` with
  `critical_block_exhaustive_pct=80`; it worsened to `1026`.
- Do not retry `2:random:1:6,5:greedy:1:6,8:mixed:1:6` or
  `1:greedy:1:6,3:random:1:6,7:mixed:1:6` unchanged with setup-ratio formula
  policies; both worsened beyond `1040`.
- Do not spend a future portfolio round only changing the effective seed
  formula, including prime/modulo offsets such as `idx * 7919`; this tied
  `1010` on `oddla20`.

## Guardrails

- Only edit `awls_sdst_portfolio_search_control`.
- Keep parser, evaluator, CLI argument names, solution schema, and benchmark
  score semantics fixed.
- Do not inspect LB/UB files or evaluator reports inside the solver.
- Keep lane execution bounded and deterministic through existing seeded AWLS
  calls.
- Preserve per-lane summaries so Core can audit which lane produced the result.
