---
name: fjsp-multiobjective-workload-adapter-worker
description: 为已选 FJSP 方法族适配 makespan、最大机器负荷和总负荷的严格词典序多目标搜索。
---

# 工作负荷多目标 FJSP 适配器

## 固定契约

- 输入是带 `.mofjsp` 标记的标准 FJSP 主体，不含额外尾部。
- 完整候选按 `(makespan, max_machine_workload, total_workload)` 严格词典序比较。
- 机器负荷等于分配到该机器的候选加工时长之和；不包含等待、空闲和隐式 setup。
- 输出保持 `standard_fjsp_schedule_v1`，并声明三个由完整排程重算的目标。
- 不修改 Core parser 或 evaluator。

## 搜索要求

1. 构造族同时保留结束时刻、最快加工、峰值负荷和后悔值导向的多个起点。
   对低柔性且 incumbent 明显来自 job-major 尾插的实例，至少一个起点必须从全局 ready-list
   重新选择下一道可执行工序，并比较 remaining-work/瓶颈压力与最早可行空闲间隙；不能只在原
   job-major 顺序上更换机器或调权。
2. 局部搜索族必须同时具有影响 makespan 的机器顺序算子，以及影响 workload 的换机重插算子。
3. 群体/模因族同时维护机器分配和机器顺序多样性，并按固定三元组选择 incumbent。
4. exact lane 采用三阶段优化或可证明不改变前序目标的等价标量化；构建模型但未调用求解、未取得可行状态或未导出排程均不算激活成功。
5. 无论内部使用何种代理评分，接受前都完整重解码并重算三个目标。
6. 报告机器负荷向量、三元组、实际运行的方法机制，以及新目标是否确实发生改善。

## 激活证据

- `constructive_search` 的 `candidates_evaluated` 只统计结构不同、完整合法且已重算三元组的排程。
- `coupled_local_search` 必须分别报告
  `diagnostics.activation.coupled_local_search.sequence_moves_evaluated > 0` 与
  `diagnostics.activation.coupled_local_search.machine_reassign_moves_evaluated > 0`；只反复评估换机、
  只打印通用 `moves_evaluated`，或关键块始终为空时，不得宣称耦合机制完整激活。

编辑前读取 `knowledge/references/multiobjective/workload_objectives_and_search.md` 和已分配的方法包。
