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

This points toward the move-candidate selection layer: the search may not be
trying the right critical or near-critical N7/NK moves often enough.

## Worker Directions

Use these as hypotheses, not as manual patches:

- Near-critical expansion: include operations whose
  `end_time[node] + backward_path_length[node]` is close to `makespan`, not
  only exactly critical nodes.
- Boundary-biased N7 moves: for each critical block, try bounded moves that
  place interior operations just outside the block or move boundary operations
  deeper inside the block.
- Bounded NK alternatives: for near-critical operations, try a small number of
  alternate-machine insertion positions from `change_machine_window`, rather
  than only the original intersection cases.
- Setup-arc focus for SDST: when ordering candidate nodes, favor moves touching
  machine arcs with high setup contribution, but still submit them through
  `consider_same` / `consider_change`.
- Keep exploration seeded and bounded.  Large exhaustive scans can waste the
  short worker-loop budget and may make final-quality comparisons noisy.

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
- If setup lookup is used only for candidate ordering, convert node ids to
  `(job_id, op_id)` and pass `schedule.index` as the op-index mapping.

## Guardrails

- Only edit `awls_sdst_neighborhood_selection`.
- Do not modify parser/evaluator/IO semantics.
- Do not change move scoring or zi policy in this stage.
- Score remains `-makespan`; LB/UB are diagnostics only.
