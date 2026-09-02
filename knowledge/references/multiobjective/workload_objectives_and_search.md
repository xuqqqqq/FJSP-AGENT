---
id: fjsp-multiobjective-workload-search
type: reference
title: 工作负荷多目标 FJSP 的语义与搜索
tags: [fjsp_multiobjective_workload, multiobjective_workload, max_machine_workload, total_workload, workload_balancing_search]
status: active
---

# 目标语义

三个目标分别控制排程长度、最拥挤机器的加工负荷和机器选择产生的总加工负荷。`total_workload` 在柔性工序的候选加工时长不同时才有优化空间；若所有候选时长相同，它是常数，不应宣称算法改善了该目标。

当前 Core 使用严格词典序 `(makespan, max_machine_workload, total_workload)`。搜索内部可以用归一化标量做代理评分，但 incumbent 接受和最终导出必须按完整三元组重算。

# 方法族适配

## 构造搜索

- 保留最早完工、最短加工、最小当前机器负荷、负荷后悔值等互补起点。
- 候选机器评分应同时观察预计结束时刻、选择后的机器负荷峰值和加工时长；单纯选最快机容易形成瓶颈，单纯均衡负荷又可能恶化 makespan。
- 使用空闲间隙插入时，workload 只由机器选择改变，不因插入位置改变。
- 对低柔性大实例，若 incumbent 是 job-major 尾插构造，先用全局 ready-list 重建工序顺序：
  至少比较 remaining-work/瓶颈压力、最早完工和峰值负荷规则，并在机器时间轴上做最早可行
  gap insertion。只保持原 job-major 顺序并调整机器分配，通常无法解除机器顺序瓶颈。

## 耦合局部搜索

- 换机重插是同时改变三个目标的主要算子；同机交换或插入通常只改变 makespan。
- 先围绕关键路径和最大负荷机器生成候选，再加入少量非关键柔性工序，避免只在一个瓶颈附近循环。
- 对每个 move 完整重解码，并按三元组接受；不要使用只更新一个机器负荷的陈旧增量值。
- 激活证据必须区分 `sequence_moves_evaluated` 和 `machine_reassign_moves_evaluated`；二者都大于
  0 才能证明“顺序 + 换机”的耦合邻域真实执行。通用 move 总数不能替代这两个分项。

## 群体与模因搜索

- 编码应同时保留机器分配和工序顺序，多样性至少覆盖分配距离与机器序列差异。
- 适应度排序遵守固定词典序；可以保留辅助档案观察 trade-off，但不得用它绕过 Core 的晋升规则。
- 局部精修应交替使用关键路径顺序算子和负荷导向换机算子。

## 精确混合

- 完整 CP-SAT 可用 optional interval 表示候选机器，并从 presence 变量重建各机器 workload。
- 多阶段求解依次最小化 makespan、固定其最优/受保护值后最小化 max workload，再固定前两项最小化 total workload。
- 正常规模优先使用启发式 incumbent、关键工序池和分配信赖域；不要因为存在 exact lane 就取消其他方法族的有效竞争。

# 激活证据

报告重算后的三元组、机器负荷向量、实际发生的换机/顺序变化、各方法族的独立机制，以及 CP-SAT 是否真正调用并成功导出。只打印 OR-Tools 版本或构建模型不算精确求解成功。
