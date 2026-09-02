---
id: fjsp-calendar-reentrant-semantics-and-search
type: reference
title: 固定日历可重入 FJSP 的兼容语义与搜索
tags: [fjsp, multi_feature, release_time, machine_calendar, reentrant_route, grasp, simulated_annealing]
status: active
---

# 来源与兼容范围

Tamssaouet、Dauzere-Peres、Knopp、Bitar、Yugma（2022）研究了含释放日期、固定机器不可用期、可重入流、序列相关准备、最小时间间隔和并行组批的复杂 FJSP，DOI `10.1016/j.ejor.2021.03.069`。论文采用随机化构造、模拟退火和多线程独立重启，不以精确求解器作为主算法。

当前 `fjsp_calendar_reentrant` 只取与项目现有定义严格兼容的限制子集：

| 论文特征 | 项目语义 | 结论 |
| --- | --- | --- |
| job release date | 首工序 `start >= release_time[j]` | 一致 |
| fixed unavailability period | 非抢占工序不得与固定 `[start,end)` 相交 | 一致 |
| reentrant flow | 每个作业一个连续回路、固定重复次数、展开后每次访问独立选机 | 兼容限制 |
| recipe SDST | 当前 SDST 仅支持 operation-pair/job-pair matrix | 不纳入 v1 |
| recipe-dependent min lag | 当前 min-lag 仅相邻且 machine-free | 不纳入 v1 |
| size/recipe-capacity batching | 当前 PBPM 按工件族和成员数容量 | 不纳入 v1 |

# 联合时序

解析时先展开回路，之后所有构造和移动都使用展开后的稳定 `(job_id, op_id)`。候选工序最早开始时刻至少满足 job release、工件前驱完成和 machine initial availability；随后在机器日历中寻找可完整容纳加工时长且不与已排机器活动冲突的最早空隙。工序不可跨越停机窗口。

回路展开不是额外的循环约束。重复体中的每次访问都是独立工序，后续访问必须等待前一次访问按展开路线完成。

# 搜索建议

1. 用 GRASP 风格受限候选集构造多个合法起点，评分同时考虑最早完整日历空隙、剩余链长度、候选机 regret 和重复访问瓶颈压力。
2. 局部搜索或模拟退火联合修改机器分配与机器序列位置；每个移动必须事务式完整重解码，不能只平移单条记录。
3. 停滞时做独立重启并共享全局 incumbent。线程只负责搜索多样性，输出仍由固定 evaluator 验证。
4. 小规模可以安排一个有界 CP-SAT 验证或局部修复 lane，但不能让全部 lane 都退化为相似 CP 模型。

# 证据边界

论文的工业实例尚未确认公开。仓库 smoke 数据只用于 parser/evaluator 回归，不能报告为论文算例、BKS 或算法质量证据。
