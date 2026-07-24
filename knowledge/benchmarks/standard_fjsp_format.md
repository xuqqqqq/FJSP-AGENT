# 标准 FJSP 文本格式

## 来源

- 格式说明：[Hexaly Flexible Job Shop Problem template](https://www.hexaly.com/templates/flexible-job-shop-problem-fjsp)
- 公开实例：[FJSPLib](https://scheduleopt.github.io/benchmarks/fjsplib)

## 格式概述

常见的标准 FJSP 文本格式以如下内容开头：

```text
number_of_jobs number_of_machines average_or_max_number_of_compatible_machines
```

随后每一行 job 包含：

```text
number_of_operations
for each operation:
  number_of_compatible_machines
  repeated machine_id processing_time pairs
```

## Token Cursor 解析规则

在 Dauzere/DP/BA/BR/HU 风格的标准 FJSP 文件中，一个物理 job 行会打包该 job 的所有 operations。该行先给出 `number_of_operations`，同一行剩余 token 再按顺序描述每一道 operation。

应使用 token cursor 解析，而不是假定每道 operation 对应一个物理输入行：

1. 读取头部 token：
   `number_of_jobs number_of_machines average_or_max_number_of_compatible_machines`。
2. 对每个后续的非空 job 行，在该行上创建一个 token cursor。
3. 消费 `operation_count`。
4. 重复 `operation_count` 次：
   先消费 `candidate_count`，再精确消费 `2 * candidate_count` 个 token，作为 `(machine_id, processing_time)` 对。
5. 校验 cursor 恰好停在当前 job 行边界；或者对整份文件使用一个全局 cursor，并校验所有声明的 operation 和 candidate pair 都已被消费。

不要假定 operation 0、operation 1 和 operation 2 出现在不同的物理行中。若 parser 在 operation 循环里递增文件行索引，那么即便它能处理 toy file，遇到这种打包 job 行实例也会失败。

machine 下标可能因数据集不同而采用 0-based 或 1-based。parser 应在内部完成归一化并校验取值范围。

## Parser 反模式

- 错误：读取某个 job 行的 operation count 后，执行 `for op_id in range(operation_count)`，并为每道 operation 读取 `lines[idx]`。
- 错误：根据剩余物理行数推导 `operation_count`。
- 正确：在当前 job 行内部把 `idx` 或 `pos` 作为 token cursor，逐步消费 candidate count 和 machine-duration pairs。
- 正确：解析结束后，检查生成的 `(job_id, op_id)` 记录数是否等于所有声明 operation count 之和。

## 对 FJSP Harness Agent 的影响

标准 FJSP adapter 必须：

- 解析 0-based 和 1-based 两种 machine id；
- 检查所有 operations 都被恰好调度一次；
- 检查所选机器确实属于合法 candidate；
- 检查 job precedence；
- 检查 machine non-overlap；
- 计算 makespan。
