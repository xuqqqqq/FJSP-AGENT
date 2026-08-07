# LightRAG Generated Knowledge Card

- Problem family: fjsp_distributed_transfer
- Stage: worker
- Source: LightRAG
- Working dir: /home/wbw20/WorkSpace/Programs/FJSP_LLM_Demo/demo3/FJSP-AGENT/knowledge/fjsp_kb
- Query mode: mix
- Top k: 10
- LightRAG answer mode: llm-synthesized
- Response type: Actionable implementation knowledge card
- Cache key: 9f0193baf205bc5a
- Created at: 2026-08-05T03:30:35.735123+00:00
- Tags: initialization, decoder, dfjspt, distributed_decoder, distributed_fjsp, energy_consumption, factory_assignment, factory_machine_assignment

## Query

为 Coding Worker 编写独立 DFJSPT solver 提供实现依据。先检索以下主题的论文证据和实现经验：多起点、负载感知、工厂分配感知和可复现初解。；factory assignment、machine assignment 加机器序列的完整可行解码，并传播同工件转移时间。；Distributed Flexible Job-Shop Scheduling Problem with Transfers。；解码器在计算最早开工时间时同时考虑工序前驱、转移等待和工厂-机器容量。；存在多个工厂，每个工厂拥有独立机器集合，工序需同时决定工厂和机器。；加工能耗与转移能耗之和。；为每道工序选择加工工厂，并评估对转移时间、工厂负载和 makespan 的影响。；工厂编号与机器编号共同定义加工资源；机器编号在每个工厂内部独立。。然后输出一张可直接转化为代码的知识卡，覆盖 parser、factory-machine candidate representation、transfer-aware decoder、factory assignment、machine assignment、candidate repair、memetic/local-search delta、energy/workload metrics、workload balancing、evaluator-backed self-check，以及不要把 transfer time 误当 setup time 或 maintenance window 的规避规则。

## Retrieved Knowledge

基于检索到的论文证据和实现经验，以下知识卡可直接转化为独立 DFJSPT 求解器的代码结构，覆盖 parser、资源表示、转移感知解码、工厂/机器分配、候选修复、memetic 局部搜索增量评估、能耗/负载指标、负载均衡、自检及常见陷阱规避。

> **核心文献**  
> - [1] 提出 DFJSPT 模型与高效模因算法 EMA，定义了问题、目标（makespan、最大工厂负载、总能耗）并给出 40 个基准实例。  
> - [2] 详细描述了基于前向插入的解码方法，以及多种问题特定算子，可直接用于 transfer‑aware 解码和邻域设计。  
> - [4] 给出了传统 DFJSP（无转移）的 MILP 模型与约束，可作为代碼健壮性校验的参考。  
> - [5] 扩展至模糊加工/转移时间并强调 HDVMA 的初始化策略，验证了多起点初始化对收敛性的作用。  
> - [8] 从理论层面验证了转移与机器空闲时间的性质，为特征选择提供依据。

---

## 1. Parser（问题实例解析器）  
- 输入格式参照 [1] 的 Table 2：每个作业包含多道工序，每道工序列出候选工厂‑机器对及其“加工时间, 能耗”。  
- 同时读入全局参数：工厂间转移时间 `T_F`、厂内机器间转移时间 `T_M`、转移单位能耗 `E_U`。  
- 解析后存储为：`jobs[作业][工序] = {候选资源: (加工时间, 加工能耗)}`；资源用`(factory_id, machine_id)`标识，其中 `machine_id` 在每个工厂内部独立编号。  
- 支持从文件（如 JSON/CSV）加载，保证可复现。

## 2. Factory‑Machine Candidate Representation（工厂‑机器候选表示）  
- 加工资源由 `(工厂编号, 厂内机器编号)` 唯一确定 [1]。在编码中，机器分配向量 MAV 的每个元素直接指向一个具体工厂‑机器对，同时实现了工厂分配和机器分配。  
- 候选集合 `M_{ijf}` 表示作业 i 的工序 j 在工厂 f 内可用机器列表，解析时构建为字典。  
- 由于转移约束，某些工厂间可能禁止转移，因此候选集会动态调整：当上一道工序确定了工厂后，后续工序的可用工厂和机器将受限 [1] [2]。这部分在“候选修复”模块处理。

## 3. Transfer‑Aware Decoder（转移感知解码器）  
- 采用基于前向插入的主动调度解码 [2]：  
  1. 按作业序列向量（JSV）顺序取出工序 `O_{i,j}`。  
  2. 根据 MAV 确定分配的工厂‑机器，并定位该机器的已排程空闲时段。  
  3. 计算最早可开始时间 `EST` = max(前驱工序完成时间 + 转移时间, 机器空闲起始时间)。  
     - 若 `O_{i,j-1}` 与 `O_{i,j}` 在不同机器，则根据是否同工厂加入 `T_M` 或 `T_F`；若在同一机器，则转移时间为 0。  
  4. 若机器有空闲且 `EST + 加工时间 ≤ 空闲结束时间`，则插入该空闲；否则尝试后续空闲，或追加到机器末尾。  
  5. 更新机器可用时间和工序完成时间。  
- 该解码器同时传播同作业的转移等待，确保工序间依赖关系满足 [2] [1]。  
- 转移时间仅取决于物理移动，不包含调整、换产或维护窗口。

## 4. Factory Assignment & Machine Assignment（工厂与机器分配编码）  
- 采用三层编码方案 [1]：  
  - 作业调度向量（JSV）：所有工序的排列（含每个作业的虚拟起始/结束工序）。  
  - 机器分配向量（MAV）：每个工序分配的具体机器（隐含工厂，因为机器唯一隶属于某个工厂）。  
  - （若考虑 AGV 还需 AGV 分配向量，但纯 DFJSP‑T 可不包含）。  
- 初始化时，工厂分配和机器分配同时决定；在交叉/变异后，需校验 MAV 中每个基因的合法性（即该机器‑工厂组合是否属于当前工序的候选集）。

## 5. Candidate Repair（候选修复）  
- 当 MAV 基因指向的资源对当前工序不可用（例如前序工序在某工厂，而该工序不能再转移至另一工厂），则启动修复：  
  - 重新从动态合法候选集中随机选取一个工厂‑机器对，或使用 ECT 规则选择最早完成时间的机器 [2]。  
- 同时修复 JSV 中缺失或多余的工序出现次数，以保证染色体有效性。

## 6. Memetic / Local‑Search Delta Evaluation（模因/局部搜索的增量评估）  
- 局部搜索算子均基于关键路径 [1] [2]，仅重新评估受影响的工序，大幅减少计算量。  
- 常用算子：  
  - `JS-swap1`：交换关键路径上两工序的 JSV 位置（△ makespan）。  
  - `JS-insert1`：将关键作业插入另一位置。  
  - `MA1`：将关键机器上的工序重分配到另一候选机器（△ makespan, workload, energy）。  
  - 针对负载和能耗的算子如 `MA2` 选择瓶颈机器上的工序进行重分配。  
- 每次算子仅需局部解码，利用前置工序的最早完成时间、机器空闲段等缓存信息计算 delta。

## 7. Energy & Workload Metrics（能耗与负载指标）  
- **Makespan** `C_max`：最后一道工序的完成时间 [1]。  
- **最大工厂负载** `W_max`：各工厂内所有机器上工序加工时间之和的最大值 [1]，用于衡量负载均衡。  
- **总能耗** `TEC`：加工能耗之和 + 工厂间转移能耗 + 厂内机器间转移能耗 [1]。  
  - 加工能耗：`Σ (加工时间 × 单位能耗)`。  
  - 转移能耗：`E_U × (ZF×T_F + ZM×T_M)`，ZF/ZM 为是否需要转移的标志。  
- 评估时直接由解码后的时间表计算出三个目标值，用于 Pareto 比较。

## 8. Workload Balancing（负载均衡）  
- 通过最小化最大工厂负载目标自然实现均衡 [1]。  
- 在局部搜索中可额外引入奖惩机制：当重分配操作降低 `W_max` 时，即使 makespan 轻微增加也可能被前沿接受（取决于非支配排序）。  
- 初始化时可采用 GLR 方法 [1]（Global‑Local‑Random），其中 Local 阶段优先将工序分配给负载较轻的工厂，提升初始种群均衡性。

## 9. Evaluator‑Backed Self‑Check（评估器自检）  
- 构建与 [1] 一致的 40 个基准实例，或使用已知最佳解进行验证。  
- 实现一个自检模块：  
  1. 对给定调度方案重新计算所有工序起止时间，验证工序先后约束、机器容量约束和转移时间逻辑是否被破坏。  
  2. 检查所有标志 ZF/ZM 是否与实际的工厂/机器转移一致。  
  3. 对比评估器的 makespan、W_max、TEC 是否与算法内部记录一致，防止实现错误。  
- 评估器与解码器使用同一核心逻辑，但独立实现以保证双重校验。

## 10. 陷阱规避：切勿将转移时间误作 Setup Time 或 Maintenance Window  
- **转移时间** 仅当一道工序完成后、工件需物理移动到下一机器时发生，由固定的 `T_M` 或 `T_F` 给出，与工序类型、刀具切换无关。  
- 它不是 setup time（换产时间），不会因为作业切换而改变，也不依赖工艺路线。  
- 它不是 maintenance window（维护窗口），机器始终可用，只是工件运输造成空档。  
- 在解码和评估时，转移时间只加在前驱工序完成之后、后续工序开始之前，不改变机器可用性，也不产生额外的机器不可用时段。

---

### References

* [1] An efficient memetic algorithm for distributed flexible job shop scheduling problem with transfers
* [2] A_Feedback_Learning-Based_Memetic_Algorithm_for_Energy-Aware_Distributed_Flexible_Job-Shop_Scheduling_With_Transportation_Constraints
* [4] A_Hierarchical_Optimization_Algorithm_With_Dual-Cache_Synced_Tuning_Mechanism_for_Distributed_Flexible_Job_Shop_Scheduling_Problem
* [5] Hybrid Memetic Algorithm to Solve Multiobjective Distributed Fuzzy Flexible Job Shop Scheduling Problem with Transfer
* [8] Self-Adaptive_Differential_Evolution-Enhanced_Quality-Diversity_Optimization_for_Distributed_Flexible_Job-Shop_Scheduling_With_Transportation
