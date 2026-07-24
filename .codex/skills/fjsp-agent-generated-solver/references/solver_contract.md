# 独立 FJSP 求解器契约

本参考面向依据需求文档和 IO 文档编写 solver 代码的 Coding Agent。它不是后端代码，也不能被复制成固定的平台算法。

## 必需的求解器形态

构建一个独立入口，并满足：

- 只解析 IO 文档描述的活动输入格式；
- 只输出声明过的解 schema；
- 不使用 evaluator 内部实现，也不导入 `harness_agent`；
- 除非契约另有说明，接受配置好的 `--input`、`--output` 和 `--seed` 接口；
- 为每道工序返回且只返回一条排程记录。

不要只提交内部 helper function。一个合格的自主生成 solver 方案必须包含 Core 会实际执行的脚本表面：

- 一个活动 parser，例如 `parse_instance(...)` 或等价实现，它要从实例文件中推导出所有 job、operation、候选机器、processing time 和活动变体数据；
- 一个 `main()` 或等价 CLI 路径，它读取 `--input`、写出 `--output`，并遵守配置的 seed；
- 一个符合声明格式的 JSON 输出，其中包含 `schedule` 数组以及必需的 `job_id`、`op_id`、`machine_id`、`start` 和 `end` 字段。

写入 `--output` 的字节内容必须是声明过的 JSON 对象。不要写出裸的 schedule 列表，例如 `json.dump(best_schedule, f)`，然后再声称满足 `declared_output_schema`；标准 evaluator 期望的是一个带有 `schedule` 数组的对象。

仅仅读文件还不够。如果一个方案只是调用 `read_text().split()` 或 `json.load(...)`，然后把 `op_info = {(0, 0): ...}`、固定的 `machine_sequences` 或固定的一工序排程硬编码进去，那就没有真正实现活动 IO parser。

## 标准 FJSP 的 packed-line 解析规则

对 Dauzere、DP、BA、BR、HU 这类标准 FJSP 文本实例，一条物理 job 行通常打包了该 job 的全部工序。Coding Agent 必须用 token cursor 解析该 job 行：

- 在 job 行开头只消费一次 `operation_count`；
- 对每道工序消费一次 `candidate_count`；
- 然后准确消费 `2 * candidate_count` 个 token，按 `(machine_id, processing_time)` 成对读取；
- 在同一条 job 行内推进工序 cursor，而不是推进文件行 cursor。

不要实现“每道工序读一条新物理行”的 parser。这种反模式可能可以编译，也能通过很浅的自检，但会在 Dauzere/DP 风格的 packed job line 实例上失败。`active_io_parser` 证据应明确指出那些消耗活动输入中全部 packed operation token 的 cursor 变量或循环。

## 标准 FJSP 的 machine-id 基准规则

公开的标准 FJSP benchmark 家族在机器编号上并不统一。有些文件使用 0-based machine id，有些使用 1-based machine id。Coding Agent 不能凭习惯在读取每个候选对时直接减 1。

建议采用下面的 parser 形态：

- 读取候选对时先收集所有原始 machine id；
- 在全部原始 id 解析完成后，仅当 `min(raw_ids) >= 0` 且 `max(raw_ids) < machine_count` 时设置 `machine_base = 0`；
- 仅当 `min(raw_ids) >= 1` 且 `max(raw_ids) <= machine_count` 时设置 `machine_base = 1`；
- 若两个条件都不成立，则抛出 parser error；
- 只在构建 decoder 和输出写入器使用的 eligible-machine map 时做一次标准化。

这样可以避免两个常见错误：在 0-based 数据上出现 `machine -1 out of range`，以及对已经标准化过的 1-based 数据再次标准化后出现 `machine 0 out of range`。

## 表示规则

选择一种 operation identity，并从头到尾保持一致：

- 对自主生成 solver，优先使用 `(job_id, op_id)`；
- 把 assigned machine 与 operation identity 分开存储；
- `machine_sequences` 存放 operation identity 列表，而不是把 schedule dict 与 id 混在一起；
- 一次性构建 `op_info[(job_id, op_id)]` 或等价结构，并在全路径保持相同 key 类型。

不要在同一条 decoder 路径里混用 global operation id、`(job_id, op_id)`、schedule 字典以及原始 parser offset。

## 构造式 baseline 标准

对标准 FJSP 和 FJSP-SDST，弱的逐 job 贪心还不够。第一个合法 baseline 通常应是 operation-level list scheduler：

- 对每个未完成 job 维护一个 ready next operation；
- 对每个 ready operation 评价其所有 eligible machine；
- 使用 job ready time、machine ready time、processing time 以及活动变体的时序效应；
- 对 SDST，还要把候选机器上前一个已排工序或 job 的 setup 计入；
- 使用带 seed 的 tie-break、随机 assignment、RCL 或 multi-start 探索不同 interleaving；
- 保留通过解码 makespan 找到的最佳完整合法排程。

随机化必须发生在 ready-operation/machine 候选打分之后。若只是先选一个 ready operation，再对其机器调用 `rng.choice(eligible)`，这不算 operation-level ready-list constructor，因为它没有在 job/machine readiness 下比较所有 ready operation 与 eligible machine。

这里描述的是方法形态，不是固定公式。打分应适配活动变体和目标。

在 solver 自检里，把这个构造器记为 `operation_level_ready_list_constructor`。证据应指出保存 ready operations 的数据结构、遍历 eligible machines 的循环，以及带 seed 的 tie-break、RCL、restart 或 multi-start 规则。不要把固定的逐 job 扫描冒充这一能力。

## 候选接受规则

自主生成 solver 可以有内部自检，但唯一的 promotion 权威仍是 Core evaluator 输出。在内部：

- 拒绝任何部分排程；
- 拒绝重复或缺失工序；
- 拒绝解析后实例中未列为 eligible 的机器分配；
- 对于 `end - start` 与所选 eligible machine 的 processing time 不一致的输出区间，要么拒绝，要么修复；
- 拒绝违反 precedence 或 machine non-overlap 的候选；
- 不要把空的失败解码当成 makespan `0` 评分；
- 试探 move 无法解码时保留 incumbent。

在解码 `assignment + machine_sequences` 时，不要按机器主序简单回放每条机器列表。应使用 progress loop：只有当某台机器的下一道工序其作业内前驱已排定时，才调度它；若没有工序能继续推进，就将该候选判为不可行。

## 结构化自检证据

返回结构化 `solver_contract_self_check` 时，每项已实现能力都必须引用提交代码里真实出现的函数、变量或 guard 符号。叙述字段也必须如此：

- `representation` 应指出代码实际使用的 operation key 以及 assignment/sequence 数据结构；
- `decoder` 应指出那个负责重建并拒绝完整候选的函数；
- `variant_handling` 应指出每个活动变体对应的时序、容量、calendar 或目标 guard；
- `runtime_bounds` 应指出迭代、restart、window 或 deadline 控制；
- `incumbent_preservation` 应指出保存 incumbent 的 best/current 变量或候选失败分支。

不要把这些字段用成没有源码锚点的策略散文。也不要定义“只为证据存在”的 helper：parser、decoder、schedule-builder 和 validation/self-check helper 都必须在写出解之前被可运行的 solver 流程真实调用。

## 运行时契约

solver 必须在 harness timeout 内稳妥结束：

- 接受 evaluator 命令传入的 `--time-limit-sec`，并据此生成一个绝对 deadline；不要硬编码 Core timeout；
- Core 会预留退出余量，但 solver 也必须足够早地停止候选生成，以完成 incumbent 校验与序列化；
- 为 restart、局部搜索迭代、候选窗口和 neighborhood 扫描设置上界；
- 不仅在外层搜索循环里检查 deadline，也要在每个嵌套的 operation、machine 和 insertion-position 循环里检查；
- 优先在关键或瓶颈子集上搜索，而不是做 all-pairs 扫描；
- 对 move 先在 clone/snapshot 上应用，只有完整解码并验证后才提交；失败 move 不能把当前状态留在部分突变状态；
- 用 visited set 或 operation count 为 predecessor/successor 遍历加界；
- 若 smoke evaluation 超时，即使思路看似合理，也应把该方法视为当前不可行。

## 演进规则

在改进轮中：

- 除非回路反馈明确指出它们是失败源，否则保留最近一次 promotion 的 parser、representation、constructive skeleton 和 legality repair；
- 每次只改一个有界规则或 operator；
- 只有在能降低 patch 风险时才新增 helper file，并保持 import 独立；
- 一旦已有 incumbent，就不要整体替换现有 solver。
