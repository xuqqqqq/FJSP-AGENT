# 设备维修时段 FJSP 输入输出协议

## 1. 适用范围

本协议用于 Flexible Job-Shop Scheduling Problem with Machine Availability Constraints
（设备维修时段 FJSP）算例，也称为 FJSSP-nfa（FJSSP with Non-Fixed Availability）。

该问题的前半部分是标准 FJSP 工序候选描述；差异在于标准 FJSP 主体之后追加**机器不可用区间列表**，描述每台机器在哪些时段内因维修而不能加工。因此 worker 必须按本协议读取尾部不可用区间数据，在调度过程中不能将工序安排在这些时段内。

设备维修时段 FJSP 中：

- 每台机器有零个或多个不可用区间 `[start, end)`。
- 不可用区间是预先已知的（固定值），不代表随机故障。
- 工序在不可用区间内不可开始、不可继续、不可结束——工序要么完全在区间之前、要么完全在区间之后。

## 2. 标准 FJSP 前缀

与标准 FJSP 完全一致。前三个整数为：

```text
job_count machine_count max_candidate_count
```

随后每个工件依次给出：

```text
operation_count candidate_count machine_id processing_time ...
```

机器编号使用 0-based。Processing time 为非负整数。

## 3. 机器不可用区间尾部编码

### 3.1 整体结构

标准 FJSP 主体读完后，剩余数据为机器不可用区间列表。尾部的第一个整数为不可用区间总数 `K`（`K ≥ 0`），随后紧接 `K` 行，每行三个整数：

```text
K
machine_id start end
machine_id start end
...
```

### 3.2 字段约束

| 字段 | 约束 |
|------|------|
| `K` | `K ≥ 0` |
| `machine_id` | `0 ≤ machine_id < machine_count` |
| `start` | `start ≥ 0` |
| `end` | `start < end` |

不可用区间不保证互不重叠——如果同一台机器有两个重叠区间，取其并集效果相同（求解器只需保证工序不与每个区间冲突）。

### 3.3 约束语义

对每条记录 `(machine_id, start, end)`：被分配到该机器的任意工序 `[s, e)` 必须满足 `s >= end` 或 `e <= start`。

等价表述：机器的可用时间为 `[0, start₁) ∪ [end₁, start₂) ∪ [end₂, start₃) ∪ ... ∪ [end_K, +∞)`，只有在这段时间内该机器能加工。

### 3.4 示例

以 FFCR01 为例（5 工件，6 机器，7 个不可用区间）：

```text
5 6 3                              ← 标准头部（5 工件, 6 机器, 最大候选数 3）
...                                ← 标准 FJSP 主体（5 个工件）

7                                  ← K = 7 个不可用区间
0 0 35                             ← 机器0: 时段 [0, 35) 不可用
1 0 15                             ← 机器1: 时段 [0, 15) 不可用
2 0 6                              ← 机器2: 时段 [0, 6) 不可用
3 0 122                            ← 机器3: 时段 [0, 122) 不可用
3 227 253                          ← 机器3: 时段 [227, 253) 也不可用（第二段）
4 0 80                             ← 机器4: 时段 [0, 80) 不可用
5 0 318                            ← 机器5: 时段 [0, 318) 不可用
```

解读：

- 机器 0 在时刻 0~35 之间不可用（初期维修），35 之后才可加工。
- 机器 3 有两段维修：`[0, 122)` 和 `[227, 253)`。工序只能安排在 `[122, 227)` 或 `253` 之后。
- 未被列出的机器（本例中没有）表示全时段可用。

### 3.5 算例规模分布

本算例集共 20 个算例，规模从小到大：

| 算例 | Jobs | Mac | Ops | 不可用区间数 |
|------|------|-----|-----|:---:|
| FFCR01~04 | 5~7 | 6~7 | 15~21 | 7~16 |
| FFCR05~10 | 7~12 | 7~8 | 21~48 | 20~53 |
| FFCR11~20 | 10~20 | 4~15 | 55~240 | 7~97 |

## 4. 输出格式

候选 solver 输出 JSON 调度解。与标准 FJSP 完全一致，不显式输出维修时段。

```json
{
  "format": "standard_fjsp_schedule_v1",
  "variant": "fjsp_machine_availability",
  "instance": "case_name",
  "strategy": "strategy_name",
  "makespan": 0,
  "schedule": [
    {"job_id": 0, "op_id": 0, "machine_id": 0, "start": 0, "end": 25}
  ]
}
```

## 5. 合法性检查

Evaluator 在标准 FJSP 检查项之外增加：

- 对每条不可用区间 `(machine_id, start, end)`：被分配到该机器的工序的加工区间 `[s, e)` 必须满足 `s >= end` 或 `e <= start`。

## 6. Evaluator 输出

核心字段：`valid`、`error_count`、`errors`、`metrics.makespan`、`metrics.scheduled_operations`、`metrics.operation_count`、`metrics.machine_availability_violations`。

## 7. 验收口径

只有 evaluator 判定 `valid=true` 的解才能被平台接受。候选 solver 或 OpenCode worker 不得通过修改 evaluator、输入解析器或输出协议来绕过机器可用性检查。

## 8. 算例来源

本算例集的 20 个 FJSP with Availability Constraints 算例来自公开的 FJSP-FCR 基准数据集（`fjsp_fcr` 项目），由 JSON 格式转换为 FJSP-AGENT 兼容的 txt 格式（标准 FJSP 主体 + 不可用区间尾部）。

原始数据集的不可用区间从"简单单一维护窗口"逐步递增到"多窗口碎片化不可用"，模拟工业场景中机器维护从简单定期保养到复杂启停的梯度变化。
