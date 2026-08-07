# LightRAG Generated Knowledge Card

- Problem family: fjsp_job_priority
- Stage: direction
- Source: LightRAG
- Working dir: /home/wbw20/WorkSpace/Programs/FJSP_LLM_Demo/demo3/FJSP-AGENT/knowledge/fjsp_kb
- Query mode: mix
- Top k: 10
- LightRAG answer mode: llm-synthesized
- Response type: Actionable implementation knowledge card
- Cache key: eb46360eb02e041c
- Created at: 2026-08-05T05:54:59.281362+00:00
- Tags: initialization, decoder, fjsp_priority, job_priority, lexicographic_objective, multi_objective, priority_completion_time, priority_jobs

## Query

针对 FJSP with Job Priority，先检索以下已选方向的论文证据、算法细节、伪代码和实现经验：多起点、负载感知、优先级工件感知和可复现初解。；assignment 加机器序列的完整可行解码，并记录每个 job 的完工时间。；Flexible Job Shop Scheduling Problem with Job Priority。；实例尾部给出 priority job ids，求解器需让这些工件整体尽早完工。；候选比较先看 makespan，再看 priority_completion_time。；同时优化 makespan 和 priority_completion_time。；所有 priority jobs 中最后完成的工件完工时间。；被尾部列表标记的优先级工件集合。。然后整理成一张可执行方法卡，必须说明适用场景、核心数据结构、priority_job_ids 表示、priority_completion_time 计算、构造式或局部搜索如何优先压缩 priority jobs 完工时间、候选比较规则，以及如何避免把 priority 当成硬 precedence 约束。

## Retrieved Knowledge

# 可执行方法卡：带工件优先级的柔性作业车间调度 (FJSP with Job Priority)

## 1. 适用场景
本方法适用于 **多目标柔性作业车间调度问题**，其中部分工件（作业）具有 **加工优先级**，但优先级 **不作为硬约束**（不强制要求高优先级工件必须在所有普通工件之前完成）。生产管理者期望在保证整体完工时间（makespan）的前提下，**让被标记的高优先级工件整体尽可能早地完工**。典型场景：半导体产线上的加急订单、设备总装中作为装配基准的关键部件等。

## 2. 核心数据结构

### 2.1 问题实例表示
| 字段 | 说明 |
|------|------|
| `jobs` | 作业列表，每个作业包含一个有序的工序序列 |
| `machines` | 机器列表，每道工序可从若干候选机器中选择一台加工 |
| `processing_time` | 三维表 `time[job][op][machine]` |
| `priority_job_ids` | 高优先级工件的 **索引/ID 集合**（见 2.2） |

### 2.2 `priority_job_ids` 表示
- **定义**：由问题实例尾部给出的整数列表或布尔掩码，标记哪些作业属于“优先级工件集合”（记作 **J′**）。
- **存储形式**：可以用 `std::set<int>` 或 `bool is_priority[job]` 数组。
- **不引入额外硬约束**，仅用于目标函数计算和启发式引导。

### 2.3 调度编码
采用 **两段式编码**（操作序列向量 + 机器分配向量）：
- **操作序列向量 (OS)**：按工序出现的顺序排列，每个工件的所有工序必须按工艺顺序出现。
- **机器分配向量 (MA)**：按工件的工序顺序，指明每道工序选择的机器。  
  配合 **活动调度解码（active schedule decoding）**，在机器空闲时隙中尽早插入作业，得到紧致时间表。

## 3. `priority_completion_time` 计算
对任意可行调度方案 $S$：
$$
\text{priority\_completion\_time}(S) = \max_{j \in J'} C_j
$$
其中 $C_j$ 为工件 $j$ 的完工时间（最后一工序完成时间）。该值 **完全由调度结果计算得到**，无需在模型中加入硬约束。

## 4. 优化目标与候选解比较规则

### 4.1 优化目标
- $f_1$：**makespan**（最大完工时间）
- $f_2$：**priority_completion_time**（优先级工件的最大完工时间）

### 4.2 候选解的比较规则（分层比较）
比较两个调度方案 $A$ 和 $B$ 时，采用 **字典序优先**：
1. **先看 makespan**：若 $makespan(A) < makespan(B)$，则 $A$ 优于 $B$；
2. 若 makespan 相等，**再看 priority_completion_time**：较小的方案更优；
3. 若二者均相等，视为等价（可再考虑总延迟、能耗等次要指标，但非必须）。

多目标优化算法（如 NSGA‑II、MOEA/D）可沿用此规则进行帕累托排序或构建奖励函数。

## 5. 构造式初解生成（多起点、负载感知、优先级工件感知）

### 5.1 多起点与负载感知
- **随机初始化**：随机生成 OS 和 MA，保证工序顺序合法。
- **基于最早完工时间 (ECT) 的启发式**：依次安排工件，每次将当前工序插入到候选机器上 **最早可完成的空闲时段**。该过程隐式考虑了机器当前负载（负载越轻则空闲段越早），有利于获得低 makespan 的初始解。
- **比例混合**：初始种群中 **50% 随机生成 + 50% ECT 启发式生成**，兼顾多样性和质量。

### 5.2 优先级工件感知的初解 (NCEM / Priority‑Aware OS)
借鉴 NCEM（新染色体编码方法）的思路：充分利用问题特征， **让高优先级作业的操作尽早调度**。
具体实现：
1. 生成操作序列时，按 **作业优先级降序**（高优先级在前）作为基本顺序；
2. 对每个优先级作业，随机将其所有工序（保持工艺顺序）穿插到序列的相对靠前位置；
3. 对普通作业，在剩余位置中随机排列其工序。  
这样可以保证初始解中高优先级作业的完工时间已经被明显压缩，加速后续迭代。

本方法对应知识库中记载的 **NCEM 充分利用 FJSPJP 特征，将优先作业尽早安排加工**，以及 **算法 getOS 根据作业优先级生成操作序列**。

## 6. 局部搜索优先压缩 priority jobs 完工时间
提出一种 **问题导向的局部搜索 (LSA)**，专门针对 `priority_completion_time` 进行优化。

### 6.1 搜索邻域
对当前解中 **priority_completion_time 决定路径上的工序** 执行：
- **机器重分配**：将决定 priority_completion_time 的关键工序切换到可用机器中加工速度更快（或当前负载更低）的选项。
- **操作前移**：在关键工序所属机器的已排序列中，通过与之前的普通工序交换位置或插入空闲间隙，使该关键工序提前执行。
- **工件内部顺序保持**：移动时仍然遵守工艺顺序，不破坏可行性。

### 6.2 选择策略
每次产生若干邻居解，根据第 4.2 节的分层比较规则择优替换当前解；也可结合模拟退火等策略避免早熟。

上述操作可在 **MA（文化基因算法）** 或 **IWOA（改进鲸鱼优化算法）** 等全局算法的局部搜索阶段嵌入，显著提升对 priority_completion_time 的优化效率。

## 7. 关键避坑：**不要把 priority 当成硬 precedence 约束**
> 许多文献中“工件优先级约束”指基于 BOM 的严格前后序关系（硬约束）。  
> 本文中的 **Job Priority** 是 **软优先级**，仅通过目标函数引导，调度中不可强制高优先级作业必须在普通作业之前加工。

**避免做法：**
- ❌ 不增设“优先级作业的工序必须排在普通作业之前”的硬性约束；
- ❌ 不通过修改工序顺序合法性（如强制前移所有工序）来满足优先级；
- ❌ 不在解码或修复机制中直接拒绝未提前加工优先级作业的解。

**正确做法：**
- ✅ 约束仅包含传统 FJSP 的工艺顺序和机器容量限制；
- ✅ 优先级只作为目标函数 $f_2$，允许优先级作业“插队”但不强制；
- ✅ 初解启发性地向前安排优先级作业，但最终调度仍由优化过程决定。

这样既保持了模型可行域不变，又能通过算法自动找到 makespan 与 priority_completion_time 的最佳折衷。

---

## 8. 算法实现骨架（伪代码）

```
Algorithm: Multi-Start MA for FJSP‑JP
输入: problem data, priority_job_ids
初始化:
  pop = []
  for i in 1..N/2:
     pop.append( RandomInit() )
  for i in 1..N/2:
     pop.append( ECTHeuristic() )
  for each individual in pop:
     individual.OS = PriorityAwareOS(individual.OS, priority_job_ids)   // NCEM思想
  ParetoArchive = non_dominated(pop, rule: min makespan, then min priority_completion_time)

主循环 (直至终止条件):
  选择父代, 交叉变异 -> offspring
  解码并评估 offspring
  对 offspring 中随机选取的个体执行 LSA (聚焦 priority jobs 的完工时间压缩)
  更新种群和 ParetoArchive (使用分层比较规则)

输出: ParetoArchive 中依据比较规则选出的最优解
```

解码子程序（`Decode(OS, MA)`）返回每个作业的 `completion_times`，进而计算 `makespan` 和 `priority_completion_time`，不施加额外约束。

### References

- [1] A memetic algorithm for the flexible job shop scheduling problem with job priority-f303156f.pdf
- [2] Multi-objective optimization for distributed flexible job shop scheduling problem with job priority-91df90f1.pdf
- [3] A_Feedback_Learning-Based_Memetic_Algorithm_for_Energy-Aware_Distributed_Flexible_Job-Shop_Scheduling_With_Transportation_Constraints-32f047d1.pdf
- [4] Research on the Flexible Job Shop Scheduling Problem with Job Priorities Considering Transportation Time and Setup Time-efb19039.pdf
- [5] Flexible job shop scheduling problem for parallel batch processing machine with compatible job families-8085d806.pdf
