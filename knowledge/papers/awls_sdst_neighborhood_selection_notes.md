# AWLS-SDST Neighborhood Selection Notes

## Why This Slot Matters

The current AWLS-SDST solver already has setup-aware time propagation and legal
schedule validation, but the local-search quality on HUdata `la20` remains far
from the published UB (`997`; current fast/short baselines are `1202` or
`1245`).  Earlier isolated scoring attempts did not lower makespan:

- NK/change-machine exact scoring tied `1202` on the stronger short budget.
- N7/same-machine setup-aware propagation tied `1245` on the fast budget, even
  though setup time dropped from `1900` to `1850`.
- Plain setup-aware greedy append initialization was legal but worsened the
  fast budget to `1320`.
- Near-critical N7/NK expansion using `gamma // 5` and extreme same-machine
  insertions was legal but worsened the stronger `oddla20` short budget from
  `1202` to `1280`.
- Direct seed sweep on `oddla20` with the same short budget found strong seed
  variance: seeds `0..4` produced best `1177` at seed `2`, while seed `3`
  produced `1383`.  The platform should treat seed/portfolio policy as a real
  search lever, not just noise.
- HUDATA bounds files may name `oddla20.txt` as `la20`.  Gap diagnostics should
  use the `laXX` UB (`la20` UB is `997`) while scoring remains `-makespan`.
- AWLS-ZI structured evolution with DeepSeek proposed `zi_policy=aggressive`
  and improved `oddla20` seed-0 short-budget quality to `1154` (gap
  `15.75%` vs UB `997`).  A simple formula
  `base * (1 + 0.3 * is_critical)` worsened to `1202`.
- A follow-up two-round AWLS-ZI run confirmed `zi_policy=aggressive` with
  default `beta/gamma/theta=500/40/5` as the current best (`1154`).  Tuning it
  to `gamma=60, theta=3` worsened to `1202`; critical formulas worsened to
  `1202` or `1265`; simple mixed-seed portfolios tied `1177` or `1154`.
- Direct AWLS parameter probes found `critical_block_exhaustive_pct` is a high
  leverage control for SDST `oddla20`: with `zi_policy=aggressive`, pct `5`
  and `10` reached `1078`, pct `20` reached `1024`, and pct `50` reached
  `1023` (gap `2.61%` vs UB `997`) on the short seed-0 budget.
- After the evolution layer was allowed to search pct `0..100`, DeepSeek found
  `zi_policy=critical`, `beta=400`, `gamma=40`, `theta=5`, pct `75`, which
  reached `1010` on `oddla20` seed `0` with the same short budget (gap
  `1.30%` vs UB `997`).  Treat `critical + pct 75` as the current incumbent
  for this la20 line.
- Follow-up AWLS-ZI evolution from the `1010` incumbent did not improve:
  `cpp + pct 75` tied `1010`; `base * (1 + 0.5 * is_critical)` at pct `75`
  tied `1010`; `critical + pct 100` worsened badly to `1138`.
- Direct local grid around the incumbent showed a plateau, not a smooth pct
  optimum: `critical` with beta `400` tied `1010` at pct `60`, `65`, `75`,
  `80`, and `90`; pct `70` worsened to `1024`, pct `85` worsened to `1039`.
  Beta `300`, `500`, and `600` at pct `75` also tied `1010`.
- A seed/init grid for `critical + beta400/gamma40/theta5 + pct75` over seeds
  `0..9` and `random/greedy/mixed` did not beat `1010`.  Best ties were
  `mixed` seeds `0` and `6`, and `greedy` seed `7`; `random` seed `7` reached
  `1015`, while many other lanes were worse.
- In the same round, the formula candidate
  `max(0, base * (1 + 0.3 * is_critical))` with lanes
  `2:mixed:1,3:random:1` reached only `1024`, so the old critical multiplier
  formula remains unattractive even with a small portfolio.
- More exhaustive or longer is not automatically better.  `exact_select_top_k`
  with aggressive scoring worsened to `1265`, and a longer pct-20 run
  (`60` cycles, `500` iterations, `60s`) worsened to `1033`.
- After AWLS-ZI prompt memory was expanded to include move-evaluation,
  initialization, and same-machine failure cards, a one-round structured
  evolution from the `1010` incumbent proposed:
  - `critical + pct75 + same_machine_eval=cpp-fast`, which worsened to `1039`.
  - `formula=max(0, base * (1 + 0.3 * is_critical * max(0, 1 - cooldown/max(1, rr))))`
    at pct `75`, which tied `1010`.
  Do not spend the next AWLS-ZI round on only switching `same_machine_eval` to
  `cpp-fast` or adding another small critical/cooldown multiplier formula.
  A materially different next hypothesis should use a bounded lane/portfolio,
  seed/init mix, or another search-control change that is not just a zi formula
  perturbation.
- After the AWLS-ZI prompt explicitly required at least one non-empty
  `portfolio_lanes` candidate when multiple candidates are requested, DeepSeek
  proposed `0:mixed:1:6,6:mixed:1:6,7:greedy:1:6` with
  `critical + beta400/gamma40/theta5 + pct75`.  It legally tied `1010` rather
  than improving.  A second formula candidate
  `max(0, base * (1 + 0.3 * is_critical * sqrt_weight))` also tied `1010`.
  Do not retry this exact three-lane portfolio or this sqrt critical formula
  unchanged.  If using portfolio again, change the lane budget or choose lanes
  not already known to tie the incumbent, and keep the comparison under the
  same evaluator score `-makespan`.
- A later structured AWLS-ZI round after slot-failure memory still did not
  improve the `1010` incumbent:
  - `sqrt + stable + pct75` worsened `oddla20` to `1051`.
  - `aggressive + pct75` with lanes
    `1:random:1:6,7:greedy:1:6,9:mixed:1:6` tied `1010`.
  Do not retry these unchanged; future structured candidates should either use
  a materially different setup-aware formula or a lane/search-control change
  that is not just another small policy/seed mix.
- A direct single-seed probe after those guards confirmed the plain pct/policy
  line is saturated under `beta400/gamma40/theta5 + stable + mixed`: `critical`
  pct `50/60/75/90` produced `1023/1010/1010/1010`, while `aggressive` pct
  `50/75` produced `1014/1030`.  Do not spend another structured round only on
  these same no-portfolio settings; require a materially different formula,
  lane budget/order, initialization mix, or slot-level move mechanism.
- Generic slot-worker attempts on `awls_sdst_neighborhood_selection` did not
  beat the `1010` incumbent:
  - Near-critical filter plus same-machine +/-10 window tied `1010`.
  - Same-machine +/-3 window plus near-critical NK boundary insertion worsened
    to `1039` and increased setup time to `1950`.
  - Tight near-critical insertion into critical blocks worsened to `1030`
    even though setup time dropped to `1840`; lower setup time alone was not
    enough to lower makespan.
- A later neighborhood slot run asked for setup-heavy arc augmentation while
  preserving incumbent traversal, but DeepSeek placed the setup-heavy moves
  behind `if not all_moves`; it legally tied `1010`, so the idea rarely affects
  the active search path.  The next setup-heavy proposal must participate in
  the main incumbent traversal with strict bounds, not only as a no-candidate
  fallback.
- In the same run, a high-setup operation filter replaced broad move generation
  and crashed at runtime with `AttributeError: 'AwlsSchedule' object has no
  attribute 'setup_time'`.  `AwlsSchedule`, `OperationIndex`, and `index` do
  not expose `setup_time`; setup lookup must use `setup_time_between` with
  operation-key tuples.
- A follow-up run after setup-fallback/API guards tried search-control pruning:
  iteration-parity lane switching skipped change-machine generation whenever
  same-machine moves already existed on even iterations and worsened `oddla20`
  from `1010` to `1134`.  A stochastic same-machine subsampling proposal was
  malformed with an `unexpected indent` compile error before evaluation.  Do
  not reduce the neighborhood by parity skipping change-machine moves or random
  same-machine block subsampling unless paired with stronger evidence and valid
  slot syntax.
- A guarded neighborhood-selection round asked for materially different
  boundary/NK/setup-arc candidates, but DeepSeek proposed
  `PrunedCriticalBlockNeighborhood`: sort critical blocks by total processing
  time, limit same-machine external targets to two nearest positions, cap move
  count at `max(50, 2 * operations)`, and cap NK targets.  It failed before
  quality evaluation with `AttributeError: 'OperationIndex' object has no
  attribute 'durations'` because it used `schedule.index.durations[node]`.
  `OperationIndex` exposes `schedule.index.duration(node, machine_id)`, not a
  `durations` field.  The platform should semantically repair proposals that
  use this nonexistent API before evaluator time is spent.
- After the API guard was added, a two-round neighborhood-selection run produced
  legal but non-improving candidates:
  - `fallback_random_shake` added random same-machine insertion candidates only
    when `all_moves` was empty.  It tied the `oddla20` incumbent at `1010`;
    this fallback rarely changes the active search because the baseline usually
    already has candidates.
  - `same_machine_first_fallback_change` removed exhaustive-mode selection and
    generated change-machine candidates only if same-machine candidates left
    `all_moves` empty.  It worsened `oddla20` from `1010` to `1295`; the run
    selected only `5` moves instead of the incumbent `6000`, showing that
    over-pruning the neighborhood can collapse the tabu search.
- After wrong-slot edit repair was added, DeepSeek generated a legal
  `lateness_focused_block_selection` candidate that discarded the incumbent
  exhaustive/non-exhaustive critical-block pass, sorted non-exhaustive
  `critical_blocks` by latest `end_time`, kept only `top_k = 3` blocks for
  same-machine moves, and always generated change-machine moves for critical
  operations.  Core evaluation worsened `oddla20` from `1010` to `1280`
  despite setup time falling to `1850`.  Do not retry fixed latest-block
  top-K pruning as the whole neighborhood replacement; it removes too much
  useful same-machine search.
- A literature-direction neighborhood run asked for critical-block
  assignment-then-sequencing moves, but the worker instead proposed a flat
  `MAX_MOVES=200` global cap while preserving the old move families.  It
  legally tied `oddla20` at `1010`, so do not retry global candidate-count
  caps as the primary novelty.
- The follow-up `BoundedDiversitySampling` proposal randomly sampled up to
  ten critical blocks, capped same-machine moves to three per block, capped
  total moves around fifty, and shuffled critical nodes/candidate machines.
  It failed at runtime with `KeyError` because `schedule.index.candidates[node]`
  is a dict and `schedule.rng.shuffle(...)` requires a mutable sequence.  Do
  not retry random diversity sampling with small fixed caps, and convert dict
  keys to a list before any seeded shuffle.
- A later main-path boundary/NK prompt asked for bounded candidates that
  participate in the incumbent traversal, but the worker proposed
  `randomized_change_only_lane`: with 50% probability it skipped all
  same-machine/N7 candidate generation and kept only change-machine moves.  It
  was legal but worsened `oddla20` from `1010` to `1039`.  Do not retry random
  change-only lanes that omit the same-machine critical-block path; future
  diversification should add or order candidates without deleting the incumbent
  N7 traversal.
- A follow-up preserve-main-path round proposed `bounded_nk_alternate_machine`:
  keep incumbent same-machine moves but convert candidate machines to a list
  and take the first three `bounded_candidates[:max_candidate_machines]`
  without setup/load/slack ordering.  It was legal but tied `oddla20` at
  `1010`.  Do not retry unordered first-N candidate-machine caps as the main
  novelty; if alternate machines are bounded, sort them by an actual
  setup-aware or load/slack score before slicing.
- After `global_sdst_cooldown_boost`, a guarded neighborhood round proposed
  `bounded_neighborhood_sampling`: preserve the broad incumbent structure but
  randomly shuffle/slice same-machine side targets to 8 and change-machine
  insertion targets to 5.  It was legal but tied `oddla20` at `1010` and
  increased setup time from `1890` to `1940`.  Do not retry random target
  subsampling or small fixed same/change target caps as the main novelty; if
  bounding is needed, order candidates by a concrete setup/load/slack score and
  keep the incumbent N7/NK traversal intact.

This points toward the move-candidate selection layer: the search may not be
trying the right critical or near-critical N7/NK moves often enough.

## Worker Directions

Use these as hypotheses, not as manual patches:

- Avoid another pure near-critical/window-pruning tweak.  Recent slot-worker
  attempts that only changed near-critical thresholds or bounded windows tied
  or worsened the `1010` incumbent.
- Boundary-biased N7 moves: for each critical block, try bounded moves that
  place interior operations just outside the block or move boundary operations
  deeper inside the block.
- Bounded NK alternatives: for near-critical operations, try a small number of
  alternate-machine insertion positions from `change_machine_window`, rather
  than only the original intersection cases.
- Setup-arc focus for SDST: when ordering candidate nodes, favor moves touching
  machine arcs with high setup contribution, but still submit them through
  `consider_same` / `consider_change`.
- Avoid random target subsampling as the main mechanism.  The latest legal
  bounded sampling tied makespan and worsened setup time.
- If this slot remains stuck, switch to `awls_sdst_initialization` or
  `awls_sdst_same_machine_evaluation`; the current neighborhood-selection slot
  has now produced legal but non-improving candidates.
- Keep exploration seeded and bounded.  Large exhaustive scans can waste the
  short worker-loop budget and may make final-quality comparisons noisy, but
  do not cap `critical_block_exhaustive_pct` below `50` during AWLS-ZI evolution
  because pct `50` is the best measured `oddla20` setting so far.

## Prior Failure Memory

- Do not bypass `consider_same` or `consider_change`; they centralize legality,
  scoring, tabu filtering, and ranked/exact bookkeeping.
- Do not directly mutate `schedule` in candidate generation.
- Do not call `trial.apply_move` inside this slot; exact top-k after the slot
  already handles clone/apply validation.
- Do not compare method constants with integers.  They are strings:
  `FRONT`, `BACK`, `CHANGE_MACHINE_FRONT`, `CHANGE_MACHINE_BACK`.
- Do not retry the broad `near_critical_gap = gamma // 5` expansion with
  same-machine front/back extremes unless it is materially narrowed or paired
  with evidence that the added candidates are improving exact top-k outcomes.
- Do not retry critical-boost zi formulas of the form
  `base + k * weight * is_critical` unless the coefficient or context differs
  materially; tested `k=0.3` and `k=0.02` were worse than aggressive zi.
- Do not retry `base * (1 + 0.3 * is_critical)` with the simple
  `2:mixed:1,3:random:1` lane portfolio; it was worse than both pct-50
  aggressive and pct-75 critical incumbents.
- Do not retry `sqrt + stable + pct75`; it worsened `oddla20` from `1010` to
  `1051`.
- Do not retry `aggressive + pct75` with
  `1:random:1:6,7:greedy:1:6,9:mixed:1:6`; it tied `1010`.
- Do not retry no-portfolio direct pct probes under
  `beta400/gamma40/theta5 + stable + mixed`: `critical` pct `50/60/75/90` and
  `aggressive` pct `50/75` have now tied or worsened the `1010` incumbent.
- Do not assume pct `100` is a stronger version of pct `75`; with
  `zi_policy=critical`, pct `100` worsened `1010 -> 1138`.
- `cpp + pct 75` and `base * (1 + 0.5 * is_critical)` at pct `75` only tied
  the `critical + pct 75` incumbent; retry only if paired with a new seed,
  construction mode, or another materially different knob.
- Do not spend another round only sweeping `critical` beta at pct `75` or pct
  neighbors near `60..90`; the measured grid found ties or regressions.  The
  next distinct lever should be seed/initialization portfolio, move structure,
  or a formula that changes more than a critical multiplier.
- Do not expect a simple `critical+pct75` seed/init portfolio over seeds `0..9`
  to beat `1010`; measured lanes only tied or regressed.
- Do not retry near-critical/window-only slot replacements:
  `0.99*makespan` filters, +/-10 or +/-3 same-machine windows, and tight
  tardiness `>-5` critical-block insertion have all tied or worsened
  `oddla20` under the `critical + beta400/gamma40/theta5 + pct75` baseline.
- Do not retune aggressive zi by only increasing `gamma` and lowering `theta`
  without another change; `gamma=60, theta=3` was worse than default.
- Do not assume increasing total runtime alone improves this setting; the
  tested longer pct-20 run was worse than the short pct-20 and pct-50 runs.
- If setup lookup is used only for candidate ordering, convert node ids to
  `(job_id, op_id)` and pass `schedule.index` as the op-index mapping.
- Do not use `schedule.index.durations[...]` in this slot.  Use
  `schedule.index.duration(node, schedule.on_machine[node])` for current-machine
  processing time, or `schedule.index.duration(node, candidate_machine)` for a
  candidate-machine processing time.
- Do not add random shake moves only under `if not all_moves`; this tied
  `1010` and is usually a dead fallback under the incumbent generator.
- Do not add setup-heavy SDST arc moves only under `if not all_moves`; this
  also tied `1010` and usually does not influence the incumbent generator.
- Do not use nonexistent setup APIs such as `schedule.setup_time`,
  `schedule.index.setup_time`, or `index.setup_time`; use `setup_time_between`
  with operation-key tuples.
- Do not use iteration parity to skip all change-machine generation whenever
  same-machine moves exist; this worsened `oddla20` from `1010` to `1134`.
- Do not randomly subsample away same-machine block candidates without a valid
  syntax-checked slot and stronger evidence; the first attempt failed before
  evaluation due to malformed indentation.
- Do not put all `change_machine_window` / `consider_change` calls behind
  `if not all_moves` after same-machine generation.  This over-pruned the
  search to `5` selected moves and worsened `oddla20` to `1295`.
- Do not replace the incumbent dual exhaustive/non-exhaustive block traversal
  with only `critical_blocks(..., exhaustive=False)` sorted by latest
  `end_time` and clipped to a small fixed `top_k`; the tested top-3 version
  worsened `oddla20` to `1280`.
- Do not retry flat global move-count caps such as `MAX_MOVES=200`; that tied
  `1010` and did not implement the requested critical-block
  assignment-then-sequencing idea.
- Do not retry random diversity sampling with `max_blocks`, `max_same_per_block`,
  `max_change_per_node`, or `total_move_limit` as the main novelty; the first
  attempt failed at runtime and is too close to over-pruning.
- Do not retry random change-machine-only lanes that guard same-machine/N7
  generation behind `if not use_change_only`; the 50% variant worsened
  `oddla20` from `1010` to `1039`.
- Do not retry unordered first-N alternate-machine caps such as
  `max_candidate_machines = 3` followed by `bounded_candidates[:3]`; this tied
  `oddla20` at `1010` without improving the Core objective.
- Do not call `schedule.rng.shuffle(schedule.index.candidates[node])`;
  `schedule.index.candidates[node]` is a dict.  Use
  `list(schedule.index.candidates[node])` before shuffling candidate machines.

## Guardrails

- Only edit `awls_sdst_neighborhood_selection`.
- Do not modify parser/evaluator/IO semantics.
- Do not change move scoring or zi policy in this stage.
- Score remains `-makespan`; LB/UB are diagnostics only.
