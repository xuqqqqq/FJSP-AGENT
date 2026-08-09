# Release-Time FJSP 输入输出协议

## 1. 适用范围

本协议用于 Flexible Job-Shop Scheduling Problem with Release Times
（Release-Time FJSP）算例。

该问题的前半部分是标准 FJSP 工序候选描述；差异在于标准 FJSP 主体之后追加两行 release-time 数据，用于描述工件最早到达时间和机器最早可用时间。因此 worker 必须按本协议读取这两行尾部数据，再生成或修改 release-aware 求解逻辑，不能把尾部 release-time 数据当作普通 trailing token 忽略。

Release-Time FJSP 中：

- `r_j` 表示工件 `j` 的 release time，即工件最早可开始加工时间。
- `a_m` 表示机器 `m` 的 initial available time，即机器最早可加工时间。

所有 `r_j` 和 `a_m` 在求解前已经给定。本协议描述的是静态调度问题，不是动态在线到达问题。

## 2. 标准 FJSP 前缀

文件只包含整数，空格、制表符和换行都可作为分隔符。标准 FJSP 主体按 token 顺序解析；换行通常按工件分行，但解析器不应依赖固定列宽。

前三个整数为：

```text
job_count machine_count max_candidate_count
```

随后每个工件依次给出：

```text
operation_count candidate_count machine_id processing_time ...
```

含义如下：

- `job_count`：工件数量。
- `machine_count`：机器数量。
- `max_candidate_count`：任一工序最多可选机器数。
- `operation_count`：当前工件的工序数。
- `candidate_count`：当前工序可选机器数。
- `machine_id processing_time`：候选机器及其加工时间。

本协议对应的算例使用 0-based 机器编号，合法范围为：

```text
0 <= machine_id < machine_count
```

输出 JSON 中也必须使用 0-based `machine_id`。Processing time 为非负整数；若出现 `processing_time = 0`，该工序在对应机器上的加工区间长度也必须为 0。

## 3. Release-Time 尾部编码

标准 FJSP 主体读完后，剩余整数为 release-time 数据。该主体本身不包含 SDST setup matrix 或其他变种尾部；剩余整数必须正好解释为一个固定 2 行矩阵。

矩阵宽度为：

```text
matrix_width = max(job_count, machine_count)
```

因此尾部整数数量必须正好等于：

```text
2 * matrix_width
```

尾部按行解释：

```text
release_time_matrix[0]
release_time_matrix[1]
```

### 3.1 Job Release-Time Row

第 1 行表示工件释放时间：

```text
release_time_matrix[0][j] = r_j, 0 <= j < job_count
```

`r_j` 表示工件 `j` 在时间 `r_j` 之前不可开始加工。调度解必须满足：

```text
start(job j, operation 0) >= r_j
```

由于同一工件内部存在工序先后关系，若第一道工序满足 release time，后续工序也不会早于该工件 release time。

如果 `job_count < matrix_width`，第 1 行剩余位置必须填充 `-1`。

### 3.2 Machine Available-Time Row

第 2 行表示机器初始可用时间：

```text
release_time_matrix[1][m] = a_m, 0 <= m < machine_count
```

`a_m` 表示机器 `m` 在时间 `a_m` 之前不可加工任何工序。调度解必须满足：

```text
start(operation assigned to machine m) >= a_m
```

如果 `machine_count < matrix_width`，第 2 行剩余位置必须填充 `-1`。

因为不同实例中可能出现 `job_count > machine_count`、`job_count < machine_count` 或二者相等，worker 不能假设 padding 总在某一固定行出现。

### 3.3 示例

若文件第一行为：

```text
15 11 2
```

则：

```text
job_count = 15
machine_count = 11
matrix_width = 15
```

尾部两行可以是：

```text
0 91 0 21 15 38 0 76 0 0 100 49 22 4 0
37 16 0 0 19 0 0 22 7 0 44 -1 -1 -1 -1
```

解释方式：

- 第 1 行 15 个数分别是 15 个工件的 release time。
- 第 2 行前 11 个数分别是 11 台机器的 initial available time。
- 第 2 行后 4 个 `-1` 是 padding，不代表真实机器。

## 4. 输出格式

候选 solver 输出 JSON 调度解。第一版平台适配只要求输出加工操作区间，不显式输出等待区间或机器不可用区间。Evaluator 根据输入实例中的 release-time 尾部数据检查时间下界。

```json
{
  "format": "standard_fjsp_schedule_v1",
  "variant": "fjsp_release_time",
  "instance": "case_name",
  "strategy": "strategy_name",
  "makespan": 0,
  "release_time_policy": "checked_by_evaluator",
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

- `job_id`、`op_id`、`machine_id` 均使用 0-based 编号。
- `start` 和 `end` 是工序加工区间。
- `end - start` 必须等于该工序在所选机器上的 processing time。
- `makespan` 应等于所有加工操作 `end` 的最大值；evaluator 会以 `schedule` 内容重新计算指标。
- Solver 不需要在输出中重复 `r_j`、`a_m` 或等待区间；这些数据来自输入实例。

## 5. 合法性检查

Evaluator 必须检查：

- 每道工序恰好出现一次，不能遗漏或重复。
- 每道工序只能选择候选机器。
- 加工区间长度必须匹配该候选机器 processing time。
- 同一工件内部满足工序先后顺序。
- 同一机器上的加工区间不能重叠。
- 每个工件第一道工序必须满足：

```text
start(job j, operation 0) >= r_j
```

- 每台机器上的所有加工工序必须满足：

```text
start(operation assigned to machine m) >= a_m
```

- Release-time 矩阵中，真实工件和真实机器位置必须为非负整数，padding 位置必须为 `-1`。

## 6. Evaluator 输出

Evaluator 输出 JSON 指标文件，核心字段包括：

- `valid`：是否合法。
- `error_count`：约束错误数量。
- `errors`：错误列表。
- `metrics.makespan`：最大完工时间。
- `metrics.scheduled_operations`：已排工序数。
- `metrics.operation_count`：总工序数。
- `metrics.max_job_release_time`：最大工件释放时间。
- `metrics.max_machine_available_time`：最大机器初始可用时间。
- 若提供 best-known CSV，则额外输出 `metrics.best_known_makespan` 和 `metrics.gap_pct`。

## 7. 验收口径

只有 evaluator 判定 `valid=true` 的解才能被平台接受。候选 solver 或 OpenCode worker 不得通过修改 evaluator、输入解析器或输出协议来绕过 release-time 检查。
