# 标准 FJSP 输入输出协议

## 1. 输入格式

算例采用常见的 `.fjs` 文本格式。文件只包含整数，空格和换行都可作为分隔符。

第一行包含三个整数：

```text
job_count machine_count max_candidate_count
```

随后每个工件一行。每个工件先给出工序数 `operation_count`，然后依次给出每道工序的候选机器数量与机器加工时间对：

```text
operation_count candidate_count machine_id processing_time ...
```

机器编号可以是 0-based 或 1-based，解析器会根据机器编号范围自动归一化为 0-based。

## 2. 输出格式

调度解采用 JSON 格式，必须包含 `schedule` 数组。每条记录表示一道工序的机器选择和加工区间：

```json
{
  "format": "standard_fjsp_schedule_v1",
  "instance": "case_name",
  "strategy": "strategy_name",
  "makespan": 0,
  "schedule": [
    {
      "job_id": 0,
      "op_id": 0,
      "machine_id": 0,
      "start": 0,
      "end": 3
    }
  ]
}
```

## 3. 评价指标

Evaluator 输出 JSON 指标文件，核心字段包括：

- `valid`：是否合法。
- `error_count`：约束错误数量。
- `metrics.makespan`：最大完工时间。
- `metrics.scheduled_operations`：已排工序数。
- `metrics.operation_count`：总工序数。
- 若提供 best-known CSV，则额外输出 `metrics.best_known_makespan` 和 `metrics.gap_pct`。

## 4. 验收口径

只有 evaluator 判定 `valid=true` 的解才能参与质量比较。非法解不能因为 makespan 较小而被接受。
