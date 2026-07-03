# AWLS-SDST HUdata20 Baseline Notes

## Purpose

This card records the current Core-evaluator baseline on the full SDST-HUdata
`oddla01`--`oddla20` set.  It is worker prompt material, not a solver patch.
Future slot proposals should use it to avoid overfitting only `oddla20`.

## Source And Command

Instances:
`C:/Users/ASUS/Downloads/FJSP_SDST_HUdata_instances_package/FJSP_SDST_HUdata_instances_package/instances/oddla*.txt`

Bounds:
`C:/Users/ASUS/Downloads/FJSP_SDST_benchmark_bounds_package/SDST_HUdata_bounds_LB_UB.csv`

Local evidence:
`outputs/hudata20_awls_sdst_ijcai30s_20260703/summary.json`

Command shape:

```powershell
python -m harness_agent.cli run-awls-benchmark `
  --instance-dir C:/Users/ASUS/Downloads/FJSP_SDST_HUdata_instances_package/FJSP_SDST_HUdata_instances_package/instances `
  --pattern oddla*.txt `
  --best-known-csv C:/Users/ASUS/Downloads/FJSP_SDST_benchmark_bounds_package/SDST_HUdata_bounds_LB_UB.csv `
  --seeds 0 `
  --max-workers 4 `
  --awls-time-limit-sec 30 `
  --awls-init random `
  --awls-beta 400 `
  --awls-gamma 40 `
  --awls-theta 5 `
  --awls-critical-block-exhaustive-pct 75 `
  --same-machine-eval stable `
  --awls-zi-policy critical `
  --awls-time-check-interval 1000
```

All 20 runs were legal under the fixed evaluator.  Score remains
`-makespan`; UB/BKS values are gap diagnostics only.

## Result Summary

- Valid runs: `20/20`.
- Average gap to UB/BKS: `6.93%`.
- Median gap to UB/BKS: `7.09%`.
- Max gap: `15.23%`.
- Within 2% of UB/BKS: `4/20`.
- Reached UB/BKS: `0/20`.

Per-instance makespan and UB gap:

| Instance | Shape | Makespan | UB/BKS | Gap |
| --- | --- | ---: | ---: | ---: |
| oddla01 | 10 jobs, 5 machines, 50 ops | 729 | 721 | 1.11% |
| oddla02 | 10 jobs, 5 machines, 50 ops | 785 | 737 | 6.51% |
| oddla03 | 10 jobs, 5 machines, 50 ops | 673 | 652 | 3.22% |
| oddla04 | 10 jobs, 5 machines, 50 ops | 695 | 673 | 3.27% |
| oddla05 | 10 jobs, 5 machines, 50 ops | 621 | 602 | 3.16% |
| oddla06 | 15 jobs, 5 machines, 75 ops | 1022 | 945 | 8.15% |
| oddla07 | 15 jobs, 5 machines, 75 ops | 978 | 902 | 8.43% |
| oddla08 | 15 jobs, 5 machines, 75 ops | 1012 | 940 | 7.66% |
| oddla09 | 15 jobs, 5 machines, 75 ops | 1089 | 984 | 10.67% |
| oddla10 | 15 jobs, 5 machines, 75 ops | 1038 | 953 | 8.92% |
| oddla11 | 20 jobs, 5 machines, 100 ops | 1387 | 1232 | 12.58% |
| oddla12 | 20 jobs, 5 machines, 100 ops | 1233 | 1070 | 15.23% |
| oddla13 | 20 jobs, 5 machines, 100 ops | 1320 | 1172 | 12.63% |
| oddla14 | 20 jobs, 5 machines, 100 ops | 1397 | 1234 | 13.21% |
| oddla15 | 20 jobs, 5 machines, 100 ops | 1424 | 1258 | 13.20% |
| oddla16 | 10 jobs, 10 machines, 100 ops | 1040 | 1007 | 3.28% |
| oddla17 | 10 jobs, 10 machines, 100 ops | 861 | 851 | 1.18% |
| oddla18 | 10 jobs, 10 machines, 100 ops | 1003 | 985 | 1.83% |
| oddla19 | 10 jobs, 10 machines, 100 ops | 976 | 951 | 2.63% |
| oddla20 | 10 jobs, 10 machines, 100 ops | 1014 | 997 | 1.71% |

## Interpretation For Workers

`oddla20` is no longer the only useful gate.  It is near UB under the current
line, while `oddla11`--`oddla15` are much worse.  These hard cases share a
20-job, 5-machine, 100-operation shape, so machine capacity is tighter and
bottleneck sequencing is more likely to dominate simple alternate-machine
reassignment.

Good next hypotheses should include at least one of:

- Bottleneck-machine sequencing pressure for the 20-job/5-machine shape.
- Same-machine N7 scoring that changes move ordering on overloaded machines,
  not only total setup time.
- Initialization or repair that preserves dynamic readiness while balancing
  bottleneck load and critical-tail pressure.
- Portfolio/search-control logic that allocates effort to shapes where the
  baseline gap is high, while keeping evaluator score as makespan.

Avoid:

- Claiming success from `oddla20` alone.
- Tuning only for lower aggregate setup time; several legal candidates lowered
  setup but worsened makespan.
- Using LB/UB inside solver logic.  They are diagnostics only and must not
  enter objective, move score, or acceptance code.
- Retrying append-only setup-aware dispatch or pure exact-trial N7 scoring
  unchanged; both are already failure memory.

## Latest Current-Line Recheck

After accepting the critical-SDST capped-tenure tabu memory and adding
neighborhood over-pruning guards, the current solver line was rerun on all 20
HUdata instances with the same seed-0, 30s-per-instance fixed-time contract:

Evidence:
`outputs/hudata20_awls_sdst_current_seed0_30s_20260703/summary.json`

- Valid runs: `20/20`.
- Average makespan: `1017.15`.
- Average gap to UB/BKS: `7.19%`.
- Median gap to UB/BKS: `7.10%`.
- Max gap: `15.80%`.
- Within 2% of UB/BKS: `3/20`.
- Reached UB/BKS: `0/20`.

Per-instance current-line makespan and UB gap:

| Instance | Makespan | UB/BKS | Gap |
| --- | ---: | ---: | ---: |
| oddla01 | 729 | 721 | 1.11% |
| oddla02 | 787 | 737 | 6.78% |
| oddla03 | 683 | 652 | 4.75% |
| oddla04 | 701 | 673 | 4.16% |
| oddla05 | 621 | 602 | 3.16% |
| oddla06 | 1015 | 945 | 7.41% |
| oddla07 | 979 | 902 | 8.54% |
| oddla08 | 1023 | 940 | 8.83% |
| oddla09 | 1081 | 984 | 9.86% |
| oddla10 | 1024 | 953 | 7.45% |
| oddla11 | 1387 | 1232 | 12.58% |
| oddla12 | 1236 | 1070 | 15.51% |
| oddla13 | 1325 | 1172 | 13.05% |
| oddla14 | 1429 | 1234 | 15.80% |
| oddla15 | 1401 | 1258 | 11.37% |
| oddla16 | 1061 | 1007 | 5.36% |
| oddla17 | 871 | 851 | 2.35% |
| oddla18 | 1003 | 985 | 1.83% |
| oddla19 | 962 | 951 | 1.16% |
| oddla20 | 1025 | 997 | 2.81% |

The accepted critical capped-tenure line previously measured `6.84%` average
gap on one 30s HUdata20 run, while this recheck measured `7.19%`.  Treat small
single-run changes as noisy under wall-clock budgets; require repeat promotion
or a clear targeted hard-shape gain before claiming a solver improvement.

## 2026-07-03 Rerun

The same current solver line was rerun again on all 20 HUdata instances with
seed `0`, `30s` per instance, `max-workers=4`, random initialization,
`critical` zi policy, `beta=400`, `gamma=40`, `theta=5`, and
`critical_block_exhaustive_pct=75`.

Evidence:
`outputs/hudata20_awls_sdst_current_seed0_30s_rerun_20260703_1500/summary.json`

- Valid runs: `20/20`.
- Average makespan: `1013.55`.
- Average gap to UB/BKS: `6.88%`.
- Median gap to UB/BKS: `7.10%`.
- Max gap: `15.51%`.
- Within 2% of UB/BKS: `3/20`.
- Reached UB/BKS: `0/20`.

Per-instance rerun makespan and UB gap:

| Instance | Makespan | UB/BKS | Gap |
| --- | ---: | ---: | ---: |
| oddla01 | 729 | 721 | 1.11% |
| oddla02 | 787 | 737 | 6.78% |
| oddla03 | 678 | 652 | 3.99% |
| oddla04 | 701 | 673 | 4.16% |
| oddla05 | 621 | 602 | 3.16% |
| oddla06 | 1015 | 945 | 7.41% |
| oddla07 | 979 | 902 | 8.54% |
| oddla08 | 1023 | 940 | 8.83% |
| oddla09 | 1081 | 984 | 9.86% |
| oddla10 | 1024 | 953 | 7.45% |
| oddla11 | 1387 | 1232 | 12.58% |
| oddla12 | 1236 | 1070 | 15.51% |
| oddla13 | 1325 | 1172 | 13.05% |
| oddla14 | 1362 | 1234 | 10.37% |
| oddla15 | 1401 | 1258 | 11.37% |
| oddla16 | 1061 | 1007 | 5.36% |
| oddla17 | 871 | 851 | 2.35% |
| oddla18 | 1003 | 985 | 1.83% |
| oddla19 | 962 | 951 | 1.16% |
| oddla20 | 1025 | 997 | 2.81% |

This rerun is slightly better than the prior `7.19%` average-gap recheck, mostly
because `oddla14` improved from `1429` to `1362`.  The hard shape remains
`oddla11`--`oddla15`; worker promotion should still require repeat evidence or
clear hard-shape improvement, not a single favorable wall-clock run.

## 2026-07-03 Full-Set Recheck

The current solver line was rerun on all 20 HUdata instances after adding the
portfolio warm-start no-op guard.  The solver code itself was unchanged; this is
fresh current-line evidence under the same seed-0, 30s-per-instance fixed-time
contract.

Evidence:
`outputs/hudata20_awls_sdst_current_seed0_30s_20260703_1850/summary.json`

- Valid runs: `20/20`.
- Average makespan: `1016.90`.
- Average gap to UB/BKS: `7.16%`.
- Median gap to UB/BKS: `7.10%`.
- Max gap: `15.80%`.
- Within 2% of UB/BKS: `3/20`.
- Reached UB/BKS: `0/20`.

Per-instance recheck makespan and UB gap:

| Instance | Makespan | UB/BKS | Gap |
| --- | ---: | ---: | ---: |
| oddla01 | 729 | 721 | 1.11% |
| oddla02 | 787 | 737 | 6.78% |
| oddla03 | 678 | 652 | 3.99% |
| oddla04 | 701 | 673 | 4.16% |
| oddla05 | 621 | 602 | 3.16% |
| oddla06 | 1015 | 945 | 7.41% |
| oddla07 | 979 | 902 | 8.54% |
| oddla08 | 1023 | 940 | 8.83% |
| oddla09 | 1081 | 984 | 9.86% |
| oddla10 | 1024 | 953 | 7.45% |
| oddla11 | 1387 | 1232 | 12.58% |
| oddla12 | 1236 | 1070 | 15.51% |
| oddla13 | 1325 | 1172 | 13.05% |
| oddla14 | 1429 | 1234 | 15.80% |
| oddla15 | 1401 | 1258 | 11.37% |
| oddla16 | 1061 | 1007 | 5.36% |
| oddla17 | 871 | 851 | 2.35% |
| oddla18 | 1003 | 985 | 1.83% |
| oddla19 | 962 | 951 | 1.16% |
| oddla20 | 1025 | 997 | 2.81% |

This recheck landed close to the earlier `7.19%` run and worse than the more
favorable `6.88%` rerun because `oddla14` returned to `1429`.  Treat
`oddla14` as a noisy but important promotion gate; hard-shape improvements
should be repeat-checked across `oddla11`--`oddla15` before claiming progress.

## 2026-07-03 Current-Line 20-Instance Run

The current solver line was rerun on all 20 HUdata instances with the same
seed-0, 30s-per-instance fixed-time contract.  The solver line was not changed
for this measurement; it is fresh current-line evidence.

Evidence:
`outputs/hudata20_awls_sdst_current_seed0_30s_20260703_2030/summary.json`

- Valid runs: `20/20`.
- Average makespan: `1013.25`.
- Average gap to UB/BKS: `6.85%`.
- Median gap to UB/BKS: `7.10%`.
- Max gap: `15.51%`.
- Within 2% of UB/BKS: `3/20`.
- Reached UB/BKS: `0/20`.

Per-instance makespan and UB gap:

| Instance | Makespan | UB/BKS | Gap |
| --- | ---: | ---: | ---: |
| oddla01 | 729 | 721 | 1.11% |
| oddla02 | 787 | 737 | 6.78% |
| oddla03 | 678 | 652 | 3.99% |
| oddla04 | 701 | 673 | 4.16% |
| oddla05 | 621 | 602 | 3.16% |
| oddla06 | 1015 | 945 | 7.41% |
| oddla07 | 979 | 902 | 8.54% |
| oddla08 | 1023 | 940 | 8.83% |
| oddla09 | 1081 | 984 | 9.86% |
| oddla10 | 1024 | 953 | 7.45% |
| oddla11 | 1387 | 1232 | 12.58% |
| oddla12 | 1236 | 1070 | 15.51% |
| oddla13 | 1325 | 1172 | 13.05% |
| oddla14 | 1362 | 1234 | 10.37% |
| oddla15 | 1401 | 1258 | 11.37% |
| oddla16 | 1055 | 1007 | 4.77% |
| oddla17 | 871 | 851 | 2.35% |
| oddla18 | 1003 | 985 | 1.83% |
| oddla19 | 962 | 951 | 1.16% |
| oddla20 | 1025 | 997 | 2.81% |

This run again shows legal full-set behavior and current noise around
`oddla14`.  The hard promotion gate remains `oddla11`--`oddla15`; those five
instances should be treated as the main improvement target before claiming an
SDST solver-quality gain.

## 2026-07-03 Current-Line 20-Instance Recheck

The current solver line was rerun once more on the full HUdata set with the
same seed-0, 30s-per-instance fixed-time contract.  No solver code changed for
this measurement.

Evidence:
`outputs/hudata20_awls_sdst_current_seed0_30s_20260703_165547/summary.json`

- Valid runs: `20/20`.
- Average makespan: `1012.80`.
- Average gap to UB/BKS: `6.81%`.
- Median gap to UB/BKS: `7.10%`.
- Max gap: `14.67%`.
- Within 2% of UB/BKS: `3/20`.
- Reached UB/BKS: `0/20`.

Per-instance makespan and UB gap:

| Instance | Makespan | UB/BKS | Gap |
| --- | ---: | ---: | ---: |
| oddla01 | 729 | 721 | 1.11% |
| oddla02 | 787 | 737 | 6.78% |
| oddla03 | 678 | 652 | 3.99% |
| oddla04 | 701 | 673 | 4.16% |
| oddla05 | 621 | 602 | 3.16% |
| oddla06 | 1015 | 945 | 7.41% |
| oddla07 | 979 | 902 | 8.54% |
| oddla08 | 1023 | 940 | 8.83% |
| oddla09 | 1081 | 984 | 9.86% |
| oddla10 | 1024 | 953 | 7.45% |
| oddla11 | 1387 | 1232 | 12.58% |
| oddla12 | 1227 | 1070 | 14.67% |
| oddla13 | 1325 | 1172 | 13.05% |
| oddla14 | 1362 | 1234 | 10.37% |
| oddla15 | 1401 | 1258 | 11.37% |
| oddla16 | 1055 | 1007 | 4.77% |
| oddla17 | 871 | 851 | 2.35% |
| oddla18 | 1003 | 985 | 1.83% |
| oddla19 | 962 | 951 | 1.16% |
| oddla20 | 1025 | 997 | 2.81% |

This is the best of the recent current-line full-set rechecks by average gap,
mainly because `oddla12` improved to `1227` and `oddla14` landed at `1362`.
It is still a single seed-0 wall-clock run, so treat it as current capability
evidence rather than a promoted algorithmic improvement.  The full-set gap is
now clear enough for worker prompts: easy-ish cases are `oddla01`,
`oddla18`, `oddla19`, and `oddla20`; the main quality gap remains the
20-job/5-machine group `oddla11`--`oddla15`.

## Recommended Evaluation Ladder

Use small smoke runs first, then a targeted hard-shape probe:

1. Compile and unit tests for slot contract and AWLS alignment.
2. Short SDST legality smoke on `oddla20`.
3. Targeted HUdata quality probe on `oddla12`, `oddla14`, and `oddla20`.
4. Full `oddla01`--`oddla20` benchmark after any candidate improves the targeted
   hard-shape probe without invalid schedules.
