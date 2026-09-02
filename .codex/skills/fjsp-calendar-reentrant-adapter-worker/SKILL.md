---
name: fjsp-calendar-reentrant-adapter-worker
description: 为已选 FJSP 方法族联合适配静态释放、机器初始可用、固定停机日历和确定性连续回路展开。仅在运行时同时激活这些特征时使用。
---

# 固定日历可重入 FJSP 适配器

## 契约

- 只处理 `fjsp_calendar_reentrant_instance_v1` 明确激活的四项输入，不推断 SDST、time lag、batching、随机故障或维修决策。
- 解析后先按 `pre + body * repeat + post` 完整展开，搜索状态和输出都使用展开后的连续工序身份。
- 每个候选必须同时满足 job release、machine initial availability、固定 downtime、工件前置、候选机器、加工时长和机器互斥。
- 不可抢占工序必须完整落入可用空隙；不能通过延长加工时长或跨窗口暂停来绕过日历。

## 方法适配

1. 构造搜索使用日历感知 earliest-gap，并用受限候选集、多起点和重复访问瓶颈压力保持多样性。
2. 耦合局部搜索在换机、插入、交换后完整重解码 release/calendar/reentrant 联合时间图。
3. 群体/模因搜索对展开工序编码，交叉变异后必须修复每个工件的展开顺序，再做有界局部改进。
4. 精确混合只作为小实例验证或 incumbent 周围的局部修复；必须报告实际 Solve 状态和固定 evaluator 结果。

## 激活证据

- 原始与展开工序数、每个回路三元组；
- release 和 machine initial availability 的解析数量；
- downtime 数量及完整空隙放置调用；
- 至少一种非 CP 的实际搜索机制及候选/接受/改进计数。
