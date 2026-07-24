# FJSP-SDST Fattahi 实例

## 本地数据集

- 本地路径：`C:\Users\ASUS\Downloads\FJSP_SDST_Fattahi_instances\FJSP_SDST_Fattahi_instances`
- 文件：`Fattahi_setup_01.fjs` 到 `Fattahi_setup_20.fjs`
- 已检查示例：`Fattahi_setup_01.fjs`

## 来源说明

随包附带的 `README.txt` 说明，这些文件是来自以下来源的公开 FJSP 实例，且带有 sequence-dependent setup times：

- `ai-for-decision-making-tue/Job_Shop_Scheduling_Benchmark_Environments_and_Instances`
- 数据集路径：`data/fjsp_sdst/fattahi`
- 推荐引用：Reijnen, van Straaten, Bukhsh, and Zhang (2023),
  *Job Shop Scheduling Benchmark: Environments and Instances for Learning and
  Non-learning Methods*, arXiv:2308.12794.

## 格式观察

`Fattahi_setup_01.fjs` 以标准 FJSP 风格的头部和 job operation alternatives 开始：

```text
2 2 2
2 2 1 25 2 37 2 1 32 2 24
2 2 1 45 2 65 2 1 21 2 65
```

随后文件包含一个独立的 setup-time block。对这个微型实例来说，检查到的 block 有 8 行：

```text
6 3 4 4
3 6 4 4
3 3 7 4
4 4 4 8
6 3 4 3
3 6 3 3
3 3 6 4
3 4 3 6
```

## 对 Harness 的含义

这些文件必须被视为独立的 `fjsp_sdst` / `planned_fjsp_sdst` 变体，而不是当前的 `standard_fjsp`。sequence-dependent setup times 会改变 machine non-overlap 和 schedule-cost 的语义，因此需要：

- 能读取额外 setup-time matrix 的 parser；
- 基于每台机器前一道 operation 插入 setup time 的 evaluator；
- 明确说明 setup interval 是在 output 中显式给出，还是由 evaluator 重新计算的 solution contract；
- 为 setup-aware neighborhood move 和 insertion scoring 单独准备 Method Package 组件。

在该 adapter 存在之前，这个数据集只能作为 parser/evaluator 的设计目标和 smoke-test 语料。
