# 固定日历可重入 FJSP IO 协议

## 实例格式

实例使用 JSON `fjsp_calendar_reentrant_instance_v1`，文件名以 `.calendar_reentrant.json` 结尾。顶层必须包含：

- `active_features`：必须恰好为 `release_time`、`machine_initial_availability`、`machine_availability`、`reentrant_route`；
- `machine_count`：正整数；
- `machine_initial_availability`：长度等于机器数的非负整数数组；
- `unavailability_intervals`：`[machine_id,start,end]` 数组，采用半开区间 `[start,end)`；
- `jobs`：按 0-based `job_id` 排列。

每个 job 包含非负 `release_time`、至少三道 `operations`，以及一个 `reentrant_loop`。每道 operation 是若干 `[machine_id,duration]` 候选。回路满足 `0 < loop_start <= loop_end < original_op_count - 1` 且 `repeat >= 2`。

解析器先验证完整 JSON，再按 `pre + body * repeat + post` 展开。展开工序获得连续 0-based `op_id`，每次重复可独立选机。

## 输出与目标

输出沿用 `standard_fjsp_schedule_v1`。每个展开工序恰好一条 `job_id/op_id/machine_id/start/end` 记录。目标只有 `makespan`。

## 明确排除

本版本不解释 SDST、minimum lag、parallel batching、随机故障、可决策维修、能耗或软优先级。若引入这些语义，必须建立新 schema/version，不能向本 JSON 静默加字段。
