# 标准 FJSP 输入输出协议

## 1. 输入格式

算例采用常见的标准 FJSP 纯文本格式。第一行包含：

```text
job_count machine_count max_candidate_count
```

之后每个物理行描述一个工件。行首是该工件的 `operation_count`，随后在同一行中
依次描述每道工序：

```text
candidate_count machine_id processing_time ...
```

解析时必须使用 token cursor 消费一个工件行中的全部工序，不能假设每道工序单独占一行。
机器编号可能是 0-based 或 1-based；读取全部候选机器编号后判断基准，并且只归一化一次。

## 2. Solver 命令行

生成的 solver 必须支持：

```text
python examples/agent_generated_fjsp_solver.py \
  --input <instance> \
  --output <solution.json> \
  --seed <integer> \
  --time-limit-sec <seconds>
```

求解器必须从 `--input` 读取全部问题数据，不能导入 evaluator、`harness_agent` 后端，
也不能按实例名、BKS 或历史答案选择预制解。

## 3. 输出格式

`--output` 必须写入一个 JSON 对象，不能只写裸 schedule 列表：

```json
{
  "format": "standard_fjsp_schedule_v1",
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

必需顶层字段为 `format`、`makespan`、`schedule`。每条 schedule 记录必须包含
`job_id`、`op_id`、`machine_id`、`start`、`end`，所有 ID 均为 0-based 整数。

## 4. Core 验证

固定 Core 将独立检查：

- 每道工序恰好出现一次；
- 机器选择属于候选集合；
- `end - start` 等于所选机器上的加工时间；
- 工件 precedence；
- 机器区间无重叠；
- 输出 makespan 等于所有工序最大结束时间。

只有 Core 判定合法的结果才能参与 makespan 和 gap 比较。
