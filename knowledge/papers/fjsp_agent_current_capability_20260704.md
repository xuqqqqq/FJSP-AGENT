---
id: fjsp-agent-current-capability-20260704
type: benchmark-evidence
title: Current FJSP Agent Capability Snapshot 2026-07-04
tags: [fjsp, sdst, benchmark, lb-ub, awls, agent-capability, rag-seed]
status: verified
---

## Purpose

This card is RAG material for future FJSP and FJSP-variant worker prompts.  It
summarizes what the current agent-generated AWLS/slot line can do under fixed
evaluator checks.  Use it to choose benchmark gates and avoid overclaiming from
single-instance runs.

## Standard FJSP: BA / BR / DP / HU

Evidence:
`outputs/eval_current_agent_standard4_5s_seed0_20260704/summary.json`

Contract:

- Instances: local BA/BR/DP/HU standard FJSP set, `313` total.
- Bounds: `knowledge/benchmarks/standard_fjsp_bounds_LB_UB.csv`.
- Budget: seed `0`, `5s` per instance, `max-workers=4`.
- Solver line: current `examples/standard_fjsp_awls_solver.py`, random init,
  `zi_policy=cpp`, stable same-machine evaluation.

Result:

- Valid instances: `313/313`.
- Bounds-covered gap count: `305/313`.
- Average gap to UB/BKS: `3.84%`.
- Median gap to UB/BKS: `1.78%`.
- Max gap to UB/BKS: `25.81%`.
- Reached UB/BKS: `67/305`.
- Within 2% of UB/BKS: `160/305`.

Family profile:

| Family | Instances | Avg UB gap | Median-ish signal | Main note |
| --- | ---: | ---: | ---: | --- |
| BA | 21 | 9.17% | 8.43% | Barnes remains weak under short budget. |
| BR | 10 | 4.21% | 1.74% | Several Brandimarte cases solved/reached, Mk07 is weak. |
| DP | 18 | 9.52% | 5.73% | Dauzere is the weakest standard family; target DP16/10/07. |
| HU | 264 | 2.98% | 1.26% | Hurink broad set is comparatively strong, but sdata-abz9 and some sdata/edata tails remain hard. |

Hard standard cases by UB gap:

- `fjsp.hurink.sdata-abz9.m15j20c1.txt`: `25.81%`.
- `fjsp.dauzere.16a.m10j20c4.txt`: `22.35%`.
- `fjsp.dauzere.10a.m8j15c4.txt`: `22.17%`.
- `fjsp.dauzere.07a.m8j15c4.txt`: `18.79%`.
- `fjsp.brandimarte.Mk07.m5j20c5.txt`: `18.71%`.

Implication for future standard-FJSP workers:

- Do not evaluate only on easy HU or Mk01-style cases.
- Include at least one BA case, one DP case, and one known hard HU/JSP-equivalent case in promotion probes.
- For DP/BA, prioritize critical-block insertion, machine reassignment, and restart/portfolio diversity over small zi-only tweaks.

## FJSP-SDST HUdata

Evidence:
`outputs/eval_current_agent_hudata20_30s_20260704/summary.json`

Contract:

- Instances: full HUdata SDST set, `oddla01.txt` ... `oddla20.txt`.
- Bounds: `SDST_HUdata_bounds_LB_UB.csv`.
- Budget: seed `0`, `30s` per instance, `max-workers=4`.
- Solver line: current setup-aware AWLS slots, random init, `zi_policy=critical`,
  `critical_block_exhaustive_pct=75`, stable same-machine evaluation.

Result:

- Valid instances: `20/20`.
- Average gap to UB/BKS: `6.81%`.
- Average gap to LB: `22.62%`.
- Median gap to UB/BKS: `7.10%`.
- Max gap to UB/BKS: `14.67%`.
- Reached UB/BKS: `0/20`.
- Within 2% of UB/BKS: `3/20`.

Hard HUdata cases by UB gap:

- `oddla12`: makespan `1227`, UB `1070`, gap `14.67%`.
- `oddla13`: makespan `1325`, UB `1172`, gap `13.05%`.
- `oddla11`: makespan `1387`, UB `1232`, gap `12.58%`.
- `oddla15`: makespan `1401`, UB `1258`, gap `11.37%`.
- `oddla14`: makespan `1362`, UB `1234`, gap `10.37%`.

Implication for future SDST workers:

- `oddla20` is no longer a good sole quality gate; it is relatively easy now
  (`2.81%` UB gap).
- Treat `oddla11`--`oddla15` as the main hard-shape promotion set.
- Prefer hypotheses that change bottleneck-machine sequencing for the
  20-job/5-machine/100-operation shape.
- Do not optimize setup time alone; previous legal candidates lowered setup
  time while worsening makespan.

## RAG Retrieval Hints

When a task mentions:

- Standard FJSP benchmark quality: retrieve this card plus
  `knowledge/benchmarks/fjsp_benchmark_scope.md` and FJSPLib cards.
- SDST / setup / HUdata: retrieve this card plus
  `knowledge/papers/awls_sdst_hudata20_baseline_notes.md` and the slot-specific
  notes for the selected code slot.
- Code-slot evolution: retrieve only the selected slot notes, this capability
  card, and the fixed evaluator/IO contract.  Avoid flooding the worker with
  every SDST note.

## Guardrails

- LB/UB are diagnostics and report fields, not solver inputs.
- Score remains `-makespan` unless a confirmed task contract changes it.
- Use full-set reports for claims; use targeted hard probes for iteration speed.
