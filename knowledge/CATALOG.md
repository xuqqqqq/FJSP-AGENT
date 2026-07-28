# 现有 Skill 与知识库分类清单

盘点日期：2026-07-25。清单覆盖项目级 `.codex/skills`、OpenCode 内部 `.opencode/skills`、`knowledge/` 和标准 FJSP Domain Pack。

## 1. 项目 Skill

### 1.1 规划、诊断与领域扩展

| Skill | 角色 | 主要内容 |
| --- | --- | --- |
| `fjsp-agent-generated-solver` | 独立求解器生成/审查 | IO 契约、独立 solver、完整解码和候选演进流程 |
| `fjsp-solver-optimizer` | 研究诊断 | 瓶颈诊断、可证伪假设、代码变异和 evaluator 证据闭环 |
| `fjsp-awls-sdst-adapter` | 完整方法适配规划 | AWLS 到 SDST 的 Method Package 选择和适配审查 |
| `fjsp-variant-domain-pack` | 领域扩展 | 新变体的 Domain Pack、RAG、Skill 和 evaluator 边界设计 |

### 1.2 Worker 基础与实验

| Skill | 角色 | 主要内容 |
| --- | --- | --- |
| `fjsp-solver-foundation-worker` | 基础合同 | parser、CLI/JSON、合法解码、deadline、incumbent 和诊断证据 |
| `fjsp-experiment-design-worker` | 实验执行 | 最小插桩、activation checks、pilot、消融和证据回报 |

### 1.3 Worker 方法实现

| Skill | 方法族 | 主要内容 |
| --- | --- | --- |
| `fjsp-constructive-search-worker` | `constructive_search` | 多规则、空闲间隙、有限前瞻、Beam、多起点和结构去重 |
| `fjsp-coupled-local-search-worker` | `coupled_local_search` | assignment/sequence 耦合邻域、VND/ILS/Tabu 和独立 incumbent |
| `high-flexibility-fjsp-playbook` | `constructive_search` + `coupled_local_search` | 高柔性标准 FJSP 的 earliest-gap、assignment regret、换机信赖域和保序重解码 |
| `fjsp-exact-hybrid-worker` | `exact_hybrid` | CP-SAT、局部精确修复、trust region 和预算组合 |
| `fjsp-population-memetic-worker` | `population_memetic` | 双层编码、交叉变异、多样性和有界局部改进 |
| `fjsp-sdst-adapter-worker` | 横切适配 | SDST 状态、setup-aware 解码和 move 评价 |

### 1.4 OpenCode 内部 Skill

| Skill | 用途 |
| --- | --- |
| `algoforge-assignment` | 读取受控 WorkerAssignment 并执行限定代码任务 |
| `experiment-design` | 通用 pilot、消融、证据与交接流程 |

项目级 Skill 共 **12** 个，OpenCode 内部 Skill 共 **2** 个。

## 2. 知识库

### 2.1 原则与架构契约

- `principles/harness_agent_design.md`
- `principles/fjsp_variant_domain_pack_rag.md`
- `principles/agent_generated_variant_quality_contracts.md`
- `principles/knowledge_skill_authoring_standard.md`

### 2.2 Benchmark、IO 与边界事实

- `benchmarks/standard_fjsp_format.md`
- `benchmarks/fjsp_benchmark_scope.md`
- `benchmarks/fjsplib.md`
- `benchmarks/fjsp_sdst_fattahi.md`
- `benchmarks/standard_fjsp_bounds_LB_UB.csv`

### 2.3 通用 FJSP 方法知识

- `references/general_fjsp/fjsp_instance_feature_method_router.md`
- `references/general_fjsp/fjsp_method_selection_zh.md`
- `references/general_fjsp/fjsp_scene_survey_2025_10_17.md`
- `references/general_fjsp/doagnn_fjsp_rl.md`
- `references/general_fjsp/eoh.md`
- `references/general_fjsp/heuragenix.md`
- `references/general_fjsp/optimization_playbook.md`
- `references/general_fjsp/core_pseudocode.md`
- `references/general_fjsp/lessons_and_pitfalls.md`

### 2.4 标准 FJSP 实现知识

- `references/standard_fjsp/constructive_multistart_blueprint.md`
- `references/standard_fjsp/high_flexibility_assignment_first_playbook.md`
- `references/standard_fjsp/cp_sat_hybrid_blueprint.md`
- `references/standard_fjsp/operation_machine_reassignment.md`
- `references/standard_fjsp/critical_path_machine_block_neighborhood.md`
- `references/standard_fjsp/population_memetic_blueprint.md`
- `references/standard_fjsp/tabu_search_loop.md`
- `references/standard_fjsp/hgtsa_fjsp_n8_k_insertion_blueprint.md`
- `references/standard_fjsp/xiejin_hgtsa_n8_k_insertion_tabu_spec.md`
- `references/standard_fjsp/standard_fjsp_agent_generated_reference_skeleton.md`
- `references/standard_fjsp/standard_fjsp_agent_generated_neighborhood_templates.md`
- `references/standard_fjsp/standard_fjsp_algorithm_semantic_review_contract.md`
- `references/standard_fjsp/standard_fjsp_awls_hgtsa_execution_skeleton.md`
- `references/standard_fjsp/idle_critical_beam_implementation_template.md`
- `references/standard_fjsp/awls_coupled_search_loop_template.md`
- `references/standard_fjsp/cp_sat_trust_region_implementation_template.md`
- `references/standard_fjsp/memetic_search_loop_template.md`

### 2.5 FJSP-SDST 变体知识

- `references/sdst/awls_sdst_adapter_notes.md`
- `references/sdst/awls_sdst_agent_generated_transfer_notes.md`
- `references/sdst/agent_generated_decoder_neighborhood.md`
- `references/sdst/setup_aware_decoder_implementation_template.md`
- `references/sdst/awls_sdst_adaptation_implementation.md`
- `references/sdst/awls_sdst_literature_notes.md`

### 2.6 完整 Method Package

| Package | 资产 |
| --- | --- |
| `standard_fjsp_awls_hgtsa` | README、行为合同、实现合同、实例无关参考 solver |
| `fjsp_sdst_awls_adaptation` | SDST 实现合同，并复用标准包的参考 solver 与迁移知识 |

### 2.7 导入材料

- `imported/huawei_fjsp_knowledge/`：外部代码库、数据集和论文摘要，共 13 份 Markdown 资产。
- `imported/local_papers/README.md`：本地论文索引。
- `imported/local_papers/raw/`：11 份用户导入 PDF 原件；不参与默认 RAG，不由自动清理改写。

### 2.8 当前为空的生命周期目录

- `capabilities/`：没有当前有效能力快照。
- `experiment_memory/`：本周暂未保留实验记忆。

## 3. Domain Pack 注册关系

`domain_packs/standard_fjsp/domain_pack.json` 当前声明：

- 4 个可组合方法族：构造搜索、耦合局部搜索、精确混合、种群/模因。
- 8 个可自动授权的 Worker Skill：2 个 always-include 基础 Skill、4 个方法族 Skill、1 个按高柔性标签激活的组合 Skill、1 个 SDST 横切 Skill。
- 2 个完整 Method Package。
- 一阶段方法选择卡、二阶段标签词表和标签到知识卡的映射。

`fjsp-agent-generated-solver`、`fjsp-solver-optimizer`、`fjsp-awls-sdst-adapter` 和 `fjsp-variant-domain-pack` 是项目可用 Skill，但不属于当前 Worker 自动授权集合。

## 4. 边界审计

### 放置正确

- Skill 主正文主要描述触发、读取顺序、执行步骤、边界、交付物和验证。
- Benchmark 数值、方法包合同、论文摘要和本周实验记忆均有独立生命周期目录。
- Worker 自动授权关系放在 Domain Pack，而不是写死在通用后端。

### 已完成的边界收敛

通用 Beam、局部搜索、CP-SAT、Memetic、SDST 解码、AWLS 机制和优化手册已经从 Skill-local `references/` 迁到 `knowledge/references/`，Skill 和 Domain Pack 均已改用新路径。

以下内容与执行合同紧耦合，继续保留为 Skill-local reference：

- 独立 solver IO/安全合同。
- Worker 实验模板与证据交接格式。
- 基础状态/完整解码合同。

当前 Domain Pack 中只剩独立 solver 合同这类紧耦合 Skill reference；通用算法检索路径均落在知识库。
