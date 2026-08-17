---
id: fjsp-jpc-tst-semantics-and-search
type: reference
title: 多特性 FJSP-JPC-TST 语义与联合搜索
tags: [fjsp, multi_feature, job_precedence, machine_transport, operation_setup, cp_sat]
status: active
---

# 问题来源

首个组合验收集采用 Zheng 与 Xie（2025）的 FJSP-JPC-TST：

- 论文：*Research on the Flexible Job Shop Scheduling Problem with Job Priorities Considering Transportation Time and Setup Time*。
- DOI：`10.3390/axioms14120914`。
- 公开数据：`https://gitee.com/zhengchuchu0807/fjsp-jpc-tst`，包含 T01-T12。

论文中的“priority”表示 BOM 形成的跨工件硬前置关系，不是本项目旧 `fjsp_priority` 的软目标。公开 T01 为 10 工件、4 机器、39 工序；准备时间为 `max(2, round(0.3 * processing_time))`，运输矩阵由公开规则生成。

# 联合解码不变量

对当前工序 `o` 及候选机器 `m`：

```text
job_ready = max(
  同工件前序完成 + 前序机器到 m 的运输,
  所有跨工件前置件完成 + 各自末机器到 m 的运输
)
machine_ready = 能容纳“准备 + 加工”的首个机器空隙
processing_start = max(job_ready, machine_ready + setup(o,m))
```

准备活动占用机器但不进入标准 schedule 记录，Evaluator 根据机器加工顺序反推其区间。运输只约束工件到达，不占机器；不同运输可并行，除非未来 IO 显式提供有限运输资源。

# 搜索重点

- 先拓扑处理跨工件 BOM，再在同一可行前沿内比较工序和机器。
- 换机同时改变加工时长、准备时间以及入边/出边运输，必须作为事务移动完整重解码。
- 关键路径应包含机器准备弧、工件运输弧和跨工件 BOM 弧。
- T01 规模较小且柔性有限，至少安排一个真实 CP-SAT lane；其他 lane 可使用耦合局部搜索和群体/模因，避免三个 lane 只做相似派工。

# 证据边界

论文公开仓库提供实例，但 T01 的 README 不完整；本项目的规范化 JSON 明确冻结了索引、运输矩阵和边方向。任何 benchmark 数值只用于报告，不进入 Worker 提示。
