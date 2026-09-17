# 工业 JSON Full 资料传递审计

审计日期：2026-09-07。本文仅审计已结束的 `full_foundation_v2_qiming` 资料传递与失败证据，不报告正在进行的 900 秒 Worker 实验结果。

## 范围与结论

审计对象是工业 JSON 算例 `small_20_random_seed20260716.json` 的 Full foundation 生成，包含初始尝试和两次修补。三次均仍处于 `baseline_trial=1`，不是已经完成三轮算法演进。

已经确认：工业知识卡被 Main 侧上下文选中，但没有进入这三次 Worker 的授权读取集合；通用方法 Skill 则确实通过工具加载，随后生成的候选仍未通过 Core。不能将本次记录概括为“没有 Skill”，也不能概括为“工业知识卡加载后无效”。资料传递缺口、通用 Skill 的语义边界、超时与候选代码不完整是并存的事实，尚未通过受控实验分离各自因果贡献。

本次审计不读取旧项目求解器源码、历史排程或密钥，不修改求解器、Skill、知识卡、固定评价器或现有实验资料。当前仅将 Worker 超时从 300 秒改为 900 秒的重跑保持原资料版本，用于观察时间预算变化，不在运行中混入资料修复。

## 已核实的传递链

工业包将 `knowledge/references/industrial_json/search_adaptation.md` 同时登记为方法包 `assets`、知识 `base_cards` 和 `industrial_json` 标签卡，见 [domain_pack.json](F:/huawei_fjsp_llm/fjsp_harness_agent/domain_packs/fjsp_industrial_json/domain_pack.json:41)。该包没有声明 `implementation_contract_asset`。

三份 `context_packet.json` 都含有这张卡的 `auto_knowledge_cards` 和 `active_direction_knowledge.paths`，且 `active_method_package.package_id` 是 `industrial_json_adaptation`。这证明 Main 侧资料选择已经发生，不是知识库完全没有相关资料。

然而，[pack.py](F:/huawei_fjsp_llm/fjsp_harness_agent/harness_agent/domains/pack.py:511) 仅从显式实现契约字段加载 `implementation_contract_assets`；没有该字段时返回空契约。因此三份 WorkerAssignment 的 `method_package.contract_paths` 均为空。[worker.py](F:/huawei_fjsp_llm/fjsp_harness_agent/harness_agent/context/worker.py:808) 又在 `baseline_trial == 1` 时将 `supporting_paths` 清空，使普通 `assets` 和方向知识无法作为 supporting knowledge 进入 read_set。

这是“已经注册到 Main/RAG，但 baseline 交付规则过滤了 Worker 资料”的具体路径。方法包注册本身不是完全未关联；缺的是工业基础阶段必须阅读的实现契约通道，且 baseline 过滤规则阻止了普通卡通道。

Worker 只附加目标文件和授权 read_set 中的文件，见 [opencode_worker.py](F:/huawei_fjsp_llm/fjsp_harness_agent/harness_agent/workers/opencode_worker.py:218)；运行时读取权限由这些路径构造，见同文件第 671 行。三份 `opencode_context_budget.json` 均声明 `full_context_packet_visible=false`。因此，不能以“完整 context packet 有工业卡”为证据声称 Worker 收到了它。

## 三次尝试的实际资料

| 尝试 | mode / trial / attempt | 实际完成加载的 Skill | read_set 范围 | 工业搜索卡 |
| --- | --- | --- | --- | --- |
| 初始 | baseline / 1 / 0 | foundation、constructive-search | 目标代码、manifest、原实例、requirement、IO、可读实例说明 | 不在 read_set |
| repair_001 | baseline / 1 / 1 | foundation、exact-hybrid | 目标代码、manifest、原实例、requirement、IO、可读实例说明 | 不在 read_set |
| repair_002 | repair / 1 / 2 | foundation、exact-hybrid | 仅目标代码、manifest | 不在 read_set |

表中 Skill 对应完整 ID 为 `fjsp-solver-foundation-worker`、`fjsp-constructive-search-worker`、`fjsp-exact-hybrid-worker`。每次的 `opencode_events.jsonl` 都包含相应 `tool=skill` 且 `state.status=completed` 的事件。这里“实际加载”指工具成功返回技能内容，不等同于模型正确遵守了每条指令。

Skill 为什么仍能进入初始 baseline：[worker.py](F:/huawei_fjsp_llm/fjsp_harness_agent/harness_agent/context/worker.py:657) 的 `specialized_baseline` 判定只要求选中包并声明 `required_features`，因此允许匹配方法族的 Skill；它不要求该包存在 `implementation_contract_asset`。另一方面，第 131 行的变体 baseline deliverable 判定还要求 `implementation_bundle.required_components`。这些条件不同，形成了“可加载通用方法 Skill，但没有工业实现契约和组件清单”的状态。

## 修补上下文缺口

repair_002 的 `opencode_session.json` 显示 `requested_session_id=null`、`command_session_id=null`，并创建了与前次不同的 `observed_session_id`；`prompt_mode` 是 `new_direction_session_via_materialized_workspace`。它是新的模型会话，不能假定保留了之前的对话文档。

同时，[worker.py](F:/huawei_fjsp_llm/fjsp_harness_agent/harness_agent/context/worker.py:738) 将 `mode=repair` 归为 `focused_retry`，第 877 行和第 879 行据此不再加入独立实例与文档的 read_set。实际 assignment 仅留下候选目标和 manifest。

确定事实是：该次新会话未通过授权附件重新得到原实例、requirement 和 IO。候选源码及反馈可能保留了部分语义，不能据此断言模型完全不知道工业契约；但当前证据不足以证明完整契约在新会话中可得。文件仍在 workspace 或 manifest 里存在路径，也不等于 Worker 有权读取它。

## 失败与上下文负担

| 尝试 | Worker 状态 | Core 失败摘要 | 附加上下文字符数 |
| --- | --- | --- | ---: |
| 初始 | 300 秒超时，有代码差异 | `NameError: name 'Abram' is not defined` | 516540 |
| repair_001 | 300 秒超时 | 同一 `Abram` 未定义错误仍在 | 542105 |
| repair_002 | 300 秒超时，有代码差异 | `NameError: name 'build_schedule' is not defined` | 42115 |

字符数来自 `opencode_context_budget.json` 的 `total_attached_chars`，不是模型实际 token 数或账单。首两次约 51.7 万和 54.2 万字符，是确定的输入负担证据；它是否导致超时，以及增加时限能改善多少，需要对照实验，不能从字符数单独推断。

以上 Core 结果证明候选未形成完整可运行求解器，并未到达足以比较排程质量的阶段。不能用这些失败来声称 Full 比 None 的最终求解质量差，也不能把资料未交付认定为唯一失败原因。

## 旧 Skill 的工业适用边界

通用方法思路可以参考，但以下硬编码语义不能直接套用当前工业 IO。此表是文本契约差异，不是对已生成候选代码的额外审查。

| Skill 或资料 | 已存在的标准语义 | 当前工业语义差异 |
| --- | --- | --- |
| [minimum-time-lag](F:/huawei_fjsp_llm/fjsp_harness_agent/.codex/skills/fjsp-min-time-lag-adapter-worker/SKILL.md:12) | 相邻工序 finish-start 下界、固定文本尾部 | 工业 Q-time 独立选择两端 start/end，保留上下界和非相邻记录 |
| [maximum-time-lag](F:/huawei_fjsp_llm/fjsp_harness_agent/.codex/skills/fjsp-max-time-lag-adapter-worker/SKILL.md:12) | 固定四元组 finish-start 上界 | 工业不能丢弃其他锚点、下界以及与日历/运输的耦合 |
| [alternative-path](F:/huawei_fjsp_llm/fjsp_harness_agent/.codex/skills/fjsp-alternative-path-adapter-worker/SKILL.md:12) | 原始路线为 0、原始工序池和 K 条尾部路线 | 工业使用原始字符串 route ID 和各自的 process_list |
| [machine-availability](F:/huawei_fjsp_llm/fjsp_harness_agent/.codex/skills/fjsp-machine-availability-adapter-worker/SKILL.md:11) | 标准尾部及半开停机区间 | 工业固定验证器有闭区间转换和一分钟边界容差 |
| [PBPM](F:/huawei_fjsp_llm/fjsp_harness_agent/.codex/skills/fjsp-pbpm-adapter-worker/SKILL.md:10) | 标准批机容量/family 尾部，并含 makespan 导向验收 | 工业按工序 family 精确匹配，批容量取成员最小整数容量，且目标不是 makespan |
| [reentrant](F:/huawei_fjsp_llm/fjsp_harness_agent/.codex/skills/fjsp-reentrant-adapter-worker/SKILL.md:11) | 显式 loop 尾部和 `pre + body * repeat + post` 展开 | 工业重复设备访问不构成该循环展开契约，不应激活它 |
| [priority](F:/huawei_fjsp_llm/fjsp_harness_agent/.codex/skills/fjsp-priority-adapter-worker/SKILL.md:12)、[distributed-transfer](F:/huawei_fjsp_llm/fjsp_harness_agent/.codex/skills/fjsp-distributed-transfer-adapter-worker/SKILL.md:12) | 各自固定 makespan/优先完工或负荷/能耗目标 | 本次仅按窗内完成重量优先、正换型次数次之排序 |

当前实际加载的 foundation 和 exact Skill 也并非完全表示无关：foundation 存在普通工序时长与 non-overlap 的描述，工业同批成员允许共享加工区间且批时长取成员最大值；exact Skill 存在“完整模型必须表达 makespan”等标准措辞。当前工业 IO 应优先，但仍需要确保相应覆盖说明实际传入 Worker，而不是只在 Main 的工业卡里存在。

SDST 的 setup-aware 时序和搜索思想可以复用，但工业的稀疏三元组字符串键、仅普通加工计 setup、setup 次数目标及批加工例外必须按本次契约处理，不能直接复用单特性实现假设。

## 旧工程资料的额外边界

本节转引主代理提供的另一项已完成资料审计结论，不表示本次 Worker 已收到这些资料；本次审计没有访问旧求解器源码。

- 旧工程存在 [huawei-aluminum-fjsp/SKILL.md](F:/huawei_fjsp_llm/huawei_fjsp_llm/.codex/skills/huawei-aluminum-fjsp/SKILL.md)，但当前工业 domain pack 未注册该 Skill。它围绕现成 preset/tuner 流程，并含 Pareto 或标量权衡，不等价于当前“独立生成求解器、严格词典序”的运行协议。
- 旧工程存在 [complex-fjsp-stepwise-optimization/SKILL.md](F:/huawei_fjsp_llm/huawei_fjsp_llm/.codex/skills/complex-fjsp-stepwise-optimization/SKILL.md)，但当前工业 domain pack 同样未注册。其约束台账和分阶段优化可以提供组织方法，但缺少本次联合约束的实现细节，不能仅凭文件存在认定当前已具备完整工业适配。
- 另一项审计核对到旧正式交付版设计文档采用无限批，旧 `xue2024`、`wang2021`、`ji2024` 知识卡含 soft-max-Qtime 语义。本次固定契约则是有限并行批、硬 Q-time 上下界和严格词典序目标。旧资料不能原样用作当前实现或验收依据；需要明确筛选、改写和版本记录后才能进入新的受控实验。

因此，“磁盘上有旧工业 Skill/论文卡”“当前 pack 注册了它”“Worker 获准并实际加载了它”“其语义匹配当前合同”是四个不同命题。正在进行的 900 秒实验不新增这些注册、不导入这些旧资料，也不更改其有限批和硬约束口径。

## 证据文件

以下文件均属于已经结束的 `full_foundation_v2_qiming`，不属于正在进行的 900 秒实验：

- 初始 [context_packet.json](F:/huawei_fjsp_llm/fjsp_harness_agent/outputs/contract_industrial_small20_random_20260907/full_foundation_v2_qiming/worker_loop/agent_generated_baseline/context_packet.json)、[worker_assignment.json](F:/huawei_fjsp_llm/fjsp_harness_agent/outputs/contract_industrial_small20_random_20260907/full_foundation_v2_qiming/worker_loop/agent_generated_baseline/worker_assignment.json)、[opencode_events.jsonl](F:/huawei_fjsp_llm/fjsp_harness_agent/outputs/contract_industrial_small20_random_20260907/full_foundation_v2_qiming/worker_loop/agent_generated_baseline/worker/opencode_events.jsonl)。
- 修补 1 [assignment_revision_001.json](F:/huawei_fjsp_llm/fjsp_harness_agent/outputs/contract_industrial_small20_random_20260907/full_foundation_v2_qiming/worker_loop/agent_generated_baseline/repair_001/assignment_revision_001.json)、[opencode_events.jsonl](F:/huawei_fjsp_llm/fjsp_harness_agent/outputs/contract_industrial_small20_random_20260907/full_foundation_v2_qiming/worker_loop/agent_generated_baseline/repair_001/worker/opencode_events.jsonl)。
- 修补 2 [assignment_revision_002.json](F:/huawei_fjsp_llm/fjsp_harness_agent/outputs/contract_industrial_small20_random_20260907/full_foundation_v2_qiming/worker_loop/agent_generated_baseline/repair_002/assignment_revision_002.json)、[opencode_events.jsonl](F:/huawei_fjsp_llm/fjsp_harness_agent/outputs/contract_industrial_small20_random_20260907/full_foundation_v2_qiming/worker_loop/agent_generated_baseline/repair_002/worker/opencode_events.jsonl)、[opencode_session.json](F:/huawei_fjsp_llm/fjsp_harness_agent/outputs/contract_industrial_small20_random_20260907/full_foundation_v2_qiming/worker_loop/agent_generated_baseline/repair_002/worker/opencode_session.json)。
- 三次状态及错误分别见初始、repair_001、repair_002 目录下的 `cycle_result.json` 和 `cycle_report.md`；字符数分别见其 `worker/opencode_context_budget.json`。

## 后续验证建议，尚未实施

待固定资料版本的 900 秒实验结束后，再单独验证工业卡的 Worker 交付路径及新修补会话的最小 IO 保留。应同时核对注册、选中、read_set、实际附件、Skill 工具事件和 Core 结果，而不是仅检查上下文文件或 Skill 名称。后续任何资料修复都应记录版本，并与纯时间预算变化区分，不能混为同一次受控对照。

## 后续状态：铝加工 Skill 增量对照已完成

2026-09-07 的 `v4aluminumcontrol / v4aluminumon` 已在前述历史试验结束后，单独适配并注册第一个 `huawei-aluminum-fjsp`，未接入第二个 Skill。处理组有成功加载事件和合法多件合批，但三轮最终质量与对照打平，不能据此声称净收益。完整协议、公共指标修复、结果与剩余缺陷见 [测试台账](contract_acceptance_test_ledger.md) 的“第一个铝加工 Skill 的增量对照”；本文此前未注册等描述仅指其标明的历史版本，不代表当前注册状态。
