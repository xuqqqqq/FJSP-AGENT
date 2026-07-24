# FJSP 基准范围

## 标准 FJSP

标准 FJSP 的回归范围使用 `FJSP-Instance-main/instance` 下的四个本地 family：

| Label | Local family | Meaning |
| --- | --- | --- |
| BA | `fjsp.barnes.*` | Barnes |
| BR | `fjsp.brandimarte.*` | Brandimarte |
| DP | `fjsp.dauzere.*` | Dauzere-Peres |
| HU | `fjsp.hurink.*` | Hurink |

当前本地包在这四个标签下共包含 313 个标准 FJSP 实例：BA 21 个、BR 10 个、DP 18 个、HU 264 个。

这个本地包的参考界记录在 `knowledge/benchmarks/standard_fjsp_bounds_LB_UB.csv` 中。界值来源于 FJSPLib metadata，以及 JSPLib 对 Hurink `sdata` 实例的经典 metadata；本地包把这些实例说明为与 JSP case 等价。当前固定 CSV 中有 8 个 HU `sdata-car*` 实例没有显式 LB/UB 行，对它们应报告为缺失界值，而不是静默映射到其他 Hurink 变体。

## FJSP-SDST

FJSP-SDST 的回归范围使用完整的 HUdata 数据集：

```text
oddla01.txt ... oddla20.txt
```

实例文件存放在 HUdata 包的 `instances` 目录下。已发布的 LB/UB 表是 `SDST_HUdata_bounds_LB_UB.csv`；论文中把这些实例记作 `la01` ... `la20`，而本地文件名是 `oddla01.txt` ... `oddla20.txt`。报告代码必须把它们视为别名。

## 报告规则

- 只要存在 bounds，就始终报告 `family_label`、`instance`、validity、makespan、LB、UB/BKS、gap to LB 和 gap to UB。
- 使用 makespan 作为 solver 目标和 promotion 分数。LB/UB 值只是诊断与对比参考，不是优化输入。
- 标准 FJSP 的 `HU` 与 SDST 的 `HUdata` 必须分开。
- 对缺失的 LB/UB 要明确标注；不要从其他变体推断界值。
- 要区分 smoke run 和 performance run。一次短时的全实例 smoke run 只能证明 parser/evaluator/report 覆盖到位，不能证明 solver 质量。
