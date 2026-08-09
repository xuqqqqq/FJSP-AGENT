# 分布式可转移 FJSP 输入输出协议

## 1. 适用范围

本协议用于 Distributed Flexible Job-Shop Scheduling Problem with Transfers
（分布式可转移 FJSP，DFJSPT）算例。

该问题与标准 FJSP 的差异是：存在多个地理分布的工厂，机器在所有工厂间保持全局编号；同一工件的不同工序可分配到不同工厂，但不同工厂/机器之间的转移有时间成本。此外，每道工序的候选机器信息包含加工时间和单位能耗——用于多目标优化（makespan + 最大工厂负载 + 总能耗）。

算例来源于 DFM（Distributed Flexible Manufacturing）基准数据集，共 40 个算例（DFM01~40），分小/中/大三类规模。

## 2. 元数据头部

每个算例文件的前 5 行为元数据头部：

```text
The source of initial data:la0110x5
The number of jobs:10
The maximum number of factory:2
The maximum number of machines in each factory:6
The available number of machines for each operation in each factory:1:2
```

| 行 | 字段 | 含义 |
|----|------|------|
| 1 | `source of initial data` | 源算例标识（如 `la0110x5` = 源自 LA01，10 工件 × 5 机器） |
| 2 | `number of jobs` | 工件总数 `n` |
| 3 | `maximum number of factory` | 工厂总数 `F` |
| 4 | `maximum number of machines in each factory` | 每工厂最大机器数 |
| 5 | `available number of machines for each operation in each factory` | 每工序每工厂可选机器数范围（如 `1:2` 表示 1~2 台） |

## 3. 工件数据行

### 3.1 整体结构

元数据头部之后是 `n` 行工件数据，每行对应一个工件。每行的基本结构为：

```text
op_count  {candidate_count  [factory_id] machine_id processing_time unit_energy}×op_count
```

- `op_count`：该工件的工序总数。
- 对每道工序：先读 `candidate_count`（该工序在所有工厂中的可选机器总数），再读 `candidate_count` 组机器数据。

### 3.2 机器数据编码规则

每台候选机器的数据为 **3 或 4 个整数**：

| 情况 | 数据 | 长度 |
|------|------|:---:|
| **工厂切换**（该候选机器与前一候选机器在不同工厂） | `factory_id machine_id processing_time unit_energy` | 4 |
| **同工厂连续**（该候选机器与前一候选机器在**同一工厂**） | `machine_id processing_time unit_energy` | 3 |

关键规则：**同一工厂内连续列出多台候选机器时，后续机器省略 `factory_id`**。只有当工厂编号发生变化时，才重新写出 `factory_id`。

### 3.3 字段约束

| 字段 | 约束 |
|------|------|
| `op_count` | ≥ 1 |
| `candidate_count` | = 该工序在所有工厂的候选机器总数 |
| `factory_id` | `1 ≤ factory_id ≤ F`（1-based） |
| `machine_id` | 全局 1-based 编号；合法范围为 `1..F*M`，不能据其数值推断工厂 |
| `processing_time` | ≥ 0 |
| `unit_energy` | ≥ 0 |

### 3.4 转移时间

转移时间是求解时的**固定参数**，不写入算例文件：

| 转移类型 | 时间 | 单位能耗 |
|----------|:---:|:---:|
| 同一工厂内不同机器间（T_M） | 30 | 6 |
| 不同工厂间（T_F） | 60 | 6 |

判定顺序是硬约束：必须先比较 `factory_id`。只要工厂不同，转移时间就是 60，
即使两个候选的全局 `machine_id` 数值恰好相同也不能按“同机”处理。只有
`factory_id` 和 `machine_id` 都相同时转移时间才为 0。

### 3.5 解析示例

以 DFM01 的工件 0（10 工件，2 工厂）为例：

```text
5 2 1 5 20 11 2 6 19 16 3 1 3 46 4 4 46 5 2 8 43 9 ...
```

逐段解析：

```
5                                ← job0 有 5 道工序

# 工序 0：candidate_count=2
  2
  1 5 20 11                      ← factory=1, machine=5, time=20, energy=11
  2 6 19 16                      ← factory=2, machine=6, time=19, energy=16（工厂切换，写出 factory_id）

# 工序 1：candidate_count=3
  3
  1 3 46 4                       ← factory=1, machine=3, time=46, energy=4
  4 46 5                         ← 同工厂，省略 factory_id → factory=1, machine=4, time=46, energy=5
  2 8 43 9                       ← factory=2, machine=8, time=43, energy=9（工厂切换）

# 工序 2：candidate_count=4
  4
  1 1 83 10                      ← factory=1, machine=1, time=83, energy=10
  3 106 2                        ← 同工厂 → factory=1, machine=3, time=106, energy=2
  2 8 99 18                      ← factory=2, machine=8, time=99, energy=18
  9 97 11                        ← 同工厂 → factory=2, machine=9, time=97, energy=11

# 工序 3：candidate_count=3
  3
  1 4 57 1                       ← factory=1, machine=4, time=57, energy=1
  2 8 66 2                       ← factory=2, machine=8, time=66, energy=2
  11 58 5                        ← 同工厂 → factory=2, machine=11, time=58, energy=5

# 工序 4：candidate_count=3
  3
  1 1 31 11                      ← factory=1, machine=1, time=31, energy=11
  3 30 5                         ← 同工厂 → factory=1, machine=3, time=30, energy=5
  2 11 24 12                     ← factory=2, machine=11, time=24, energy=12
```

### 3.6 算例规模分布

| 类别 | 算例 | 工件数 | 工厂数 | 每工厂机器数 | 每工序候选范围 |
|------|------|:---:|:---:|:---:|:---:|
| 小规模 | DFM01~15 | 10~15 | 2~4 | 6 | 1~2 |
| 中规模 | DFM16~30 | 15~20 | 2~4 | 8 | 1~3 |
| 大规模 | DFM31~40 | 20~30 | 2~4 | 10 | 1~4 |

## 4. 输出格式

候选 solver 输出 JSON 调度解。新增工厂分配字段：

```json
{
  "format": "standard_fjsp_schedule_v1",
  "variant": "fjsp_distributed_transfer",
  "instance": "DFM01",
  "strategy": "strategy_name",
  "makespan": 0,
  "total_energy_consumption": 0,
  "max_factory_workload": 0,
  "schedule": [
    {"job_id": 0, "op_id": 0, "factory_id": 0, "machine_id": 0, "start": 0, "end": 25}
  ]
}
```

新增字段说明：

| 字段 | 含义 |
|------|------|
| `factory_id` | 工序分配的工厂编号（0-based） |
| `total_energy_consumption` | 总能耗（加工能耗 + 转移能耗） |
| `max_factory_workload` | 最大工厂负载 |

工厂编号和全局机器编号在输出中使用 0-based，分别将输入 `factory_id` 与 `machine_id` 减一。
机器编号不能在每个工厂内重新从 0 开始，也不能用机器编号区间猜测 factory；DFM 候选组中的
显式 factory marker 才定义资源所属工厂。

## 5. 合法性检查

Evaluator 在标准 FJSP 检查项之外增加：

- 每道工序的 `factory_id` 必须在合法范围内（0 ~ F-1）。
- 每道工序的 `machine_id` 必须是该工厂内该工序的合法候选机器。
- 加工时间 = 该工序在所选工厂和机器上的 processing time。
- 同一工序可能包含重复的 `(factory_id, machine_id)` 候选。Evaluator 保留全部候选，
  并用 `(factory_id, machine_id, 实际加工时长)` 匹配所选元组；若三个字段仍重复，
  按输入中最后一个精确匹配元组确定单位能耗。
- 前后工序在不同工厂时，开始时间 ≥ 前驱完成时间 + T_F（60）。
- 前后工序在同一工厂不同机器时，开始时间 ≥ 前驱完成时间 + T_M（30）。
- 前后工序在同一工厂同一机器时，开始时间 ≥ 前驱完成时间（标准 FJSP 约束，无额外转移）。
- 求解器自检返回任何错误时，该候选必须判失败，不得继续作为合法 incumbent 输出。

## 6. Evaluator 输出

核心字段：`valid`、`error_count`、`errors`、`metrics.makespan`、`metrics.max_factory_workload`、`metrics.total_energy_consumption`、`metrics.scheduled_operations`、`metrics.operation_count`。

## 7. 验收口径

只有 evaluator 判定 `valid=true` 的解才能被平台接受。候选 solver 或 OpenCode worker 不得通过修改 evaluator、输入解析器或输出协议来绕过工厂分配和转移时间检查。

## 8. 算例来源

本算例集的 40 个 DFM 算例来自 Luo et al. (2020) 的论文 *"An efficient memetic algorithm for distributed flexible job shop scheduling problem with transfers"*，源数据托管于 GitHub（`hnulq191817880/mygit2019`）。

算例由 Hurink 标准 FJSP 基准通过以下方式扩展得到：
1. 添加工厂维度（2~4 个工厂，每工厂 6~10 台机器）。
2. 机器在所有工厂间使用全局编号；工厂归属只由显式 factory marker 确定，不能由机器编号推断。
3. 每道工序的候选机器被分配到各工厂，每工厂 1~4 台候选。
4. 为每个机器-工序对附加单位能耗数据。
5. 固定转移时间参数：同厂 T_M=30，跨厂 T_F=60。
