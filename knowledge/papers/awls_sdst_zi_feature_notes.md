# AWLS-SDST zi Feature Notes

## Why This Slot Matters

The current AWLS zi evolution plateaued on `oddla20` / `la20` at
`makespan=1010` with `critical + beta400/gamma40/theta5 + pct75`.  Previous
formula attempts mostly changed critical/cooldown multipliers, so they did not
give the agent a way to reason about sequence-dependent setup arcs.  The
`awls_sdst_zi_features` slot exposes setup-aware numeric features to
`zi_policy=formula` and `zi_policy=slot` while keeping default AWLS policies and
the fixed evaluator unchanged.

The fixed Core evaluator remains the authority.  Score is `-makespan`; LB/UB
are diagnostics only.

## Feature Contract

Formula/slot zi policies can read the existing AWLS values plus these SDST
features:

- `setup_prev`: setup time from the current machine predecessor to this
  operation.
- `setup_next`: setup time from this operation to the current machine successor.
- `setup_adjacent`: `setup_prev + setup_next`.
- `setup_prev_ratio`, `setup_next_ratio`, `setup_adjacent_ratio`: setup values
  divided by this operation's processing duration on its current machine.
- `setup_is_sdst`: `1.0` when the instance has sequence-dependent setup data,
  otherwise `0.0`.
- `setup_predecessor_critical`, `setup_successor_critical`: whether adjacent
  machine neighbors are critical operations.

These are scoring features only.  They must not change parser, evaluator,
solution schema, or benchmark score semantics.

## Measured Cautions

- Lower setup time alone has not been enough: move-evaluation candidates
  reduced setup time from `1900` to `1840` but worsened makespan to `1030` or
  `1032`.
- Pure critical multipliers tied or worsened the `1010` incumbent:
  `base * (1 + 0.3 * is_critical)`, `base * (1 + 0.5 * is_critical)`, and
  small cooldown/critical variants did not improve.
- `same_machine_eval=cpp-fast` worsened `1010 -> 1039`; do not combine setup
  features with that switch unless the hypothesis is materially different.
- Do not use setup features as a replacement objective.  A useful formula
  should still preserve makespan pressure through `base`, `forward`,
  `backward`, or criticality.
- The first confirmed-slot worker attempt on `awls_sdst_zi_features` returned
  an empty proposal with no risk note.  The platform rejected it via
  `empty_slot_proposal_without_risk_note`.  Future attempts must either provide
  one concrete feature-extraction edit and hypothesis, or explain a concrete
  blocker in `risk_notes`.
- After semantic proposal repair was added, a worker proposed replacing
  duration-only setup ratios with bounded self-normalized ratios
  `setup / (setup + duration)`.  The proposal was legal but tied the
  `oddla20` incumbent (`1010 -> 1010`) under `zi_policy=formula` with
  `zi_formula=base`, so do not retry this exact normalization-only feature
  change unchanged.
- A setup-aware AWLS-ZI structured round tested actual formula use of these
  features under the short `oddla20` gate:
  - `max(0, base * (1 + 0.2 * is_critical * setup_adjacent_ratio))` with
    portfolio `1:mixed:1:10,4:mixed:1:10` worsened `1010 -> 1050`.
  - `max(0, base * (1 + 0.3 * is_critical * setup_next_ratio))` tied
    `1010 -> 1010`.
  Do not retry these exact formulas or the `1:mixed:1:10,4:mixed:1:10`
  portfolio unchanged.

## Worker Directions

Use these as hypotheses, not as manual patches:

- Penalize high adjacent setup only when the operation is also critical or has
  large backward tail, e.g. setup-aware pressure gated by `is_critical`.
- Try asymmetric formulas: predecessor setup may indicate bad incoming arcs,
  while successor setup may indicate downstream disruption.
- Use setup ratios rather than raw setup when durations vary widely.
- Keep formulas short and interpretable so failed rounds can be attributed.
- If a setup-aware formula ties, the next attempt should alter the gate
  condition or pair with a distinct portfolio/search-control hypothesis rather
  than only changing a coefficient.
- Do not spend another round only changing setup ratio normalization unless a
  formula or downstream zi policy actually uses the new feature differently.
- Prefer the next formula to change the gate structure, for example combining
  setup features with `backward`, `forward`, or neighbor-critical flags, rather
  than only multiplying `base` by `is_critical * setup_*_ratio`.

## Guardrails

- Only edit `awls_sdst_zi_features` when changing feature extraction.
- Only edit `awls_zi_policy` / `EVOLVE` when changing the evolved zi function.
- Convert AWLS node ids to `(job_id, op_id)` with `operation_key`; never pass
  node ids directly to `setup_time_between`.
- Missing predecessor/successor edges contribute zero setup.
- Do not read LB/UB, evaluator reports, instance files, environment variables,
  network, or filesystem state inside the solver.
