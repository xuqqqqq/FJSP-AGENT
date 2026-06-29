# FJSP-SDST Fattahi 输入输出协议

## 1. 输入格式

算例采用 Fattahi FJSP-SDST `.fjs` 文本格式。文件只包含整数，空格和换行都可作为分隔符。

文件前半部分与标准 FJSP 相同。前三个整数为：

```text
job_count machine_count max_candidate_count
```

随后每个工件依次给出：

```text
operation_count candidate_count machine_id processing_time ...
```

机器编号可以是 0-based 或 1-based，解析器会根据机器编号范围自动归一化为 0-based。

## 2. Setup 矩阵

标准 FJSP 工序候选部分结束后，文件尾部必须包含：

```text
machine_count * total_operation_count * total_operation_count
```

个整数，表示 sequence-dependent setup times。

矩阵解释方式：

- 将所有工序按输入顺序扁平化为全局工序编号：先 job 0 的所有工序，再 job 1 的所有工序，依此类推。
- setup 数据按机器分块。
- 每个机器分块包含 `total_operation_count` 行，每行 `total_operation_count` 个整数。
- `setup[m][i][j]` 表示在机器 `m` 上，全局工序 `i` 之后紧接全局工序 `j` 所需的准备时间。
- 每台机器的第一道加工工序之前不计 setup time。

## 3. 输出格式

第一版平台适配仍使用 JSON 调度解。候选 solver 只输出加工操作区间，不显式输出 setup interval。Evaluator 会根据同一机器上的加工操作顺序隐式重算 setup time。

```json
{
  "format": "standard_fjsp_schedule_v1",
  "variant": "fjsp_sdst",
  "instance": "Fattahi_setup_01",
  "strategy": "strategy_name",
  "makespan": 0,
  "setup_time_policy": "implicit_by_evaluator",
  "schedule": [
    {
      "job_id": 0,
      "op_id": 0,
      "machine_id": 0,
      "start": 0,
      "end": 25
    }
  ]
}
```

字段说明：

- `start` 和 `end` 是工序加工区间，不包含该工序之前的 setup 区间。
- 对同一机器上相邻两条记录 `A` 和 `B`，合法性要求 `B.start >= A.end + setup[machine][A][B]`。
- `makespan` 应等于所有加工操作 `end` 的最大值；evaluator 会以 schedule 内容重新计算指标。

## 4. Evaluator 输出

Evaluator 输出 JSON 指标文件，核心字段包括：

- `valid`：是否合法。
- `error_count`：约束错误数量。
- `errors`：错误列表。
- `metrics.makespan`：包含 setup 影响后的最大完工时间。
- `metrics.scheduled_operations`：已排工序数。
- `metrics.operation_count`：总工序数。
- `metrics.setup_time`：同机相邻操作触发的 setup time 总和。
- `metrics.setup_count`：触发非零 setup 的相邻操作对数量。
- 若提供 best-known CSV，则额外输出 `metrics.best_known_makespan` 和 `metrics.gap_pct`。

## 5. 验收口径

只有 evaluator 判定 `valid=true` 的解才能被平台接受。候选 solver 或 OpenCode worker 不得通过修改 evaluator、输入解析器或输出协议来绕过 setup 检查。
