# LightRAG Generated Knowledge Card

- Problem family: fjsp_machine_availability
- Stage: direction
- Source: LightRAG
- Working dir: /home/wbw20/WorkSpace/Programs/FJSP_LLM_Demo/demo3/FJSP-AGENT/knowledge/fjsp_kb
- Query mode: mix
- Top k: 10
- LightRAG answer mode: llm-synthesized
- Response type: Actionable implementation knowledge card
- Cache key: fa51bdf9574407fd
- Created at: 2026-07-29T06:22:13.167806+00:00
- Tags: construction, calendar_aware_decoder, machine_availability, availability_aware_insertion

## Query

针对 FJSP with machine availability constraints，先检索以下已选方向的论文证据、算法细节、伪代码和实现经验：从零建立稳定合法 baseline 的构造规则与状态闭环。；解码器在计算最早开工时间时跳过机器不可用区间，并保持工序覆盖、候选机器和 precedence 合法。；机器存在预先给定的不可用维修区间，调度时工序不能跨越或落入不可用窗口。；在机器可用空隙中寻找最早可行插入位置，并避免跨越不可用区间。。然后整理成一张可执行方法卡，必须说明适用场景、核心数据结构、解码/插入/局部搜索步骤、move delta 如何保持维修窗口合法、如何评估 makespan、如何做失败规避。

## Retrieved Knowledge

# 可执行方法卡：带有机器不可用区间的柔性作业车间调度（FJSP‑MA）

## 适用场景
- **问题**：柔性作业车间调度问题（FJSP）中，每台机器存在预先给出的、不可跨越的维护时段（机器不可用窗口）。工序不能在不可用区间内加工，也不允许跨越该区间（`non‑crossable` 约束）。
- **典型假设**（来自 [3]）：
  - 工序不可抢占、不可恢复（被维护打断后需重头加工）。
  - 维护资源可以充足或仅有一组资源，但本卡默认采用“仅需避开不可用区间，多台机器可同时不可用”的场景。
  - 维护窗口可以是固定的（`fixed`，开始‑结束时间已定）或非固定的（`non‑fixed`，开始时间可在给定时间窗内滑动），本卡以**固定不可用区间**为主进行说明，非固定情形可通过在窗口内枚举或决策维护开始时间进行扩展。
- **优化目标**：最小化最大完工时间（makespan）。

## 核心数据结构

| 数据 | 说明 |
|------|------|
| Jobs `i = 1..n` | 每个工件 `i` 包含有序的工序序列 `j = 1..J_i` |
| Machines `k = 1..m` | 每道工序 `O_{ij}` 存在候选机器子集 `M_{ij} ⊆ {1..m}`，并有对应的加工时间 `p_{ijk}` |
| Unavailability intervals `U_{kr}` | 机器 `k` 的第 `r` 个不可用区间，由 `[su_{kr}, cu_{kr}]` 定义，不可跨越，且工序不得与之重叠 ([2]) |
| 解表示（类似 [2]） | **MS字符串**：长度为总工序数，第 `v` 个基因表示第 `v` 道工序（按工件顺序展开）所选的机器 `k ∈ M_{ij}`。<br>**OS字符串**：长度为总工序数，是由工件号重复 `J_i` 次组成的排列，从左到右扫描，第 `f` 次出现工件号表示该工件的第 `f` 道工序。 |
| 机器可用间隙链表 | 每台机器维护一个有序列表，记录当前已安排工序和不可用区间构成的空间时间片。解码时动态更新。 |

## 构造规则与基线生成（状态闭环）
1. **初始化**：按某种规则生成 OS 字符串（如随机优先序）和 MS 字符串（如随机选择候选机器或最短加工时间规则）。
2. **解码器**（见下节）将 (MS, OS) 转换为实际开始/完成时间。若解码成功，得到一个完整的可行调度，否则标记为不可行并重新生成或赋予极大惩罚值。
3. **局部搜索**：在后续邻域移动后，必须重新调用解码器以保持“机器不可用区间不被侵入”的硬约束，构成**闭环校验**。

## 解码器（左移插入，保持机器可用性）
遵循 [2] 中计算 makespan 时对不可用时段的处理思路，但调整为针对 `non‑crossable` 约束的左移插入算法：

```
输入： MS, OS, 不可用区间集合 U_{kr}（每一台机器的固定区间列表）
输出： s_{ijk}, c_{ijk}（工序开始与完成时间）、makespan

1. 将每台机器 k 的可用时间线初始化为 [-∞, +∞] 并扣除所有不可用区间，得到可用间隙列表 free_slots_k
   （初始只含一个始于0的无限间隙，然后被 U_{kr} 切割）
2. 按 OS 从左到右依次取出一道工序 O_{ij}（由工件i的第j道标识）：
   a. 从 MS 获取其所选机器 k
   b. 获取工件 i 的前驱工序的完成时间 prec_c （若无前驱则为0）
   c. 在机器 k 上寻找最早的可插入位置：
       遍历 free_slots_k 中的每个间隙 [start, end)，若满足：
          max(prec_c, start) + p_{ijk} ≤ end
       则令 s_{ijk} = max(prec_c, start)
       否则继续检查下一个间隙
   d. 若找到，更新：
        c_{ijk} = s_{ijk} + p_{ijk}
        将机器 k 的间隙 [s_{ijk}, c_{ijk}) 标记为已占用，更新 free_slots_k
   e. 若遍历所有间隙均无法容纳，则标记该解为不可行，中止解码并返回极大 makespan
3. 全部工序安排完成后，makespan = max_{i,j} c_{ijk}
```

**关键点**：
- 不可用区间在解码前就已转化为“不可通过的盲区”，间隙切割保证了工序不会落入或跨越不可用窗口。
- 工序的前驱约束通过 `prec_c` 保证，同时工序只在其候选机器上执行，合法性由 MS 限定。

## 局部搜索与 move delta 的维修窗口合法性保持
常用的邻域结构：
- **机器重分配**：随机改变一台工序的 MS 值（仅在其候选机器集内）。
- **工序顺序交换**：在 OS 中随机选取两个位置交换（需保证仍为可行排列）。
- **插入移动**：从 OS 中移除一道工序并插入到另一位置。

每次移动后，**必须重新运行解码器**，因为时间线会发生变动。为了快速评估，可先使用**快速左移解码**，一旦插入失败立即终止并记该解为不可行。

**Delta 评估与合法性检查**：
- 对于机器重分配移动，可在新机器上尝试插入（仅影响该机器及后续工序），若发现无足够间隙则移动非法。
- 对于 OS 移动，可使用修复性左移解码，但为提高效率可仅重调度受影响的机器上的工序，并结合约束传播检查是否违反不可用区间。

典型的合规写法（来自 [3] 的 FBS 思想）是：在分支扩展时，每步仅考虑那些能安排在机器可用间隙中的操作，从而天然规避非法状态。

## Makespan 评估
Makespan 即所有机器上最后一道工序的完成时间的最大值：
```
makespan = max( c_{ijk} )  ∀ i, j
```
在解码过程中维护各机器的最大完成时间，最终取全局最大值。对于不可行解，直接赋予一个足够大的惩罚值（如 `INF`），保证其不会优于任何可行解。

## 失败规避与鲁棒性措施
1. **构造阶段**：若初始随机解无法成功解码（即使尝试多次），可退回到简单规则（如空闲优先、最早完成优先）或使用文献 [3] 的过滤束搜索（FBS）构造初始可行解。FBS 的分支方案显式地考虑了机器可用性与维护资源约束，能保证生成可行调度。
2. **局部搜索过程**：
   - 只接受通过解码器验证的移动。
   - 若邻域内长期未能发现可行移动，可扩大随机扰动强度（如多道工序重排）或触发重启。
   - 维护一个历史最优可行解，避免因不可行解过多而丢失较好调度。
3. **解码器**：在寻找插入位置时，若当前机器无合法空隙，可尝试对该工序**重新分配机器**（如果允许）或激活**回溯**（如返回上一步尝试其他排列）。但在大规模邻域方案中，通常选择直接丢弃该解。

## 来自文献的核心依据
- [3] 提出的 FBS 启发式专门处理带维护活动的 FJSP，将机器可用性约束和维护资源约束集成到分支方案中，证明通过修改分支步骤可以经济且快速地获得可行调度。
- [2] 针对带可跨越（crossable）不可用区间的 FJSP 给出了 makespan 的计算规则：“if the starting of the operation is overlapping the unavailable interval, the starting must be delayed to the end of the unavailable period; if the starting of the unavailable period is overlapping the operation, the processing time must be increased”。本卡将该思想适配为 non‑crossable 的左移间隙搜索。
- [1] 提供了 FJSP‑nfa 的新基准，并证明 MIP 可求解小规模实例，为启发式算法的评估提供了下界。
- 解表示（MS+OS）和解码逻辑在 [2] 中用于离散萤火虫算法，该表示法天然保证了工序优先级和机器可用性的合法性。

## 实施提示
- 采用**事件驱动仿真**或**间隙链表**的方式可高效维护机器可用间隙。
- 若维护窗口非固定，可将维护任务的开始时间视为决策变量，并在解码器中动态确定，注意不要与其它维护或加工任务冲突。
- 多资源约束（如仅一个维护团队）需额外保证不同机器的维护时段不重叠。

---

### 参考文献

- [1] A Mathematical Model for the Flexible Job Shop Scheduling Problem With Availability Constraints-e4585d89.pdf
- [2] A Mathematical Model and a Firefly Algorithm for an Extended Flexible Job Shop Problem with Availability Constraints-2289591f.pdf
- [3] An effective heuristic for flexible job-shop scheduling problem with maintenance activities-3d02380f.pdf
