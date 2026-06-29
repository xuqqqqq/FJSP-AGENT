# FJSP-SDST 输入输出协议

## 1. 适用范围

本协议用于 Flexible Job-Shop Scheduling Problem with
Sequence-Dependent Setup Times（FJSP-SDST）算例。当前平台已经遇到两类本地数据集：

- Fattahi SDST：`Fattahi_setup_01.fjs` 等。
- SDST-HUdata：`oddla01.txt` 到 `oddla20.txt`，论文表中通常记作 `la01` 到 `la20`。

两类数据的前半部分都是标准 FJSP 工序候选描述；差异在于尾部 setup-time 矩阵的编码粒度不同。因此 worker 必须先按本协议识别输入变体，再生成或修改 setup-aware 求解逻辑，不能把尾部 setup 数据当作普通 trailing token 忽略。

## 2. 标准 FJSP 前缀

文件只包含整数，空格、制表符和换行都可作为分隔符。

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

机器编号可能是 0-based 或 1-based。解析器必须根据机器编号范围归一化为 0-based，输出 JSON 中也使用 0-based `machine_id`。

## 3. Setup-Time 尾部编码

标准 FJSP 前缀读完后，剩余整数为 setup-time 数据。平台按尾部长度识别以下两种编码。

### 3.1 Operation-Pair Matrix（Fattahi）

若尾部整数数量等于：

```text
machine_count * total_operation_count * total_operation_count
```

则按 Fattahi operation-pair matrix 解释。

解释方式：

- 将所有工序按输入顺序扁平化为全局工序编号：先 job 0 的全部工序，再 job 1 的全部工序，依此类推。
- setup 数据按机器分块。
- 每个机器分块包含 `total_operation_count` 行，每行 `total_operation_count` 个整数。
- `setup[m][i][j]` 表示在机器 `m` 上，全局工序 `i` 后紧接全局工序 `j` 所需准备时间。

### 3.2 Job-Pair Matrix（SDST-HUdata）

若尾部整数数量等于：

```text
machine_count * job_count * job_count
```

则按 HUdata job-pair matrix 解释。

解释方式：

- setup 数据按机器分块。
- 每个机器分块包含 `job_count` 行，每行 `job_count` 个整数。
- `setup[m][a][b]` 表示在机器 `m` 上，工件 `a` 的某道工序后紧接工件 `b` 的某道工序所需准备时间。
- 对同一工件内部连续操作，如果它们在同一机器上相邻加工，也按 `setup[m][job][job]` 计算，除非数据矩阵给出 0。

### 3.3 首件 Setup

每台机器的第一道加工工序之前不计 setup time。Setup time 只在同一机器的相邻加工工序之间触发。

## 4. 输出格式

候选 solver 输出 JSON 调度解。第一版平台适配只要求输出加工操作区间，不显式输出 setup interval。Evaluator 根据同一机器上的加工操作顺序隐式重算 setup time。

```json
{
  "format": "standard_fjsp_schedule_v1",
  "variant": "fjsp_sdst",
  "instance": "case_name",
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

- `job_id`、`op_id`、`machine_id` 均使用 0-based 编号。
- `start` 和 `end` 是工序加工区间，不包含该工序之前的 setup 区间。
- `end - start` 必须等于该工序在所选机器上的 processing time。
- `makespan` 应等于所有加工操作 `end` 的最大值；evaluator 会以 `schedule` 内容重新计算指标。

## 5. 合法性检查

Evaluator 必须检查：

- 每道工序恰好出现一次，不能遗漏或重复。
- 每道工序只能选择候选机器。
- 加工区间长度必须匹配该候选机器 processing time。
- 同一工件内部满足工序先后顺序。
- 同一机器上相邻两条加工记录 `A` 和 `B` 必须满足：

```text
B.start >= A.end + setup_time(machine=A.machine_id, previous=A, current=B)
```

其中 `setup_time` 根据第 3 节识别出的 setup 编码计算。

## 6. Evaluator 输出

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

## 7. 验收口径

只有 evaluator 判定 `valid=true` 的解才能被平台接受。候选 solver 或 OpenCode worker 不得通过修改 evaluator、输入解析器或输出协议来绕过 setup 检查。
