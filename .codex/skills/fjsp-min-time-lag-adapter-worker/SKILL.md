---
name: fjsp-min-time-lag-adapter-worker
description: 为受控 Coding Agent 把已选 FJSP 方法族适配到固定、相邻工序、machine-free 的 minimum time-lag 约束。仅在 runtime contract 明确激活 minimum_time_lag 且 Harness 授权本 Skill 时使用。
---

# FJSP 最小时间间隔适配执行器

## 触发条件

- runtime contract 已明确激活 `minimum_time_lag`。
- Harness 已授权本 Skill，且任务是在既定方法族上补入 min-lag 语义，而不是重新选择方法族。
- 活动 IO contract 的约束是固定四元组 `(job_id, k, k+1, L_min)`，语义为 `start(k+1) >= end(k) + L_min`。

若任务同时含 maximum lag、SDST、运输时间、有限等待缓冲或跨工件 lag，本 Skill 不足以单独覆盖，必须先取得对应变体合同。

## 读取顺序

1. 先加载 `fjsp-solver-foundation-worker` 和当前方法族 Skill。
2. 读取 Assignment `read_set` 中的需求与 IO 文档。
3. 读取 `knowledge/references/min_time_lag/min_time_lag_semantics_and_decoder.md`。
4. 需要构造、邻域或重启策略时，再读取 `knowledge/references/min_time_lag/min_time_lag_search_adaptation.md`。

## 执行步骤

1. **确认语义**：lag 约束 job，不占用机器；机器区间仍是 `[start, end)`，不能把 `processing_time + lag` 当作机器加工时长。
2. **接入状态**：解析尾部 `K + K*4`，保存每个受约束前驱的固定 lag；`L_min=0` 退化为普通 precedence。
3. **闭合时间传播**：job arc 使用 `processing_time + lag`，machine arc 使用 `processing_time`。任何构造、换机、交换、插入、变异或修复后，都必须由同一个 lag-aware 解码器得到完整排程。
4. **适配已选方法族**：
   - 构造搜索使用 `job_ready = predecessor_end + lag`，并让优先级或下界能看到剩余 lag 链；
   - 耦合局部搜索在 lag-aware 关键图上选择工序，候选可先估值，但最终接受必须完整重解码；
   - 精确混合为每条记录加入 finish-start 下界，并保持机器 `NoOverlap` 不包含 lag；
   - 群体/模因搜索必须让每个个体经过同一 lag-aware 解码和完整性检查。
5. **保留 incumbent**：不可解码、含正权环、不完整、非法或目标退化的候选不能覆盖当前合法 best。零时长/零 lag 形成的零权 SCC 可同刻执行，必须收缩或确定性规范化，不能误判为非法。
6. **提交证据**：报告解析约束数、正 lag 数、解码次数、拒绝的正权环/非法候选数、零权 SCC 数、lag-aware move 数，以及 Core 的 `min_time_lag_violations`。

## 实现底线

- 不允许先生成 lag-blind 排程，再只靠右移做最终修补；修补可作兜底，不能代替决策时的 lag-awareness。
- 不允许把 lag 膨胀进加工时长，因为这会错误占用机器。
- 不允许复用标准 FJSP 的旧 delta 后直接接受 move；局部估值只能用于筛选。
- 不允许从 Harness parser/evaluator 导入实现；求解器必须在允许路径内独立解析和生成解。
- 不把论文中的跨工件 generic lag、机器相关移动 lag 或 maximum lag 偷渡进当前合同。

## 验证与停止条件

至少覆盖以下微型行为：

- `L_min=0` 与标准 precedence 等价；
- 正 lag 使后继恰好在 `pred_end + lag` 或更晚开始；
- lag 期间前驱机器可加工其他工序；
- 一次同机交换和一次换机后仍满足所有 lag；
- 正权依赖环被拒绝且 incumbent 保留，全零权 SCC 被合法处理；
- 输出恰好包含每道工序一次，Core 返回 `valid=true` 且 `min_time_lag_violations=0`。

只证明“最终输出合法”不足以声称适配完成。若构造决策、关键结构或候选接受仍是 lag-blind，停止质量主张并继续修补当前阶段。
