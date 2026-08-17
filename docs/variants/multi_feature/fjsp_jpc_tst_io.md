# 多特性 FJSP-JPC-TST IO

## 输入

规范化实例为 `fjsp_jpc_tst_instance_v1` JSON：

- `machine_count`：机器数。
- `jobs[].operations[]`：每道工序的 `[machine_id, processing_time]` 候选列表，ID 为 0-based。
- `operation_setup_rule`：准备时间规则；T01 使用 `max(minimum, round(ratio * processing_time))`。
- `transport_times`：机器到机器的非负运输时间方阵，对角线为 0。
- `job_precedences`：`[predecessor_job, successor_job]` 硬前置边。

## 输出

使用 `standard_fjsp_schedule_v1`：

```json
{
  "format": "standard_fjsp_schedule_v1",
  "makespan": 0,
  "schedule": [
    {"job_id": 0, "op_id": 0, "machine_id": 0, "start": 0, "end": 1}
  ]
}
```

`start/end` 是加工区间，不包含准备活动。Evaluator 根据同机加工顺序反推准备区间并检查互斥。
