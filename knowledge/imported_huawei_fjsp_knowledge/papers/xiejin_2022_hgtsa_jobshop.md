---
id: paper-xiejin-2022-hgtsa-jobshop
type: paper
title: 基于混合遗传禁忌搜索算法的作业车间调度方法
tags: [fjsp, jsp, dfjsp, hybrid-genetic-tabu-search, n8, k-insertion, critical-path, key-factory]
source: C:/Users/ASUS/Downloads/基于混合遗传禁忌搜索算法的作业车间调度方法_谢晋 (1).pdf
status: verified_local_text
---

## 方法摘要

该博士论文提出 HGTSA（Hybrid Genetic Tabu Search Algorithm）统一求解 JSP、FJSP、DJSP 和 DFJSP。其核心路线是：用遗传算法进行全局搜索，用路径重连交叉保持种群多样性，用关键路径/关键工厂变异跳出局部最优，再用禁忌搜索进行局部强化。

对 FJSP，论文提出：

1. 能同时表示机器分配与工序排列的编码方式。
2. 基于路径重连的交叉算子。
3. 两种基于关键路径的变异算子：关键路径连续工序交换、关键工序换机器。
4. N8 与 k-insertion 混合邻域结构。
5. HGTSA 在 BRdata、BCdata、DPdata 上取得强结果，并更新 5 个 FJSP 算例上界。

## 适用问题

1. 标准 FJSP makespan 最小化。
2. 分布式 FJSP / 跨厂调度的算法结构设计。
3. 需要强局部搜索的工业 FJSP 变体。

## 核心算法片段

### 编码

FJSP 染色体包含 `m` 条机器子串，每条子串表示对应机器上的工件加工顺序。由于工序可选机器，不同机器子串长度不同。该表示同时隐含机器选择和机器排序。

### 路径重连交叉

从父代 `xf` 到父代 `xm`，通过机器变换和同机器工序交换逐步靠近。路径上的中间解作为候选子代。为控制计算量，只抽取部分中间解，并对候选解做变异和小规模 TS。

### 关键路径变异

1. 随机交换关键路径上的一对连续工序。
2. 随机选择关键路径上的一道工序，改变其加工机器。

### 禁忌搜索

禁忌长度：

```text
L = 15 + n / m
```

FJSP 邻域由 N8 和 k-insertion 混合组成：

1. N8：调整关键工序在同一机器上的位置。
2. k-insertion：改变关键工序的加工机器，并插入到目标机器可行位置。

### 推荐参数

论文对 FJSP 给出的 DOE 推荐参数：

```text
PS = 30
Pc = 0.9
Pm = 0.1
smallIter = 1200
largeIter = 15000
alpha = sd / 5
beta = max(sd / 10, 2)
```

## 可迁移到本项目的点

### 标准 FJSP/Barnes

当前 Barnes smoke 相对 public best-known 平均 gap 为 12.61%。论文 HGTSA 在 BCdata 上可以达到很强结果，因此下一步应优先复现简化 HGTSA：

1. 显式机器序列表示。
2. 主动解码。
3. 关键路径提取。
4. 简化 N8 邻域。
5. 简化 k-insertion 邻域。
6. 小预算 TS。
7. 再接 GA/path-relinking。

### 华为工业算例

不能直接套标准 FJSP move，但可以迁移思想：

1. 关键路径 -> 窗口产量临界链/尾部迟完链。
2. N8 -> 同设备关键块局部重排。
3. k-insertion -> 关键工序候选设备重分配。
4. 禁忌表 -> 避免局部修复反复撤销。
5. 每个 move 必须经过 qtime、setup、维修、转运、组批校验。

### LLM 自演进

这篇论文可作为自演进 prompt 的核心参考。LLM 不应只演化派工权重，而应能演化：

1. 编码层。
2. 交叉层。
3. 变异层。
4. 邻域层。
5. TS/GA 参数层。

## 风险与限制

1. 论文面向标准 makespan 目标，华为工业算例是窗口产量、setup、完整性等多目标。
2. HGTSA 参数较重，完整复现可能超过 5 分钟预算。
3. k-insertion 邻域很大，需要裁剪关键工序和候选插入点。
4. 对工业扩展约束，必须重解码或调用校验器，否则容易生成不可行解。

## 后续动作

1. 在 `standard_fjsp_benchmark.py` 外新增一个标准 FJSP local search demo。
2. 先实现关键路径提取和主动解码。
3. 再实现 N8/k-insertion 的小邻域版本。
4. 用 `Best.csv` 对比 Barnes gap 是否从 12.61% 显著下降。

