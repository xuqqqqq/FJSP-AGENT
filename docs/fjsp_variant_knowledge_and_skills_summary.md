# FJSP 变种知识库与 Skill 建设总结

> 更新日期：2026-08-13  
> 范围：近期适配的 FJSP 变种知识卡、Worker Skill、Method Package、Domain Pack 与验证资产。

## 1. 总体结论

近期工作不是为每个算例分别添加求解器分支，而是建立了一条可复用、可路由、可验证的变种适配链路：

```mermaid
flowchart LR
    A[需求 / IO / 算例] --> B[Domain Pack<br/>识别变种与能力]
    B --> C[知识卡<br/>语义与搜索依据]
    C --> D[Worker Skill<br/>执行步骤与边界]
    D --> E[Method Package<br/>完整方法合同]
    E --> F[Coding Worker<br/>自主生成求解代码]
    F --> G[Fixed Core / Evaluator<br/>合法性与目标裁决]
    G -->|严格提升| H[Promotion]
    G -->|不合法或未提升| I[Repair / Rollback]
```

当前形成的主要资产为：

| 资产 | 数量 | 统计口径 |
| --- | ---: | --- |
| 变种 Adapter Skills | 9 | SDST、Min/Max Time Lag、Alternative Path、Release Time、Machine Availability、Distributed Transfer、Priority、Reentrant |
| Skill 正文 | 393 行 | 9 个 `SKILL.md` 的物理行数 |
| 变种知识卡 | 22 张 | 近期八类变种 16 张，加上较早的 SDST 6 张 |
| 近期 Method Packages | 14 个 | 近期八类变种；只统计包含有效 `implementation_contract.json` 的目录 |
| SDST 历史 Method Package | 1 个 | `fjsp_sdst_awls_adaptation`，未计入上面的 14 个 |
| 直接变种测试 | 60 个 | 9 个变种相关测试文件中的命名测试 |

十四个近期方法合同进一步包含：

- 74 个 `required_components`；
- 31 条 competition tracks；
- 52 项 checkpoint checks；
- 21 个 canonical activation fields。

这些字段的目的不是增加文档数量，而是把“方法是否真正实现和运行”变成可检查事实。

## 2. 四类资产分别负责什么

### 2.1 知识库：回答“是什么、为什么、如何适配”

变种知识主要位于 [`knowledge/references/`](../knowledge/references/)。每个近期变种通常有两张核心卡：

1. **语义与解码卡**：定义输入尾部、编号体系、约束公式、完整解码和合法性不变量。
2. **搜索适配卡**：说明构造、局部搜索、群体/模因搜索和 exact/hybrid 应如何感知该变种。

知识卡不负责授予文件权限，不指定具体模型，也不把某次 makespan、seed 或临时失败猜测写成通用规律。

### 2.2 Worker Skill：回答“Agent 接到任务后怎么做”

变种 Skill 位于 [`.codex/skills/`](../.codex/skills/)，内容保持短小、可执行，主要包括：

- 触发条件与所需 runtime features；
- 读取知识卡和方法合同的顺序；
- 对已选方法族进行横切适配的步骤；
- 禁止的语义捷径；
- activation evidence、验收条件和停止条件。

Adapter Skill **不自行选择方法族**。Main 选择 constructive、coupled local search、population/memetic 或 exact/hybrid 后，Adapter 再把变种语义注入该方法。

### 2.3 Method Package：回答“什么才算完整实现”

方法合同位于 [`knowledge/method_packages/`](../knowledge/method_packages/)。它不是后端插件，也不是一份可直接运行的固定答案，而是 Worker 必须整体满足的实现合同。

典型字段包括：

- `required_components`：必须存在的解析、解码、搜索、验收和诊断组件；
- `required_behaviors`：组件必须表现出的语义；
- `coupled_groups`：不能拆开或只实现一半的机制组合；
- `forbidden_shortcuts`：会导致名实不符的捷径；
- competition tracks：不同 lane 应形成的真实方法差异；
- checkpoint / activation checks：如何证明代码路径确实执行。

### 2.4 Domain Pack 与 Evaluator：负责路由和真值裁决

- 大部分变种注册在 [`domain_packs/standard_fjsp/domain_pack.json`](../domain_packs/standard_fjsp/domain_pack.json)。
- Distributed Transfer 因 IO、资源身份和目标体系差异较大，使用独立的 [`domain_packs/fjsp_distributed_transfer/domain_pack.json`](../domain_packs/fjsp_distributed_transfer/domain_pack.json)。
- Domain Pack 通过 `supported_variants`、`required_features`、`activation_tags`、知识标签和 `selection_enabled` 路由知识、Skill 与 Method Package。
- Fixed parser/validator/evaluator 重新计算合法性和目标值；Worker 的自报指标只作为诊断证据，不能替代 Core 裁决。

## 3. 变种覆盖总表

| 变种 | 知识卡 | Adapter Skill | 变种方法合同 | 主要方法覆盖 |
| --- | ---: | --- | ---: | --- |
| SDST | 6 | `fjsp-sdst-adapter-worker` | 1 | AWLS/HGTSA 横切适配 |
| Minimum Time Lag | 2 | `fjsp-min-time-lag-adapter-worker` | 3 | Constructive / Coupled / Exact |
| Maximum Time Lag | 2 | `fjsp-max-time-lag-adapter-worker` | 3 | Constructive / Coupled / Exact |
| Alternative Path | 2 | `fjsp-alternative-path-adapter-worker` | 3 | Constructive / Coupled / Exact |
| Release Time | 2 | `fjsp-release-time-adapter-worker` | 1 | 四方法族横切适配 |
| Machine Availability | 2 | `fjsp-machine-availability-adapter-worker` | 1 | 四方法族横切适配 |
| Distributed Transfer | 2 | `fjsp-distributed-transfer-adapter-worker` | 1 | Constructive / Coupled / Population / Exact |
| Priority | 2 | `fjsp-priority-adapter-worker` | 1 | 四方法族横切适配 |
| Reentrant | 2 | `fjsp-reentrant-adapter-worker` | 1 | 四方法族横切适配 |

## 4. 各变种具体写了什么

### 4.1 Sequence-Dependent Setup Time（SDST）

**问题语义**

- 换型时间由同一机器上的前后工序顺序共同决定。
- Setup 不能折叠进 processing time，否则会错误改变机器占用、关键路径和邻域增量。
- 首工序 setup、矩阵索引和 setup 是否占机必须严格服从活动 IO contract，不能猜测。

**知识库内容**

- setup-aware decoder 与邻域重算；
- AWLS/HGTSA 在 SDST 下的状态、关键结构、move、tabu 和自适应评分；
- 论文归纳、实现模板、迁移说明与失败边界。

知识目录：[`knowledge/references/sdst/`](../knowledge/references/sdst/)

**Skill 约束**

- 保留 Main 已选方法族，只增加 SDST 横切语义；
- 时间传播、候选评价、关键路径和完整合法性检查都必须 setup-aware；
- activation evidence 应包含 setup lookup、setup contribution、setup-aware move 和完整重解码计数；
- 若仍使用标准 FJSP 的旧 delta，不得声称 SDST 适配完成。

Skill：[`fjsp-sdst-adapter-worker`](../.codex/skills/fjsp-sdst-adapter-worker/SKILL.md)  
方法合同：[`fjsp_sdst_awls_adaptation`](../knowledge/method_packages/fjsp_sdst_awls_adaptation/implementation_contract.json)

### 4.2 Minimum Time Lag

**问题语义**

相邻工序的最小时间间隔为：

```text
start(k + 1) >= end(k) + L_min
```

- 约束是固定、稀疏、machine-free 的 finish-start 下界；
- 等待期间前驱机器已经释放，不能把 lag 计入机器占用；
- `L_min = 0` 与普通工序 precedence 等价。

**知识库内容**

- lag 尾部解析与约束索引；
- earliest-ready 传播和 lag-aware 完整解码；
- 多起点构造、关键 lag 压力、换机重插和完整重解码；
- CP-SAT/局部精确修复中的真实最小 lag 约束；
- 正权依赖环和零权 SCC 的处理边界。

知识目录：[`knowledge/references/min_time_lag/`](../knowledge/references/min_time_lag/)

**Skill 约束**

- 不允许先生成 lag-blind 排程再只靠最终右移修补；
- 不允许把 lag 膨胀进 processing time；
- 局部 delta 只能筛选，接受前必须完整重解码；
- 最终要求 `min_time_lag_violations = 0`。

Skill：[`fjsp-min-time-lag-adapter-worker`](../.codex/skills/fjsp-min-time-lag-adapter-worker/SKILL.md)

**方法合同**

- [`fjsp_min_time_lag_constructive_adaptation`](../knowledge/method_packages/fjsp_min_time_lag_constructive_adaptation/implementation_contract.json)
- [`fjsp_min_time_lag_coupled_local_search`](../knowledge/method_packages/fjsp_min_time_lag_coupled_local_search/implementation_contract.json)
- [`fjsp_min_time_lag_exact_hybrid`](../knowledge/method_packages/fjsp_min_time_lag_exact_hybrid/implementation_contract.json)

### 4.3 Maximum Time Lag

**问题语义**

最大时间间隔为：

```text
start(to_op) <= end(from_op) + L_max
```

- 约束可以稀疏且非相邻；
- 它是随前驱完工时刻变化的上界，不能简化为固定 due date；
- `L_max = 0` 与 precedence 共同形成严格 no-wait。

**知识库内容**

- 完整差分约束解码与不一致环检测；
- earliest/latest 双向可行窗口；
- tight-pair、bridge-chain 和 max-lag slack 驱动邻域；
- 候选机器与顺序变化后的全局重解码；
- CP-SAT 中真实发布 `start(to) <= end(from) + L_max`；
- 完整 seed pool fallback，用于在主构造搜索无法形成叶节点时保留可验证候选。

知识目录：[`knowledge/references/max_time_lag/`](../knowledge/references/max_time_lag/)

**Skill 约束**

- 禁止只用单向右移修补 maximum lag；
- 禁止复用标准 FJSP 旧 delta 后直接接受 move；
- exact/hybrid 必须报告 `cp_sat_called` 和真实 posted max-lag 约束数；
- 正式结果必须 `max_time_lag_violations = 0`。

Skill：[`fjsp-max-time-lag-adapter-worker`](../.codex/skills/fjsp-max-time-lag-adapter-worker/SKILL.md)

**方法合同**

- [`fjsp_max_time_lag_constructive_adaptation`](../knowledge/method_packages/fjsp_max_time_lag_constructive_adaptation/implementation_contract.json)
- [`fjsp_max_time_lag_coupled_local_search`](../knowledge/method_packages/fjsp_max_time_lag_coupled_local_search/implementation_contract.json)
- [`fjsp_max_time_lag_exact_hybrid`](../knowledge/method_packages/fjsp_max_time_lag_exact_hybrid/implementation_contract.json)

**当前证据与边界**

- mt10c1 实验中，63 条 max-lag 约束全部合法，CP-SAT 得到 `927` 且状态为 `OPTIMAL`；
- coupled lane 记录了 13,660 个已评估 move 和 11,504 个 max-lag 拒绝 move；
- seed pool 产生了 2 个完整、不同、合法的构造候选；
- 当前 `927` 的质量来自 CP-SAT。seed pool 解决的是构造路径的激活可达性，尚未证明启发式质量已经足够强。

证据目录：

- [`max_time_lag_routing_wiring_20260813_151840`](../outputs/selfhosted_variant_tests/max_time_lag_routing_wiring_20260813_151840/)
- [`max_time_lag_constructive_budget_20260813_163553`](../outputs/selfhosted_variant_tests/max_time_lag_constructive_budget_20260813_163553/)

### 4.4 Alternative Process Path

**问题语义**

- 每个 job 在原始路线和若干替代路线中必须且只能选择一条；
- `op_id` 引用原始 operation pool，但输出只包含所选路线的工序；
- 输出中的 `selected_routes` 必须与 schedule 一致；
- 未选路线的工序不得泄漏到结果中。

**知识库内容**

- route one-hot、所选路线工序覆盖和条件 precedence；
- route-aware 构造与完整诱导解码；
- route-switch 与换机/重插耦合邻域；
- CP-SAT optional intervals、route one-hot 与 conditional precedences。

知识目录：[`knowledge/references/alternative_path/`](../knowledge/references/alternative_path/)

**Skill 约束**

- 禁止把路线选择误写成普通 machine flexibility；
- route switch 后必须重建 operation set 并完整重解码；
- exact/hybrid 必须提供 one-hot、optional interval 和 conditional precedence 的真实 posted 证据；
- 构造、局部、群体和精确方法分别有独立 canonical activation counters。

Skill：[`fjsp-alternative-path-adapter-worker`](../.codex/skills/fjsp-alternative-path-adapter-worker/SKILL.md)

**方法合同**

- [`fjsp_alternative_path_constructive_adaptation`](../knowledge/method_packages/fjsp_alternative_path_constructive_adaptation/implementation_contract.json)
- [`fjsp_alternative_path_coupled_local_search`](../knowledge/method_packages/fjsp_alternative_path_coupled_local_search/implementation_contract.json)
- [`fjsp_alternative_path_exact_hybrid`](../knowledge/method_packages/fjsp_alternative_path_exact_hybrid/implementation_contract.json)

**当前证据**

- mt10c1 合法 makespan 为 `711`，CP-SAT 状态为 `OPTIMAL`；
- coupled search 评估 14,671 个 move，接受 852 个；
- 其中 route-switch 评估 7,931 个，接受 20 个；
- exact 模型发布 10 个 route one-hot、110 个 optional intervals 和 168 个 conditional precedences；
- 候选与 incumbent 持平时正确 rollback，没有把平局误晋升。

证据目录：[`alternative_path_coupled_wiring_20260813_150542`](../outputs/selfhosted_variant_tests/alternative_path_coupled_wiring_20260813_150542/)

### 4.5 Release Time

**问题语义**

- 标准 FJSP 主体后有两行宽度为 `max(job_count, machine_count)` 的数据，并验证 `-1` padding；
- job `j` 的首工序不得早于 `release_time[j]`；
- machine `m` 上的任何工序不得早于 `machine_initial_availability[m]`。

**知识库内容**

- 释放时间尾部的完整解析；
- job-ready 与 machine-ready 双下界；
- 构造、解码和候选验收共享 ready-time guard；
- 局部搜索移动后重新计算受影响链的就绪时间；
- exact 模型对 job 和 machine 初始时间发布真实下界。

知识目录：[`knowledge/references/release_time/`](../knowledge/references/release_time/)

**Skill 约束**

- 不得只在 evaluator 中检查 release time；
- 搜索必须真正使用 job/machine ready time；
- 保持固定 CLI 和 `standard_fjsp_schedule_v1`；
- 不修改固定 parser/evaluator Core。

Skill：[`fjsp-release-time-adapter-worker`](../.codex/skills/fjsp-release-time-adapter-worker/SKILL.md)  
方法合同：[`fjsp_release_time_adaptation`](../knowledge/method_packages/fjsp_release_time_adaptation/implementation_contract.json)

### 4.6 Machine Availability / Downtime

**问题语义**

- 输入尾部为 `K + K * (machine_id, start, end)`；
- 不可用窗口采用半开区间 `[start, end)`；
- 工序不可抢占，必须整体位于所有维修窗口之外；
- 重叠或相接窗口只能在保持相同并集语义时合并。

**知识库内容**

- 机器日历归一化；
- calendar-aware earliest-gap placement；
- 邻域移动后的完整日历重解码；
- downtime-aware critical structure；
- exact 模型中的固定不可用 intervals 与 NoOverlap。

知识目录：[`knowledge/references/machine_availability/`](../knowledge/references/machine_availability/)

**Skill 约束**

- Adapter 不改变 Main 选择的方法族；
- 禁止排程区间与维修窗口相交；
- 构造、局部搜索、群体搜索和 exact 必须共享同一日历语义；
- 最终执行完整 machine-calendar legality audit。

Skill：[`fjsp-machine-availability-adapter-worker`](../.codex/skills/fjsp-machine-availability-adapter-worker/SKILL.md)  
方法合同：[`fjsp_machine_availability_adaptation`](../knowledge/method_packages/fjsp_machine_availability_adaptation/implementation_contract.json)

### 4.7 Distributed FJSP with Transfer

**问题语义**

- 使用五行 DFM 元数据头和按 factory 分组的候选编码；
- 输入 ID 为 1-based，输出 `factory_id` 和全局 `machine_id` 为 0-based；
- 资源身份是 `(factory_id, machine_id)`，不能把跨厂同号机器视为同一资源；
- 转运等待：同机 `0`、同厂异机 `30`、跨厂 `60`；
- 转运能耗为 `transfer_time * 6`；
- 固定目标为严格词典序最小化：

```text
(makespan, max_factory_workload, total_energy_consumption)
```

**知识库内容**

- DFM/grouped-candidate parser 与全局机器编号；
- 工厂、机器、工序顺序联合编码和完整转运感知解码；
- transfer-aware critical path；
- 关键块 sequence move、较快工厂-机器替换、降低转运的 reassignment；
- population/memetic 中的 OS/FA/MA 多样性；
- 加工与转运能耗的独立重算。

知识目录：[`knowledge/references/distributed_transfer/`](../knowledge/references/distributed_transfer/)

**Skill 约束**

- 不得把同厂异机或跨厂转运漏算；
- 每个 operation 同时输出 `factory_id` 和 `machine_id`；
- 加权和或任意 Pareto archive 选择不能替代 Core 的严格词典序；
- 论文中的初始化比例只能作为可调假设，不能硬编码为通用答案；
- exact lane 必须处理活动三目标合同，或明确声明有界 repair neighborhood。

Skill：[`fjsp-distributed-transfer-adapter-worker`](../.codex/skills/fjsp-distributed-transfer-adapter-worker/SKILL.md)  
方法合同：[`fjsp_distributed_transfer_adaptation`](../knowledge/method_packages/fjsp_distributed_transfer_adaptation/implementation_contract.json)  
独立域包：[`fjsp_distributed_transfer/domain_pack.json`](../domain_packs/fjsp_distributed_transfer/domain_pack.json)

### 4.8 Job Priority

**问题语义**

- 标准主体后为 `K` 个严格递增、唯一、0-based 的优先 job ID；
- 当前 benchmark contract 要求 `K = ceil(job_count / 4)`；
- Priority 是软目标，不增加 precedence、release time、due date 或机器特权；
- 可行域与标准 FJSP 相同；
- 当前固定目标顺序为：

```text
minimize (makespan, priority_completion_time)
```

其中 `priority_completion_time` 是优先 job 的最大完工时间。只有 makespan 不变时，次目标改善才可接受。

**知识库内容**

- 优先 job 集合的完整解析和目标重算；
- priority-aware ready-operation ranking；
- 优先 job 关键链及其阻塞机器弧；
- 不增加 makespan 的 priority-targeted move；
- population 中的词典序选择；
- CP-SAT 两阶段优化：先 makespan，再在主目标界内优化优先工件完工时间。

知识目录：[`knowledge/references/priority/`](../knowledge/references/priority/)

**Skill 约束**

- 禁止把软优先级改成硬 precedence；
- 禁止使用可能牺牲 makespan 的加权和；
- 每个完整候选都重新计算两个目标；
- 输出、schedule、两个目标和 solver evidence 必须描述同一最终 incumbent。

Skill：[`fjsp-priority-adapter-worker`](../.codex/skills/fjsp-priority-adapter-worker/SKILL.md)  
方法合同：[`fjsp_priority_adaptation`](../knowledge/method_packages/fjsp_priority_adaptation/implementation_contract.json)

### 4.9 Reentrant FJSP

**问题语义**

- 标准主体后必须完整消费每个 job 的三元组 `(loop_start, loop_end, repeat)`；
- 完整路线展开为 `pre + loop_body * repeat + post`；
- 展开后使用连续 0-based `op_id`；
- 每次重复 pass 是独立 operation identity，可以独立选择候选机器；
- evaluator 要求每个 expanded operation 恰好出现一次。

**知识库内容**

- 连续单回路尾部解析和严格边界校验；
- 完整 route expansion 与 expanded/original identity 映射；
- 重入访问导致的机器瓶颈、重复访问压力和关键块；
- 构造、局部搜索、群体和 exact 的 expanded-route 适配。

知识目录：[`knowledge/references/reentrant/`](../knowledge/references/reentrant/)

**Skill 约束**

- 禁止只调度原始路线或复用同一 pass 的机器决策；
- 禁止引入当前 IO 未定义的 batching、概率返工或任意路线图；
- 候选生成和验收都基于完整展开后的 operation universe；
- 不替代 Main 已经选择的方法族。

Skill：[`fjsp-reentrant-adapter-worker`](../.codex/skills/fjsp-reentrant-adapter-worker/SKILL.md)  
方法合同：[`fjsp_reentrant_adaptation`](../knowledge/method_packages/fjsp_reentrant_adaptation/implementation_contract.json)

## 5. Agent 如何识别并加载新变种

当前识别和加载链路如下：

1. Domain parser 根据需求、IO 文档和实例内容生成 `instance_profile` 与 runtime features。
2. Domain family / alias 将任务路由到对应 Domain Pack。
3. `supported_variants` 和 feature contract 确认该变种是否受支持。
4. `required_features` 与 `activation_tags` 决定哪些 Adapter Skill 可进入 Worker Assignment。
5. `knowledge_tags` 与 Main 返回的 `knowledge_query` 选择少量相关知识卡。
6. 只有 `selection_enabled=true`、变种匹配且由 Main 显式选择的 Method Package 才进入 Worker `read_set`。
7. Worker 在隔离 worktree 内生成或修补独立 solver。
8. Fixed Core 重新运行 parser、validator 和 evaluator，决定 promotion、repair 或 rollback。

因此，新增变种通常需要以下资产，而不是新增 Web 路由或在 orchestration 中硬编码算法：

- Domain Pack 注册或现有 Domain Pack 的 variant/feature 扩展；
- IO/问题说明和固定 parser/evaluator；
- 语义与解码知识卡；
- 搜索适配知识卡；
- Adapter Skill；
- 一个或多个 Method Package；
- parser、validator、routing、assignment 和端到端测试。

只有当新变种属于全新的问题族、拥有不同 IO/evaluator 或目标体系时，才需要像 Distributed Transfer 一样建立独立 Domain Pack。

## 6. 已经解决的共性问题

这批知识和 Skill 重点修复了此前反复出现的框架性问题：

1. **只解析、不搜索感知**：现在要求字段必须进入构造、邻域、重解码和验收。
2. **方法族名实不符**：通过 required components 和 activation counters 验证真实机制。
3. **CP-SAT 静默跳过**：exact lane 要报告 `cp_sat_called`、solver status 和真实 posted constraints。
4. **不同 lane 方法相似**：Method Package 用 competition tracks 规定机制差异，而不是只改参数。
5. **局部 delta 误判合法**：复杂变种在接受 move 前统一执行完整重解码和固定 evaluator 复验。
6. **no-op 候选参与竞争**：无 changed files 或机制未激活的候选不能作为有效获胜者。
7. **把单次实验写成通用知识**：运行分数和 seed 留在 experiment memory，稳定知识只保留可复用语义和方法边界。

## 7. 当前仍需补强的部分

### 7.1 启发式质量还不均衡

- Max Time Lag 已解决完整合法种子和激活可达性，但高质量结果仍主要依赖 CP-SAT。
- Release Time、Downtime、Distributed Transfer、Priority 和 Reentrant 已形成语义闭环，但尚缺统一预算下的跨算例质量对照。

### 7.2 方法路由仍需更强的实例画像

- 小规模、低柔性、紧约束实例应至少保留一个真实 exact/hybrid lane；
- 中大规模或高柔性实例应强化 coupled local search、population/memetic 和启发式种子；
- lane 差异应来自机制，不应只是同一种构造搜索的参数变化。

### 7.3 Benchmark 口径需要统一

建议每类变种建立统一实验表：

- 小、中、大三档算例；
- 已知最优、下界或独立 solver 对照；
- 固定时间预算、随机种子和并发配置；
- 合法性、目标值、gap、事件流、provider/session 和 activation evidence；
- promotion/rollback 原因与残留缺陷。

### 7.4 前端证据展示需要继续完善

前端应直接展示：

- 当前处于生成、修复、评估还是回滚；
- 每条 lane 的方法族和实际激活机制；
- CP-SAT 是否安装、是否调用、状态和模型规模；
- changed files、候选合法性、目标值和晋升原因；
- provider retry、context limit、session reset 和事件流状态。

## 8. 关键文件索引

### 架构与写作规范

- [`docs/architecture.md`](architecture.md)
- [`knowledge/README.md`](../knowledge/README.md)
- [`knowledge/CATALOG.md`](../knowledge/CATALOG.md)
- [`knowledge/principles/knowledge_skill_authoring_standard.md`](../knowledge/principles/knowledge_skill_authoring_standard.md)
- [`knowledge/principles/fjsp_variant_domain_pack_rag.md`](../knowledge/principles/fjsp_variant_domain_pack_rag.md)
- [`knowledge/method_packages/README.md`](../knowledge/method_packages/README.md)

### Domain Packs

- [`standard_fjsp/domain_pack.json`](../domain_packs/standard_fjsp/domain_pack.json)
- [`fjsp_distributed_transfer/domain_pack.json`](../domain_packs/fjsp_distributed_transfer/domain_pack.json)

### 测试

- [`test_fjsp_sdst.py`](../tests/test_fjsp_sdst.py)
- [`test_fjsp_min_time_lag.py`](../tests/test_fjsp_min_time_lag.py)
- [`test_fjsp_max_time_lag.py`](../tests/test_fjsp_max_time_lag.py)
- [`test_fjsp_alternative_path.py`](../tests/test_fjsp_alternative_path.py)
- [`test_fjsp_release_time.py`](../tests/test_fjsp_release_time.py)
- [`test_fjsp_machine_availability.py`](../tests/test_fjsp_machine_availability.py)
- [`test_fjsp_priority.py`](../tests/test_fjsp_priority.py)
- [`test_fjsp_reentrant.py`](../tests/test_fjsp_reentrant.py)
- [`test_variant_integration.py`](../tests/test_variant_integration.py)

## 9. 一句话总结

当前版本已经形成“**Domain Pack 注册、知识卡解释、Skill 执行、Method Package 约束、Fixed Evaluator 裁决**”的 FJSP 变种扩展底座；下一阶段重点不再是继续堆适配文件，而是提高启发式质量、强化 lane 真实差异，并把运行证据完整呈现在前端。
