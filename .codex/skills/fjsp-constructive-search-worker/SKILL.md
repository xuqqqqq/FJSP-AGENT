---
name: fjsp-constructive-search-worker
description: 为受控 Coding Agent 实现 FJSP 多规则构造、空闲间隙插入、有限前瞻、Beam、多起点和结构去重。用于 Main 已选择 constructive_search 方法族时，要求根据获准知识卡和实例规模自主设计可执行搜索，而不是只写极小占位参数。
---

# FJSP Constructive Search Worker

先读取 WorkerAssignment 中与 `construction`、`constructive_search`、`beam_search`、`idle_gap`、`initialization`、`multi_start`、`load_balance` 或 `critical_dispatch` 相关的获准知识卡。把卡片原则映射到当前 solver 的真实数据结构和预算，不照抄固定常数。

设计 Beam、最早间隙或多规则入口时，可按需参考 [idle-critical-beam-template.md](references/idle-critical-beam-template.md)。它提炼自用户提供的高柔性构造求解器和项目蓝图，但不要求照搬结构、评分或宽度；Coding Agent 应结合现有源码、实例画像和预算选择等价或更合适的实现。

## 设计要求

- 同时决定 ready operation、机器和插入位置；高柔性实例不能只枚举工序而固定最短机器。
- 机器时间轴使用最早可行空闲间隙，必要时比较尾插与 gap insertion。
- 多规则入口必须在排序压力上互补，并用 assignment/order 指纹去重。
- Beam 状态至少记录足以继续合法构造的 job、machine 和部分排程信息；下界、排序和 profile key 不能丢失关键分配差异。
- 根据 operation 数、平均候选机器数、分支数、deadline 和实测层耗时确定 width/branch/入口数。若完整运行远低于预算，优先扩大有意义的状态覆盖，而不是保留占位宽度。
- 全程维护最佳完整可行解；部分状态、估计分或未解码路径不能覆盖 incumbent。

## 激活证据

在 assignment 允许时记录各入口 makespan、每层 expanded/retained/pruned、Beam winner、incumbent 路径存活、profile collision、规则耗时和机器 shortlist 分布。计数必须来自实际执行路径。

若同时获准局部搜索 Skill，把构造阶段输出定义为结构不同的可行入口池，由后续模块消费；不要在两个模块中重复构造同一批入口。
