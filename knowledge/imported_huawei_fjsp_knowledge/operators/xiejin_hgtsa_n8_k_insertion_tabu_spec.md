---
id: operator-xiejin-hgtsa-n8-k-insertion-tabu-spec
type: operator
title: 谢晋 HGTSA 的 N8/k-insertion 禁忌搜索实现要点
tags: [operator, fjsp, hgtsa, n7, n8, k-insertion, tabu-search, approximate-evaluation, critical-path]
source: knowledge/local_papers/raw/基于混合遗传禁忌搜索算法的作业车间调度方法_谢晋 (1).pdf
status: implementation_spec
---

## 作用

本卡片把谢晋博士论文中与标准 FJSP 求解器最相关的局部搜索细节转成可实现规范。Coding Agent 应结合当前 incumbent、需求与 IO 契约选择性实现，不依赖平台内置 solver。

## 论文定位

论文第 2.3.4 节给出 JSP 的 N8 邻域与邻域裁剪，第 3.3.4 节将 N8 与 k-insertion 组合用于 FJSP。

核心判断：

1. 标准 FJSP 的局部搜索不能只调整派工权重；必须在完整排程上操作析取图。
2. N8 负责同机器关键块内外的工序位置移动。
3. k-insertion 负责关键工序换机器并插入目标机器可行位置。
4. 禁忌搜索每一步选择未禁忌的最优邻域解，或满足特赦准则的解。
5. 近似/裁剪评价用于减少无效邻域，完整重解码只应留给候选短名单。

## N7 与 N8 的区别

N7 是 N6 的扩展，重点仍是关键块内部移动：移动关键块的块首、块尾或内部工序到关键块内部位置。

N8 在 N7 基础上继续扩展：

1. 若两个工序都在关键块内，按 N7 移动。
2. 若关键块内工序 `u` 与关键块后的同机器工序 `v` 满足可行条件，则可将 `u` 移至 `v` 之后。
3. 若关键块内工序 `v` 与关键块前的同机器工序 `u` 满足可行条件，则可将 `v` 移至 `u` 之前。

这意味着 N8 不只是相邻交换，也不是仅移动块首到块尾；它允许关键工序跨出关键块，搜索空间显著大于 N5/N6/N7。

## N8 裁剪规则

论文提出的裁剪思想是删除理论上不会降低 makespan 的移动：

1. 第一个关键块内，移动块首工序到内部工序之后不会降低 makespan。
2. 第一个关键块内，移动内部工序到块首之前不会降低 makespan。
3. 最后一个关键块内，移动块尾工序到内部工序之前不会降低 makespan。
4. 最后一个关键块内，移动内部工序到块尾之后不会降低 makespan。

工程实现时，可先用这些规则过滤 N8 候选，再用近似评价排序，最后对前若干个候选主动解码。

## k-insertion 邻域

k-insertion 用于 FJSP 的机器柔性：

1. 选择关键路径上的关键工序 `o`。
2. 将 `o` 从原机器 `m2` 移到候选机器 `m1`。
3. 在目标机器序列上选择两个相邻工序 `u` 和 `v`，把 `o` 插入 `u` 与 `v` 之间。

论文强调 k-insertion 的邻域规模远大于 N8，因此不能全量重解码所有插入点。应优先考虑：

1. 目标机器上与 `o` 当前开始/结束时间邻近的位置。
2. 目标机器关键块附近的位置。
3. 目标机器负载尾部和最早可插入位置。
4. 基于前向 `head` 与后向 `tail` 信息筛掉明显不可改善的位置。

## 禁忌对象

当前代码只禁 `(move_type, op, from_machine, to_machine)`，过于粗糙。更接近论文和经典 TS 的禁忌属性应按 move 类型区分：

1. N8 同机移动：禁忌该工序在同一机器上被立即移回原相邻关系，例如 `(op, machine, old_prev, old_next)`。
2. k-insertion 换机：禁忌关键工序短期内回到原机器，例如 `(op, previous_machine)` 或 `(op, previous_machine, previous_prev, previous_next)`。
3. 相邻交换：禁忌反向相邻弧，例如 `(machine, right_op, left_op)`。

特赦准则：若 tabu move 产生的新 makespan 优于全局最好解，允许破禁。

论文给出的禁忌长度参考：

```text
L = 15 + n / m
```

其中 `n` 为工件数，`m` 为机器数。工程实现可加入小幅随机扰动，避免周期循环。

## 近似评价/增量评价方向

当前 solver 对每个 move 都完整 `decode_state()`，成本高，导致无法评估足够多的 N8/k-insertion 候选。应增加两层评价：

1. 快速可行性过滤：用析取图前向 head、后向 tail、工序前后继和机器相邻关系判断是否可能成环或明显不改善。
2. 近似 makespan 排序：只估计被移动工序及其局部机器弧影响的最长路径下界/上界，将候选排序后只重解码 top-k。

推荐的候选评分字段：

```text
proxy_score =
    estimated_new_critical_length
    + machine_load_balance_penalty
    + insertion_idle_penalty
    + cycle_risk_penalty
```

`proxy_score` 只用于排序，不作为最终指标。最终接受仍必须基于主动解码后的真实 makespan 和 evaluator。

## 常见简化实现的差距

只有机器序列表示、主动解码和基础重插入的简化实现，通常仍有以下差距：

1. N8 只实现了关键块首尾扰动和随机同机重插入，没有完整“关键块内外移动”规则。
2. k-insertion 只按时间 pivot 和随机位置枚举，未使用论文中的插入点集合思想。
3. 禁忌属性偏粗，不能精确阻止反向弧或回到原机器。
4. 邻域候选全部完整解码，缺少近似评价和 top-k 解码机制。

## 推荐实现顺序

1. 在 decoded state 中计算 `head`、`tail`、关键块编号、每个工序的机器前驱/后继。
2. 实现 `generate_n8_neighbors()`，按 N8 规则枚举关键块内外移动，并加入裁剪规则。
3. 实现 `generate_k_insertion_neighbors()`，先枚举目标机器候选插入点，再用 proxy 选 top-k。
4. 重构 tabu key，使 N8、相邻交换、k-insertion 分别禁反向弧、回迁机器和原相邻关系。
5. 将新邻域作为 `--neighborhood-profile hgtsa-lite` 暴露给 agent，让 harness 与 `combined` 交叉评估。
