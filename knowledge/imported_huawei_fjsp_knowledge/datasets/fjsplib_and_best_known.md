---
id: dataset-fjsplib-best-known
type: dataset
title: FJSPLib 与 FJSP best-known 结果
tags: [fjsp, benchmark, best-known, barnes, brandimarte, hurink]
source: https://scheduleopt.github.io/benchmarks/fjsplib
status: verified
---

## 数据集定位

FJSPLib 是柔性作业车间调度问题的基准库，收集了多个来源的 FJSP 算例及当前已知较优结果。适合用于评估自演进框架是否只是在弱启发式基线上提升，还是能逐步接近公开 best-known。

本项目当前使用的 Barnes 系列来自本地 `qimingme/FJSP-Instance` 下载目录，并已用 `Best.csv` 中的 `Best` 列进行对齐比较。

## 当前 Barnes 对照结论

实验路径：

```text
outputs/standard_fjsp_barnes_smoke/
```

结果：

1. 21 个 Barnes 算例均可生成合法完整解。
2. 相对内置弱启发式基线平均提升 10.43%。
3. 相对公开 best-known 平均 gap 12.61%。
4. 最大 gap 17.97%，说明仅靠构造式派工评分远远不够。

## 对自演进框架的意义

1. 必须同时报告“相对自建基线”和“相对公开 best-known”。
2. 若只优化派工评分函数，容易出现对弱基线有效但离 best-known 仍很远。
3. Barnes 可作为标准 FJSP 快速回归集，用于测试新算子是否真正缩小 gap。

## 后续动作

1. 把 Brandimarte、Hurink、Dauzere-Pérès 等系列也接入同一评估脚本。
2. 对每个数据集保存 best-known 对照 CSV。
3. 引入强局部搜索后，优先观察 Barnes 平均 gap 是否从 12.61% 降到 5% 以内。

