# 工作负荷多目标 FJSP IO

## 输入

文件名包含 `.mofjsp.`，主体与标准 FJSP 完全一致：

```text
job_count machine_count max_candidate_count
op_count candidate_count machine_id processing_time ...
...
```

机器编号可为公开数据常见的 0-based 或 1-based，解析后统一为 0-based。标准主体后不得包含额外 token。

## 输出

输出继续使用 `standard_fjsp_schedule_v1`，并必须声明三个目标：

```json
{
  "format": "standard_fjsp_schedule_v1",
  "variant": "fjsp_multiobjective_workload",
  "makespan": 0,
  "max_machine_workload": 0,
  "total_workload": 0,
  "schedule": [
    {"job_id": 0, "op_id": 0, "machine_id": 0, "start": 0, "end": 1}
  ]
}
```

固定 evaluator 根据 `schedule` 和原始候选加工时长重算三个目标。缺少声明或声明值与重算值不一致时，解无效。
