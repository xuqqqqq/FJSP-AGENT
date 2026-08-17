---
name: fjsp-priority-adapter-worker
description: 为已选 FJSP 方法族适配软工件优先级和严格词典序双目标。
---

# 作业优先级 FJSP 适配器

## 契约

- 完整解析标准 FJSP 主体后，再读取恰好 `K` 个严格递增、互不重复的 0-based 优先作业 ID，其中 `K = ceil(job_count / 4)`。
- 优先级只改变目标排序，不引入 precedence、release-time、due-date 或机器容量约束。
- 完整解必须按 `(makespan, priority_completion_time)` 做词典序比较。绝不能用更差的 `makespan` 交换更早的优先作业完工时间。
- 每个候选在接受前，都必须基于完整重解码结果重算两个目标；不要相信缓存增量或求解器自报指标。
- 保持固定的 CLI 和 `standard_fjsp_schedule_v1`；不要修改 parser 或 evaluator Core。

## 搜索适配

1. 构造阶段可以更早排列已就绪的优先工序，但必须同时保留多种优先压力和常规的 `makespan` 导向起点。
2. 局部搜索应覆盖优先作业的关键或近关键工序、阻塞它们的机器弧，以及能在不恶化 `makespan` 的前提下降低其最终完工时间的分配与插入 move。
3. 群体或模因搜索应按词典序给个体排序，并保留结构多样性；加权和不是等价替代。
4. CP-SAT 应先最小化 `makespan`。得到受保护的 `makespan` 值后，再将其固定并最小化优先作业的最晚完工时间。只有在能证明系数大于所有可能次级目标范围时，才允许使用单一标量目标。
5. 报告以下激活证据：解析得到的优先 ID、两个重算目标值，以及至少一个 `makespan` 相同但优先完工时间发生变化的比较案例。
6. 只有在最终 incumbent 接受决策完成后，才能构造输出 JSON 负载。若精确阶段或搜索阶段替换了 `schedule`，则在序列化前必须同步刷新 `schedule`、`makespan` 和 `priority_completion_time`；标记为 `accepted=true` 的诊断信息必须与固定 evaluator 对该序列化排程的指标一致。

编辑前先阅读优先级语义卡、搜索卡以及已分配的方法包。
