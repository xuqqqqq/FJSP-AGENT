# FJSP 自演进框架合同测试台账

## 目的

本台账按问题特性逐项记录 Full（启用 FJSP 领域适配）与 None（仅通用自演进闭环）的同轮次对照证据。标准 FJSP 与 SDST 三实例聚合已完成，当前按同一协议逐项推进其他单特性。

## 固定指标

| 指标 | 计算口径 | 合同目标 | Bonus |
| --- | --- | ---: | ---: |
| 可行满足率 | 固定 Core 判定合法的实验数 / 实验总数 | 提升 8 个百分点 | 提升 16 个百分点 |
| 平均求解质量 | `(None 平均 makespan - Full 平均 makespan) / None 平均 makespan` | 提升 5% | 提升 10% |
| 求解效率 | 质量差不超过 1% 时，比较固定 Core 实测 solver wall time | 提升 10% | 提升 30% |
| 有效迭代率 | 晋升方向轮数 / 已尝试方向轮数 | 记录 | 显著提升 |
| 主控耗时 | 总墙钟时间减去并发 Core 评测区间并集 | 20 轮或 30 分钟内 | 记录 |

Full 与 None 必须使用相同需求/IO 文档、实例、固定 evaluator、seed、候选 solver 时间预算和主控轮次。实际主控耗时不要求相同，只记录并检查是否超过 30 分钟。外层实验顺序执行；单次实验内部可同时运行多个 lane。

## 标准 FJSP

### 当前发现

1. 旧流程只能生成单次运行报告，没有自动生成 Full/None 合同指标与达标结论。
2. 旧 manifest 没有分离主控耗时和固定 Benchmark 评测耗时。
3. 旧对照可能分别由 Agent 生成不同 baseline，随机起点会掩盖领域适配的净收益。
4. 旧报告未强制检查输入哈希、预算、评价器和冻结 baseline 是否一致。
5. 单算例、单次顶层运行只能作为冒烟证据，不能证明稳定净收益或泛化。

### 已实施的平台修复

| 修复 | 验收方式 | 状态 |
| --- | --- | --- |
| 自动 Full/None 合同对比器 | `compare-contract-guidance` 生成 JSON 与 Markdown | 已完成 |
| 协议门禁 | 输入哈希、evaluator、目标、轮次、seed、预算、共享 baseline 一致 | 已完成 |
| 主控/Core 分时 | 合并并发 Core 时间区间后从总墙钟时间扣除 | 已完成 |
| solver 效率实测 | Core 记录 `solver_wall_seconds`，不采用 solver 自报耗时 | 已完成 |
| 合同阈值判定 | 8%/5%/10% 与 16%/10%/30% 自动判定 | 已完成 |
| None 源码可读、领域资产隔离 | Main/Worker 可读取冻结 incumbent 源码，Skill、知识库、方法包和经验记忆保持禁用 | 已完成 |
| lane 语义修复 | 每轮按实际算法方法启动 3 条 lane，禁止把 `direct_evidence` 等角色名作为候选方法 | 已完成 |
| Main 失败恢复 | None 仅复用最近一轮完整真实方法 tournament；无历史方法时明确失败 | 已完成 |
| 历史压缩健壮性 | implementation planning 在 `io_digest` 被压缩省略时恢复空 section | 已完成 |
| CLI/Web Main 模式对齐 | `run-standard-worker-loop` 默认并显式记录 `main_planning_mode=fast`，保留 `research` 供离线深度规划 | 已完成 |
| 候选 Core 计时覆盖 | smoke、diagnostic smoke 和 full evaluator 均写入 Core 时间区间 | 已完成 |
| 跨 family 方法包隔离 | family tournament 的每条 lane 独立绑定兼容方法包；无兼容包时清空父 lane 包合同并由对应方法族 Skill 承接 | 已完成 |
| OpenCode 语义审查上下文 | 审查包优先放置候选源码，并投影重复的方向/方法包字段，避免附件截断后 Reviewer 看不到源码 | 已完成 |

### 正式对照协议

1. 选择标准 FJSP 实例集，并冻结一份由固定 Core 验证合法的共享 baseline。
2. 先运行 None，再运行 Full；两侧使用相同轮次和候选 solver 时间预算。
3. 每次实验内部保留多 lane 竞争；不同时启动另一项外层实验。
4. 至少完成 3 组独立配对运行后才评价稳定性；单组结果只记为试跑。
5. 自动报告协议门禁失败时，该组证据不得用于合同达标声明。

### 运行记录

| 日期 | 实例集 | 配对次数 | 轮次 | None 结果 | Full 结果 | 合同比较报告 | 结论 |
| --- | --- | ---: | ---: | --- | --- | --- | --- |
| 2026-08-31 | DP17a，seed 0，solver 60 秒，共享 baseline 2673 | 1/3 | 3 | 2537，合法率 100%，2/3 轮晋升 | 2162，合法率 100%，2/3 轮晋升 | `outputs/contract_standard_fjsp_dp17a_20260831/contract_comparison_v4/contract_comparison.md` | 协议有效；Full 相对 None 质量提升 14.781%，通过合同 5% 与 Bonus 10%。单组证据不足以证明稳定性；Full/None 主控耗时均超过 30 分钟。 |
| 2026-08-31 | DP15a，seed 0，solver 60 秒，共享 baseline 2969，fast Main | 2/3 | 3 | 2226，最终解合法，3/3 轮晋升 | 2234，最终解合法，2/3 轮晋升 | `outputs/contract_standard_fjsp_dp15a_20260831/contract_comparison_v3/contract_comparison.md` | 协议有效；质量差 0.359%，满足质量相当口径；Full solver 实测效率提升 13.186%，通过合同 10% 效率指标。Full 主控 2388.9 秒，仍超过 30 分钟，且候选层有一次非法运行。 |
| 2026-08-31 | DP15a 修复后同模型复测，Mimo v2.5 free，seed 0，solver 60 秒，共享 baseline 2969 | 2/3 修复复测 | 3 | 2600，最终解合法，2/3 轮晋升 | 2164，最终解合法，1/3 轮晋升 | `outputs/contract_standard_fjsp_dp15a_20260831/contract_comparison_v5_same_model/contract_comparison.md` | 协议门禁全部通过；Full 质量提升 16.769%，同时通过合同 5% 与 Bonus 10%；Full 主控 1073.1 秒，满足 30 分钟。该复测替代 DP15a v3 的质量结论，但仍需第三组独立配对验证跨实例稳定性。 |
| 2026-08-31 | DP18a，Mimo v2.5 free，seed 0，solver 60 秒，共享 baseline 2830 | 3/3 | 3 | 2276，最终解合法，2/3 轮晋升 | 2171，最终解合法，2/3 轮晋升 | `outputs/contract_standard_fjsp_dp18a_20260831/contract_comparison_v2/contract_comparison.md` | 协议门禁全部通过；Full 质量提升 4.613%，方向为正但单组未达到合同 5%；Full 主控 1837.8 秒，超过 30 分钟 37.8 秒。 |
| 2026-08-31 | DP17a + DP15a 同模型修复复测 + DP18a 聚合 | 3/3 | 每组 3 | 平均 makespan 2471.0，最终可行率 100% | 平均 makespan 2165.667，最终可行率 100% | `outputs/contract_standard_fjsp_three_instance_20260831/contract_comparison_v1/contract_comparison.md` | 全部协议门禁通过；Full 聚合质量提升 12.357%，通过合同 5% 与 Bonus 10%。该结论证明当前三实例总体净收益，不代表每个实例单独达到 5%；Full 平均主控 2200.7 秒，仍未满足 30 分钟。 |

### DP17a 第一组配对证据

- 外层按 None 后 Full 严格顺序执行；每个实验内部同时运行 3 条算法方法 lane。
- 两侧使用相同需求/IO、实例 SHA256、固定 evaluator、seed 0、3 轮、solver 60 秒、共享冻结 baseline 和候选 lane 数。
- None 每轮 3/3 候选合法，最终 makespan 从 2673 降至 2537；Full 每轮 3/3 候选合法，最终降至 2162。
- Full 相对 None 的平均 makespan 改善为 14.781%；可行率均为 100%，有效迭代率均为 2/3。
- 固定 Core 实测 solver wall time 不具质量等价前提，不能据此申报效率提升。
- 主控耗时（扣除固定 Core 区间）约为 Full 3691.2 秒、None 2031.5 秒，均未满足 30 分钟边界；后续应缩短 Main/Worker provider 与修补耗时。
- 当前只有 1/3 组独立配对，结果可作为正式单组证据，不作为跨运行稳定性结论。

### DP15a 第二组配对证据

- 外层按 None 后 Full 严格顺序执行；两侧均显式使用 `main_planning_mode=fast`，每轮启动 3 条算法方法 lane。
- 两侧使用相同需求/IO、实例哈希、固定 evaluator、seed 0、3 轮、solver 60 秒、共享 baseline 2969 和相同 Worker/修补预算。
- None 最终 makespan 为 2226，Full 为 2234；Full 质量落后 0.359%，在自动比较器的 1% 质量相当范围内。
- 固定 Core 实测最终 solver wall time 为 Full 2.692 秒、None 3.100 秒；Full 效率提升 13.186%，通过合同 10% 效率指标。
- 主控耗时（扣除固定 Core 区间）为 Full 2388.9 秒、None 1466.9 秒；None 满足 30 分钟边界，Full 仍超出约 9.8 分钟。
- v1 的三轮 research Main 均失败并退回固定 tournament；v3 改用 fast Main 后第 1、2 轮成功规划，但第 0 轮仍有一次 120 秒 Provider 超时 fallback。
- 当前累计 2/3 组配对：DP17a 通过质量指标，DP15a 通过质量相当时的效率指标；仍不足以声称 Skill 具有跨运行稳定净收益。

### DP15a 同模型修复复测证据

- Full 与 None 均使用 `opencode/mimo-v2.5-free`，命令产物可审计；两侧使用相同需求/IO、实例哈希、固定 evaluator、seed 0、3 轮、solver 60 秒、共享 baseline 2969 和相同 Worker/修补预算。
- 三轮均实际启动 3 条不同算法方法 lane；自动协议门禁全部通过。
- None 最终 makespan 为 2600，Full 为 2164；Full 平均求解质量提升 16.769%，通过合同 5% 和 Bonus 10% 质量指标。
- 两侧最终可行率均为 100%；Full 前两轮候选未满足激活/语义要求而回退，第三轮候选激活通过并晋升，最终结果未吸收失效候选。
- 主控耗时（扣除固定 Core 区间）为 Full 1073.1 秒、None 1775.7 秒，两侧均满足 30 分钟边界。
- 本次修复包括：零加工时长跨度不再触发 assignment-regret 高柔性打法；标准局部搜索包按增量算法阶段签发；包内 lane 使用真实算法方法名；OpenCode Worker 使用同 provider 的只读语义审查，避免 DeepSeek 余额故障阻断候选。
- 该复测解决了 DP15a 上 Full 质量倒退问题，但仍只有两个不同标准 FJSP 算例，不能据此宣称跨实例稳定性；第三组配对仍是正式总体结论的前置条件。

### DP18a 第三组配对证据

- v1 暴露两个控制链缺陷：`population_memetic` 与 `constructive_search` 错误继承仅兼容 `coupled_local_search` 的 `standard_fjsp_awls_hgtsa`，三轮均在 Worker 启动前失败；语义审查包超过 provider 有效上下文，Reviewer 未看到位于尾部的候选源码。
- 修复后 v2 每轮实际同时启动 `constructive_search`、`coupled_local_search`、`population_memetic` 三条真实方法 lane；首轮三条 lane 均完成且通过激活检查，不再出现 `requested method package is not resolvable`。
- 语义审查包从该候选旧版 103356 字符压缩到 64773 字符，并把候选源码放在第 5276 字符开始的位置；v2 产出结构化 `pass`、`warning` 或 `repair_required` 结论，不再出现“未提供候选源码”的 unavailable 异常。
- None 最终 makespan 为 2276，Full 为 2171；Full 质量净提升 4.613%，单组略低于合同 5% 门槛，但已把 v1 的质量倒退 22.979% 修复为正收益。
- 两侧最终合法率均为 100%，Full 三轮决策为晋升、回滚、晋升；第三轮 `constructive_search` 候选以 2171 晋升。
- Full 主控耗时 1837.8 秒，超过 30 分钟 37.8 秒；该边界仍需通过减少重复生成/审查与更早停止无改进 lane 解决。

### 三实例聚合结论

- 聚合使用 DP17a v4、DP15a v5 同模型修复复测和 DP18a v2，三组均为 seed 0、3 轮、每轮 3 lane、solver 60 秒，并按每对各自冻结的共享 baseline 运行。
- 自动比较器的全部协议门禁通过，包括输入/evaluator、预算、Main/Worker 模型、实际 lane 数、真实方法名和冻结 baseline。
- Full 平均 makespan 为 2165.667，None 为 2471.0，聚合质量提升 12.357%；通过合同 5% 与 Bonus 10% 质量指标。
- Full 与 None 最终可行率均为 100%，因此本组三实例的收益来自求解质量，而不是可行率差异。
- Full 有效迭代率为 55.556%，None 为 66.667%；Full 的优势不能归因于更多晋升轮次，而是领域方法候选的晋升质量更高。
- Full 平均主控耗时 2200.7 秒，None 为 1461.1 秒；30 分钟主控边界仍未整体通过，是标准 FJSP 下一轮优化的首要剩余风险。

## FJSP-SDST

### oddla13 第一组配对证据

- 从 HUdata `oddla01` 至 `oddla20` 中仅按结构规模选择 `oddla13`，未参考历史分数；该实例包含 20 个作业、5 台机器和 100 道工序，SHA256 为 `2538d3b68c346a3af663928c3130a0646cd3ff2f929b848e646d80cc8d0351a9`。
- 先由中性 Agent 生成并经固定 Core 验证共享 baseline，makespan 为 1696；随后严格按 None 后 Full 顺序运行。两侧均使用 Mimo v2.5 free、seed 0、3 轮、每轮 3 条方法 lane、solver 60 秒和相同 Worker/修补预算。
- 自动比较器的全部协议门禁通过，包括输入与 evaluator 哈希、预算、Main/Worker 模型、实际 lane 数、方法名和冻结 baseline。
- None 三轮决策为晋升、晋升、回滚，最终 makespan 为 1420；Full 三轮决策为回滚、晋升、晋升，最终 makespan 为 1205。两侧最终解均合法，有效迭代率均为 2/3。
- Full 相对 None 的平均求解质量提升 15.141%，通过合同 5% 和 Bonus 10% 质量指标；可行率均为 100%，因此不申报可行率提升。质量不等价，固定 Core solver wall time 不能用于申报效率提升。
- Full 的 SDST 方法包 `fjsp_sdst_awls_adaptation` 已实际进入首轮 Worker，三轮所选候选的机制激活检查均通过；最终晋升候选还通过固定 evaluator 和语义审查。
- Full 主控耗时 2042.4 秒，超过 30 分钟约 4.0 分钟；None 为 794.4 秒。Full 的长尾来自个别方法 lane 的模型生成接近 900 秒上限，是下一步应改进的预算感知方法路由问题。
- 合同比较报告：`outputs/contract_fjsp_sdst_oddla13_20260901/contract_comparison_v1/contract_comparison.md`。当前仅完成 SDST 1/3 组独立配对，证明该大实例上的净收益，不足以声明 SDST 跨实例稳定收益。

### oddla14 第二组配对证据

- `oddla14` 包含 20 个作业、5 台机器和 100 道工序，SHA256 为 `2445b0a599105de6a5bec1226f93d82f0c799059bfeca212d797a8c36398e416`；选择时未读取历史分数。
- 中性 baseline 首次生成因 Worker 未修改 solver 且修补超时而失败，失败产物保留；v2 经一次合法性修补后由固定 Core 验证，冻结 makespan 为 4499。
- Full 与 None 均使用 Mimo v2.5 free、seed 0、3 轮、每轮 3 条方法 lane、solver 60 秒和同一冻结 baseline；自动比较器的全部协议门禁通过。
- None 三轮均晋升，最终 makespan 为 1652；Full 仅 1/3 轮晋升，最终 makespan 为 1237。两侧最终解均合法，Full 相对 None 的质量提升为 25.121%，通过合同 5% 和 Bonus 10%。
- Full 主控耗时 1933.8 秒，超过 30 分钟约 2.2 分钟；None 为 1133.7 秒。Full 的优势来自单次高质量晋升，不来自更高有效迭代率。
- 合同比较报告：`outputs/contract_fjsp_sdst_oddla14_20260901/contract_comparison_v1/contract_comparison.md`。

### oddla20 第三组配对证据

- `oddla20` 包含 10 个作业、10 台机器和 100 道工序，平均候选机数约 1.13，SHA256 为 `31e4b21248b2f050f71ebb93898295e57ba55e932092e1db38e524854b1410c0`；它覆盖了与前两例不同的低柔性 10 机器结构。
- Mimo 在 baseline v1/v2 及 None v1 的首轮 Main 上连续返回空事件流并超时；系统因不存在完整历史 tournament 而明确失败，没有伪造 fallback。随后复用 oddla14 已通过无硬编码审查的通用 foundation，并在 oddla20 上重新通过固定 Core，冻结 baseline 为 3995。
- 为完成公平配对，Full 与 None 一起切换为 `opencode/nemotron-3.5-lightning-free`，仍使用相同 seed、3 轮、3 lane、solver/Worker/修补预算和冻结 baseline。该组内部全部协议门禁通过，但不能作为三实例同模型稳定性证据。
- None 最终 makespan 为 3889，1/3 轮晋升；Full 三轮均无 eligible candidate，最终保持 baseline 3995。Full 相对 None 质量倒退 2.726%，本例所有合同与 Bonus 指标均失败。
- Full 的 9 个候选中，多数虽 Core 合法但仍为 3995；局部搜索和群体模因多次机制未激活，`exact_hybrid` 的 exact-execution 门禁失败，另有 timeout/failed-runtime。当前方法路由没有适配该低柔性、setup/排序主导结构。
- Full 主控耗时 3493.6 秒，None 为 3089.3 秒，均远超 30 分钟。原始 Worker 与同轮修补分别拥有 900 秒预算，导致单 lane 最坏时延叠加；应改为同一 lane 共享总预算并对空事件流提前停止。
- 合同比较报告：`outputs/contract_fjsp_sdst_oddla20_20260901/contract_comparison_v2_nemotron/contract_comparison.md`。

### oddla20 自建 DeepSeek 修复复测证据

- 复测显式固定 Main、Coding Worker 与 Full 语义审查模型为 `qiming/deepseek-v4-flash`，不再使用 Mimo 或 Nemotron；Full/None 复用同一 Core 合法 baseline 3995，并保持 seed 0、3 轮、每轮 3 lane、solver 60 秒及相同 Worker/修补预算。
- 首次自建模型运行暴露 OpenCode 并发 lane 共用全局 SQLite 数据目录导致 `database is locked`；Harness 已为每个 Main 输出目录和 Worker lane 会话分别设置隔离的 XDG data/state 目录。三路真实并发探针均返回 meaningful event，相关 OpenCode 单测通过。
- Full v4 第三轮恢复旧 session 时三条 lane 均发生零事件重试；Harness 已改为恢复请求无首包时保留物化 workspace 与任务附件、但切换 fresh OpenCode session。Full v5 三轮共 9 条正式 lane 均产生非空事件流，数据库锁为 0。
- None 最终 makespan 为 1296，2/3 轮晋升；Full 最终 makespan 为 997，1/3 轮晋升。Full 相对 None 的质量提升为 23.071%，通过合同 5% 和 Bonus 10% 质量指标；两侧最终解均由固定 Core 判定合法。
- Full 首轮晋升 `exact_hybrid`，其 `cp_sat_called=true`、exact execution=`passed`、机制激活检查通过、语义审查为非阻断 warning；后两轮无严格更优候选并保留 997 incumbent。该结果来自一个 exact lane 与构造/耦合搜索 lane 的真实竞争，不是用 CP 替代全部 lane。
- Full 主控耗时 2719.1 秒，仍未满足 30 分钟边界；None 为 981.7 秒，满足 30 分钟边界。有效迭代率 Full 为 1/3、None 为 2/3，Full 优势来自单次高质量晋升而非更多晋升次数。
- 自动比较器判定全部协议检查通过，报告：`outputs/contract_fjsp_sdst_oddla20_20260901/contract_comparison_v3_qiming/contract_comparison.md`。该复测取代 Mimo/Nemotron 结果作为 oddla20 当前交付环境证据，但仍只有一次独立自建模型配对，不能单独声明跨运行稳定性。

### 三实例聚合结论

- 聚合使用 oddla13、oddla14 和 oddla20 三组配对；每组内部均使用相同模型、输入/evaluator、预算、实际 lane 数和冻结 baseline，自动比较器判定 `protocol_valid=true`。
- Full 平均 makespan 为 2145.667，None 为 2320.333，聚合质量提升 7.528%；通过合同 5% 质量指标，但未达到 Bonus 10%。两侧最终可行率均为 100%。
- Full 有效迭代率为 33.333%，None 为 66.667%；Full 的总体收益来自少量高质量晋升，候选激活与生成稳定性仍弱。
- Full 平均主控耗时 2489.9 秒，None 为 1672.5 秒，聚合未满足 30 分钟要求。
- oddla20 使用 Nemotron，前两例使用 Mimo，因此该聚合证明当前框架配置在三种配对条件下总体超过合同 5%，不证明单一模型或每个实例都稳定净收益。修复 oddla20 的低柔性方法路由和 lane 总预算后，应以统一模型复测该例。
- 聚合报告：`outputs/contract_fjsp_sdst_three_instance_20260901/contract_comparison_v1/contract_comparison.md`。

## 工作负荷多目标 FJSP

### Brandimarte Mk01 第一组配对证据

- 正常规模实例 `fjsp.brandimarte.Mk01.m6j10c3.mofjsp.txt` 直接复用公开 Brandimarte Mk01 主体，仅由文件标记激活工作负荷多目标合同；包含 10 个作业、6 台机器和 55 道工序，SHA256 为 `392ebe8cfeba6cca0defc340db09b37e6c1d9e6e5cc3426bce859c471dcd7aa2`。
- 先在 `iterations=0` 模式下由自建 `qiming/deepseek-v4-flash` 自主生成中性 solver。初稿把 `--time-limit-sec` 只接受为整数，固定 Core 以实际传入的 `48.0` 暴露该 CLI 缺陷；同方向修补后 Core 验证合法并冻结共享 baseline `(makespan, max_machine_workload, total_workload)=(87, 68, 173)`。
- 外层严格按 None 后 Full 顺序执行；两侧均固定自建 `qiming/deepseek-v4-flash`、seed 0、3 轮、每轮 3 条不同算法方法 lane、solver 60 秒、Worker 单次尝试 300 秒、一次同方向修补机会和同一冻结 baseline。自动比较器的输入/evaluator、目标、预算、模型、实际 lane 数和共享 baseline 门禁全部通过。
- None 三轮均晋升，最终三元组为 `(56, 46, 157)`；Full 前两轮晋升、第三轮回滚，最终三元组为 `(42, 42, 161)`。两侧最终解均由固定 `fjsp_multiobjective_workload_evaluator.py` 判定合法。
- 以合同主质量指标 makespan 计算，Full 相对 None 提升 `25.0%`，通过合同 5% 与 Bonus 10%。Full 的最大机器负荷也由 46 降至 42，但总负荷由 157 增至 161；由于固定目标是严格词典序，前序 makespan 的显著改善使 Full 三元组整体严格更优，不能表述为三个分量都改善。
- Full 每轮实际运行 `constructive_search`、`exact_hybrid` 和 `coupled_local_search`。最终胜者为第 2 轮 `coupled_local_search`，Core 记录 185271 次已评价移动、161104 次换机移动、24086 次非相邻重插、801 次接受和 328 次重启，机制激活检查通过；语义审查仍留下非阻断的 `repair_required`/warning，因此本组只按固定 Core 与 activation 证据申报质量，不宣称语义审查完全无告警。
- Full 与 None 的正式 lane 均为 9 条，OpenCode 命令均使用自建 DeepSeek；审计未发现 `database is locked`、空事件流或零事件重试。Full 有 12 次 Worker 命令、None 有 10 次，差额来自同方向修补而非额外正式 lane。
- 扣除固定 Core 区间后，Full 主控耗时为 2004.6 秒，超过 30 分钟约 3.4 分钟；None 为 794.5 秒，满足边界。当前 `max_runtime_seconds` 约束单次 Worker 尝试而非同 lane 的生成加修补总预算，Full 的 exact/语义修补形成长尾，是下一步平台性能修复项。
- 自动比较报告：`outputs/contract_fjsp_multiobjective_workload_mk01_20260901/contract_comparison_v1_qiming/contract_comparison.md`。当前只有 1/3 组独立配对，证明该实例上的净收益，不足以声明多目标负载特性的跨实例稳定收益。

### Brandimarte Mk08 第二组配对与低柔性路由修复

- 大规模低柔性实例 `fjsp.brandimarte.Mk08.m10j20c2.mofjsp.txt` 复用公开 Brandimarte Mk08 主体，仅以 `.mofjsp` 文件名激活三目标合同；包含 20 个作业、10 台机器、225 道工序、最大候选机数 2，原始 token 序列 SHA256 为 `1976993260B27470A61F29ACFEC93C9322DE750119D0CB9D6575A73D023E6177`。
- 固定 Core 验证共享 baseline 为 `(2081, 592, 2534)`。None 使用同一自建 `qiming/deepseek-v4-flash`、seed 0、3 轮、每轮 3 lane、solver 60 秒、Worker 300 秒和一次修补，最终为 `(566, 524, 2655)`，1/3 轮晋升且合法。
- 首次 Full v2 的正式协议有效，但三 lane 被路由为 `coupled_local_search`、`exact_hybrid`、`population_memetic`，强全局构造被挤出；最终仅为 `(2042, 592, 2541)`，显著劣于 None。该失败证据保留在 `contract_comparison_v1_qiming`，不作为当前性能结论。
- Harness 已修复多目标低柔性路由：3 lane 配额内优先保留 `constructive_search`，并要求其从全局 ready-list 生成多个完整合法排程；多目标耦合搜索的 activation 还必须分别证明顺序 move 与换机 move 均真实执行。Main 相关 96 项单测通过。
- 修复后 Full v3 三轮均实际运行 `coupled_local_search`、`exact_hybrid` 和 `constructive_search`，共 9 条正式 lane；最终三元组为 `(523, 523, 2631)`，1/3 轮晋升且合法。相对 None 的 makespan 提升 `7.597%`，通过合同 5%，未通过 Bonus 10%。
- 最终胜者为首轮 `exact_hybrid`；固定 Core 证据记录 `cp_sat_called=true`、状态 `OPTIMAL`、目标/界均为 523、322 个 interval、781 个约束，exact execution 与 activation 均通过。首轮构造 lane 也把弱 baseline 降到 660，但只形成一个完整候选，因 `candidates_evaluated > 1` 门禁未被错误计为完整激活。
- Full v3 有 12 次 Worker 命令，均使用 `qiming/deepseek-v4-flash`；9 条正式 lane 之外的 3 次来自同方向修补。审计未发现空事件流或 `database is locked`。自动比较器判定全部协议检查通过。
- Full 主控耗时 2040.2 秒，超过 30 分钟约 240 秒；None 为 1047.9 秒。Mk08 当前质量合同通过，但主控时限仍未通过。
- 自动比较报告：`outputs/contract_fjsp_multiobjective_workload_mk08_20260901/contract_comparison_v2_qiming/contract_comparison.md`。当前完成 2/3 组独立配对，尚不能声明该特性的跨实例稳定收益。

### Brandimarte Mk10 第三组配对与高柔性路由修复

- 高柔性大实例 `fjsp.brandimarte.Mk10.m15j20c5.mofjsp.txt` 复用公开 Brandimarte Mk10 主体，仅以 `.mofjsp` 文件名激活三目标合同；包含 20 个作业、15 台机器、240 道工序、最大候选机数 5，原始 token 序列 SHA256 为 `C59EB50D179D35571D217C2B0E7449D1901BC559F8EF752DA2925B875D4DC7CF`。
- 固定 Core 验证共享 baseline 为 `(1084, 318, 1981)`。None 在同一模型、seed、3 轮、3 lane、solver/Worker/修补预算下最终为 `(254, 218, 2173)`，2/3 轮晋升且合法。
- Full v1 的 9 条 Worker lane 均在 300 秒生成上限超时，未在候选 worktree 形成可晋升算法，最终保持 baseline。根因是每条 improvement lane 重复加载 foundation、实验技能、需求全文和项目材料，单条首轮上下文约 2.3 万 token；该失败保留在 `contract_comparison_v1_qiming`。
- Harness 已把 `exact_probe_tournament` improvement assignment 收敛为聚焦上下文：保留变体适配器、当前方法技能、实例、方法合同、incumbent 与 smoke，移除已由合法 incumbent/Core 覆盖的通用 foundation、实验技能及重复需求材料。Full v2 恢复到 1/3 晋升并取得 `(684, 273, 1953)`，但仍劣于 None；该中间失败保留在 `contract_comparison_v2_qiming`。
- 进一步发现 Mk10 的 `avg_candidate_count=2.983333`、柔性工序占比 `0.920833`、加工时长跨度非零，却因阈值硬编码为 `avg_candidate_count >= 3.0` 未触发高柔性 playbook。画像门槛已改为常规高选择密度或“接近 3 台且高覆盖”两档，并把 `high_flexibility`、`assignment_regret`、`idle_gap` 与保序重解码标签只路由到 constructive/coupled lane，exact lane 保持独立。
- 修复后 Full v3 最终为 `(221, 217, 2167)`，2/3 轮晋升且合法；相对 None 的 makespan 提升 `12.992%`，同时通过合同 5% 与 Bonus 10%。最终胜者为第 2 轮 `coupled_local_search`，固定 Core 记录 434 次顺序 move、108 次换机 move、542 次总评估、1 次严格改进；构造入口也评估了 3 个完整候选，activation 通过。
- Full v3 共 9 条正式方法 lane、15 次自建 DeepSeek Worker 命令，额外 6 次来自同方向修补；空事件流 0、数据库锁 0。Full 主控耗时 2150.5 秒，超过 30 分钟；None 为 892.4 秒。
- 自动比较报告：`outputs/contract_fjsp_multiobjective_workload_mk10_20260901/contract_comparison_v3_qiming/contract_comparison.md`。

### 工作负荷多目标三实例聚合结论

- 聚合使用 Mk01、Mk08 和 Mk10 三组统一 `qiming/deepseek-v4-flash` 配对；每组内部均保持相同输入/evaluator、seed、3 轮、solver/Worker/修补预算、实际 lane 数和冻结 baseline，自动比较器判定 `protocol_valid=true`。
- Full 平均 makespan 为 `262.0`，None 为 `292.0`，聚合质量提升 `10.274%`；通过合同 5% 与 Bonus 10%。两侧最终可行率均为 100%。
- Full 有效迭代率为 `55.556%`，None 为 `66.667%`；领域模块的优势来自更高质量晋升，而不是增加晋升次数。
- Full 平均主控耗时 `2065.1` 秒，None 为 `911.6` 秒；Full 聚合仍未满足 30 分钟主控边界。Full 固定 Core solver 平均耗时也高于 None，但两侧质量不相当，因此不申报效率指标。
- 聚合报告：`outputs/contract_fjsp_multiobjective_workload_three_instance_20260901/contract_comparison_v1_qiming/contract_comparison.md`。该结果完成工作负荷多目标特性 3/3 独立实例验证，并在统一自建模型下证明当前总体净收益；不代表每次随机生成或每个单例都必然达到 Bonus 10%。

## 后续特性队列

| 顺序 | 问题特性 | 状态 |
| ---: | --- | --- |
| 1 | 标准 FJSP | 三实例聚合通过合同 5% 与 Bonus 10% 质量指标 |
| 2 | SDST | 3/3 组完成；聚合通过合同 5%，未通过 Bonus 10%；oddla20 自建 DeepSeek 修复复测单例通过合同与 Bonus 质量指标 |
| 3 | 工作负荷多目标 FJSP | Mk01、Mk08、Mk10 共 3/3 组完成；聚合质量提升 10.274%，通过合同 5% 与 Bonus 10%；Full 主控时限未通过 |
| 4 | 其余必选/扩展特性 | 待逐项安排 |
