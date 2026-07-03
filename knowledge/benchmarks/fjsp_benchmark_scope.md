# FJSP Benchmark Scope

## Standard FJSP

The standard FJSP regression scope uses four local families from
`FJSP-Instance-main/instance`:

| Label | Local family | Meaning |
| --- | --- | --- |
| BA | `fjsp.barnes.*` | Barnes |
| BR | `fjsp.brandimarte.*` | Brandimarte |
| DP | `fjsp.dauzere.*` | Dauzere-Peres |
| HU | `fjsp.hurink.*` | Hurink |

The current local package contains 313 standard FJSP instances across these
four labels: BA 21, BR 10, DP 18, HU 264.

Reference bounds for this local package are recorded in
`knowledge/benchmarks/standard_fjsp_bounds_LB_UB.csv`.  Bounds come from
FJSPLib metadata, plus JSPLib classical metadata for Hurink `sdata` instances
that the local package documents as equivalent to JSP cases.  Eight HU
`sdata-car*` instances currently have no explicit LB/UB row in the pinned CSV
and should be reported as missing bounds rather than silently mapped to another
Hurink variant.

## FJSP-SDST

The FJSP-SDST regression scope uses the full HUdata set:

```text
oddla01.txt ... oddla20.txt
```

The instance files are stored under the HUdata package `instances` directory.
The published LB/UB table is `SDST_HUdata_bounds_LB_UB.csv`; the paper names the
instances `la01` ... `la20`, while the local files are named `oddla01.txt` ...
`oddla20.txt`.  Reporting code must treat these as aliases.

## Reporting Rules

- Always report `family_label`, `instance`, validity, makespan, LB, UB/BKS,
  gap to LB, and gap to UB when bounds are available.
- Use makespan as the solver objective and promotion score.  LB/UB values are
  diagnostics and comparison references, not optimization inputs.
- Keep standard FJSP `HU` separate from SDST `HUdata`.
- Mark missing LB/UB explicitly; do not infer bounds from another variant.
- Distinguish smoke runs from performance runs.  A short all-instance smoke run
  proves parser/evaluator/report coverage, not solver quality.
