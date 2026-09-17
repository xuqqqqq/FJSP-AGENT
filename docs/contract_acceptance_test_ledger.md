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

## 单元运输、族序列相关准备、回流与拖期组合 FJSP

### Ins21 第一组配对与路由修复

- `Ins21` 包含 15 个作业、16 台物理机器、4 个 cell 和 48 道完整展开的工序，平均候选机数 1.500；固定目标为严格词典序 `(makespan, total_tardiness)`。
- 共享 Agent-generated baseline 经固定 Core 验证为 `(69,96)`。None 使用 `qiming/deepseek-v4-flash`、seed 0、3 轮、每轮 3 条方法 lane、solver 60 秒、Worker 300 秒和一次同方向修补，最终为 `(45,114)`，2/3 轮晋升且合法。
- Full v1 三轮 Fast Main 均耗尽 step 后 fallback，且低柔性通用路由选出 `coupled_local_search / exact_hybrid / population_memetic`，挤掉变种合同要求的多起点构造；最终仅为 `(44,79)`，相对 None 的 makespan 提升 2.222%，未达到合同 5%。该失败证据保留在 `contract_comparison_v1_qiming`。
- Harness 已把 Fast Main 限定为只读规划附件、禁用 Skill 与项目文件读取并使用隔离 cwd；同时为该组合变种固定保留 `constructive_search` lane，focused Worker 始终保留当前方法包专用语义卡，并在复合 adapter 激活时去除重复的通用 SDST adapter。相关 Main/WorkerAssignment 140 项测试和 Worker Loop 153 项测试全部通过；真实 Fast Main 探针一次返回，未 fallback 或调用 read/skill。
- Full v2 三轮均由真实 Fast Main 运行 `coupled_local_search / exact_hybrid / constructive_search`。首轮 exact 以 `(42,92)` 晋升，第二轮 exact 以 `(31,76)` 再次晋升；其 `cp_sat_called=true`、状态 `OPTIMAL`、activation、exact execution 与固定 Core 均通过。第二轮 constructive 也以 `(41,127)` 通过 Core 与机制激活，证明构造 lane 实际执行而非占位。
- Full v2 最终 `(31,76)`，相对 None 的 makespan 提升 `31.111%`，通过合同 5% 与 Bonus 10%；两侧最终可行率均为 100%，有效迭代率均为 2/3。Full 主控耗时约 1991.6 秒，仍超过 30 分钟约 191.6 秒；None 约 925.7 秒，满足 30 分钟。
- 自动比较器全部协议门禁通过，报告：`outputs/contract_fjsp_cell_sdst_transport_tardiness_ins21_20260902/contract_comparison_v2_qiming/contract_comparison.md`。当前仅完成该组合变种 1/3 组独立配对，不据此声明跨实例稳定收益。

### Ins22 第二组配对证据

- `Ins22` 包含 20 个作业、26 台物理机器、4 个 cell 和 81 道完整展开的工序，平均候选机数 1.420。自建 `qiming/deepseek-v4-flash` 生成的中性 baseline 经固定 Core 验证为 `(109,510)`，并作为 None/Full 共享冻结起点。
- None 与 Full 均使用 seed 0、3 轮、每轮 3 条方法 lane、solver 60 秒、Worker 300 秒、一次同方向修补和同一固定 evaluator。None 首轮晋升后最终为 `(106,477)`，1/3 轮晋升且合法。
- Full 三轮均由真实 Fast Main 选择 `constructive_search / exact_hybrid / coupled_local_search`，全部绑定 `fjsp_cell_sdst_transport_tardiness_adaptation`。首轮机制均未激活而不晋升；第二轮 exact 经修补得到 `(30,103)`，其 `cp_sat_called=true`、activation、exact execution 与固定 Core 均通过并晋升；第三轮无进一步改进，保留 incumbent。
- Full 最终 `(30,103)`，相对 None 的 makespan 提升 `71.698%`，通过合同 5% 与 Bonus 10%；两侧最终可行率均为 100%，有效迭代率均为 1/3。Full 主控耗时约 2283.4 秒，超过 30 分钟；None 约 1202.2 秒，满足 30 分钟。
- 自动比较器全部协议门禁通过，报告：`outputs/contract_fjsp_cell_sdst_transport_tardiness_ins22_20260902/contract_comparison_v1_qiming/contract_comparison.md`。当前完成该组合变种 2/3 组独立配对，尚不据此声明三实例稳定收益。

### Ins42 第三组配对证据

- `Ins42` 包含 50 个作业、30 台物理机器、6 个 cell 和 175 道完整展开的工序，平均候选机数约 1.526。自建 `qiming/deepseek-v4-flash` 生成的中性 baseline 经一次同方向合法性修补后由固定 Core 验证为 `(131,1050)`，175 道工序完整调度且运输违反为 0。
- 外层严格按 baseline、None、Full 顺序执行；None 与 Full 均使用 seed 0、3 轮、每轮 3 条方法 lane、solver 60 秒、Worker 300 秒、一次同方向修补、Fast Main 和同一冻结 baseline。自动比较器的输入/evaluator、预算、模型、实际 lane 数和共享 baseline 门禁全部通过。
- None 首轮由 tardiness gap-filling 方法晋升到 `(71,385)`，后两轮没有严格改进，最终合法且 1/3 轮晋升。Full 首轮由 `exact_hybrid` 晋升到 `(59,316)`，后两轮保留 incumbent，最终合法且同为 1/3 轮晋升。
- Full 相对 None 的 makespan 提升 `16.901%`，通过合同 5% 与 Bonus 10%；两侧最终可行率均为 100%，有效迭代率均为 1/3，因此质量收益不是由更多晋升轮次或可行率差异造成。
- Full 三轮均实际运行 `coupled_local_search / exact_hybrid / constructive_search`。三轮 exact 均记录 `cp_sat_called=true`，exact execution 与 activation 均通过；第三轮 constructive 也通过 activation。None/Full 共 18 条正式 lane 均有非空事件流，审计未发现 `database is locked`。
- Full 主控耗时约 `1692.0` 秒，None 约 `998.4` 秒，两侧均满足 30 分钟边界。这是本组合变种三组中首个 Full 主控时限通过的实例。
- 自动比较报告：`outputs/contract_fjsp_cell_sdst_transport_tardiness_ins42_20260902/contract_comparison_v1_qiming/contract_comparison.md`。

### 三实例聚合结论

- 聚合使用 Ins21 修复后 v2、Ins22 v1 和 Ins42 v1，三组均固定 `qiming/deepseek-v4-flash`、seed 0、3 轮、每轮 3 lane、solver 60 秒、Worker 300 秒、一次同方向修补和各自共享冻结 baseline；自动比较器判定 `protocol_valid=true`。
- Full 平均 makespan 为 `40.0`，None 为 `74.0`，聚合质量提升 `45.946%`；通过合同 5% 与 Bonus 10%。两侧最终可行率均为 100%。
- Full 与 None 的有效迭代率均为 `44.444%`，领域适配的聚合优势来自晋升候选质量，而不是更高晋升频率。
- Full 平均主控耗时约 `1989.0` 秒，None 约 `1042.1` 秒；None 聚合满足 30 分钟，Full 聚合仍未满足。Ins42 已单例通过，Ins21/Ins22 的 Full 长尾仍是该问题族的主要剩余性能风险。
- 聚合报告：`outputs/contract_fjsp_cell_sdst_transport_tardiness_three_instance_20260902/contract_comparison_v1_qiming/contract_comparison.md`。该结果完成本组合变种 3/3 独立实例验证，并在统一自建模型下证明当前三实例总体净收益；不代表每次随机生成或每个实例都必然达到 Bonus 10%。

## 作业释放时间与机器初始可用时间 FJSP

### DP17a 第一组配对证据

- 基于 Dauzere DP17a 构造单特性实例，仅激活作业释放时间与机器初始可用时间；共享冻结 baseline makespan 为 `2587`。
- Full 与 None 均固定 `qiming/deepseek-v4-flash`、seed 0、3 轮、每轮 3 条真实算法方法 lane、solver 60 秒、Worker 300 秒和一次同方向修补；自动比较器全部协议门禁通过。
- None 最终 makespan 为 `2556`，1/3 轮晋升；修复后的 Full v4 最终为 `2356`，2/3 轮晋升且合法。Full 相对 None 质量提升 `7.825%`，通过合同 5%，未达到 Bonus 10%。
- Full 主控耗时约 `1942.3` 秒，超过 30 分钟；None 约 `965.7` 秒。比较报告：`outputs/contract_fjsp_release_time_dp17a_20260902/contract_comparison_v4_qiming/contract_comparison.md`。

### DP18a 第二组配对证据

- 基于 Dauzere DP18a 构造第二个单特性实例；共享冻结 baseline makespan 为 `2405`，两侧模型、seed、轮次、lane 数及 solver/Worker/修补预算与第一组一致。
- None 最终 makespan 为 `2391`，Full 为 `2341`；两侧均 1/3 轮晋升且最终解合法。Full 相对 None 质量提升 `2.091%`，方向为正，但单例未达到合同 5%。
- Full 主控耗时约 `1697.1` 秒，None 约 `841.1` 秒，两侧均满足 30 分钟边界。比较报告：`outputs/contract_fjsp_release_time_dp18a_20260902/contract_comparison_v1_qiming/contract_comparison.md`。

### Barnes mt10c1 第三组配对与服务端故障复测

- 第三个实例为 Barnes `mt10c1`，包含 10 个作业、11 台机器；共享冻结 baseline makespan 为 `1313`。None 最终为 `1193`，2/3 轮晋升且合法。
- Full v1 的 9 条正式 Worker lane 均在自动重试后仍为零事件流，没有代码改动和 activation，最终回退 baseline。隔离环境审计确认 Qiming provider、`deepseek-v4-flash` 模型与凭据均已正确注入，随后相同环境的最小请求成功返回；客户端未取得 HTTP 状态或服务端内部日志，因此该次仅能归因为临时 Qiming/上游服务故障窗口，作废且不作为算法质量证据。
- Full v2 三轮产生非空事件流并正常结束，最终 makespan 为 `1056`，1/3 轮晋升且合法；相对 None 提升 `11.484%`，同时通过合同 5% 与 Bonus 10%。这证明 v1 的失败不是固定配置、模型名或 API Key 失效。
- Full v2 主控耗时约 `2321.8` 秒，超过 30 分钟；None 约 `846.0` 秒。比较报告：`outputs/contract_fjsp_release_time_barnes_mt10c1_20260902/contract_comparison_v2_qiming/contract_comparison.md`。

### Release-Time 三实例聚合结论

- 聚合使用 DP17a Full v4、DP18a Full v1 与 Barnes mt10c1 Full v2，并分别配对对应 None v1；三组均固定 `qiming/deepseek-v4-flash`、seed 0、3 轮、每轮 3 lane、solver 60 秒、Worker 300 秒、一次同方向修补和各自共享冻结 baseline。
- 自动比较器全部协议门禁通过。Full 平均 makespan 为 `1917.667`，None 为 `2046.667`，聚合质量提升 `6.303%`；通过合同 5%，未达到 Bonus 10%。两侧最终可行率均为 100%。
- Full 与 None 有效迭代率均为 `44.444%`，聚合优势来自晋升质量，不是更多晋升次数。
- Full 平均主控耗时约 `1987.1` 秒，None 约 `884.3` 秒；Full 聚合仍未满足 30 分钟边界，DP18a 单例已满足。固定 Core solver 时间因质量不相当，不申报效率指标。
- 聚合报告：`outputs/contract_fjsp_release_time_three_instance_20260902/contract_comparison_v1_qiming/contract_comparison.md`。该结果完成 Release-Time 单特性 3/3 独立实例验证，并在统一自建模型下证明当前三实例总体达到合同质量提升要求。

## 最大生产间隔 FJSP

### Barnes seti5xx 第一组配对与 lane 协议修复

- `fjsp.barnes.seti5xx.m17j15c3.tlfjsp.seed20260714.txt` 包含 15 个作业、17 台机器、225 道工序和 268 条最大生产间隔约束，其中 232 条为非相邻工序约束；共享冻结 baseline makespan 为 `3499`。
- None 与 Full 均固定 `qiming/deepseek-v4-flash`、seed 0、3 轮、每轮 3 条方法 lane、solver 60 秒、Worker 300 秒和一次同方向修补。None 最终 makespan 为 `2974`，3/3 轮晋升且合法。
- Full v1 因 exact lane 实际使用 `repair_002` 超出约定的一次修补预算而作废；Full v2 暴露 exact activation 计数路径兼容问题；Full v3 虽达到 `1194`，但第 2 轮退化为同一方法族的角色 lane，协议无效。这些中间运行均保留，仅用于定位 Harness 缺陷。
- Harness 已修复 Fast exact lane 的修补预算换算、扁平 exact counter 解析，以及继承 `research_tournament` 但 `candidate_variants` 为空时的真实方法族重建。Full v4 三轮分别运行 `coupled_local_search / exact_hybrid / constructive_search`，没有角色 lane 或 `repair_002`。
- Full v4 首轮 exact 以 `1194` 晋升，后两轮无严格改进并保留 incumbent；最终合法。相对 None 的 makespan 提升 `59.852%`，通过合同 5% 与 Bonus 10%。两侧可行率均为 100%。
- Full 主控耗时约 `1498.4` 秒，None 约 `1031.9` 秒，均满足 30 分钟边界。自动比较器全部协议门禁通过，报告：`outputs/contract_fjsp_max_time_lag_seti5xx_20260903/contract_comparison_v4_qiming/contract_comparison.md`。当前完成该特性 1/3 组独立配对，不据此声明跨实例稳定收益。

### Barnes seti5c12 第二组配对与 exact scope 修复

- `fjsp.barnes.seti5c12.m16j15c2.tlfjsp.seed20260714.txt` 包含 15 个作业、16 台机器、225 道工序和 261 条最大生产间隔约束；共享冻结 baseline makespan 为 `11472`，None v1 最终为 `3245`。
- Full v1 的 exact 候选曾得到 Core 合法、activation 通过且 makespan 为 `1169` 的完整 CP-SAT 解，但方法包把完整模型与局部 trust region 同时列为必做组件，语义审查因此误判缺少 `bounded_pair_trust_region` 并拒绝晋升，Full v1 最终仅为 `8951`。该结果属于 Harness 契约缺陷，不作为正式质量结论。
- 方法包现按实例规模选择 exact scope：`complete_or_bounded` 要求完整 CP-SAT、真实 max-lag 约束、激活证据、合法 incumbent 回退以及完整解提取/复验，但不要求局部 trust region；`local_trust_region` 仍保留 tight-pair 有界信赖域。通用绑定器同步裁剪依赖、competition stage、coupled group 和 checkpoint，Core 与 activation 门槛未放宽。
- Full v2 与 None v1 均固定 `qiming/deepseek-v4-flash`、seed 0、3 轮、每轮 3 条真实方法 lane、solver 60 秒、Worker 300 秒和一次同方向修补。Full 首轮 exact 修补候选真实发布 261 条 max-lag 约束，语义审查确认当前四个 scope 组件全部实现并通过；最终 makespan 为 `1169`，1/3 轮晋升且合法。
- 自动比较器全部协议门禁通过，Full 相对 None 的 makespan 提升 `63.975%`，通过合同 5% 与 Bonus 10%，两侧最终可行率均为 100%。Full 主控耗时约 `1922.9` 秒，超过 30 分钟；None 约 `1063.2` 秒。当前实验按已确认的相同 3 轮口径比较，时间超限单独保留为风险。报告：`outputs/contract_fjsp_max_time_lag_seti5c12_20260903/contract_comparison_v2_qiming/contract_comparison.md`。当前完成该特性 2/3 组独立配对。

### Barnes seti5xyz 第三组配对

- `fjsp.barnes.seti5xyz.m18j15c2.tlfjsp.seed20260714.txt` 的共享 baseline 首次构造违反 maximum-lag 并被 Core 拒绝，唯一一次 baseline 修补生成合法 makespan `9591`；None 与 Full 均从该冻结工作树开始。
- None v1 三轮均运行三个独立通用方法候选，3/3 轮晋升，最终 makespan 为 `7524`。Full v1 三轮均运行 `constructive_search / exact_hybrid / coupled_local_search`，2/3 轮晋升，最终 makespan 为 `1125`；两侧最终排程均合法且 maximum-lag 违反为 0。
- Full 运行中曾出现 semantic reviewer 摘要确认完整实现、但组件行号证据未通过归一化而保守拒绝的中间候选；同轮修补补齐可验证源码证据后通过语义门并晋升。该过程未放宽 Core、activation 或语义完整性要求。
- 自动比较器全部协议门禁通过，Full 相对 None 的 makespan 提升 `85.048%`，通过合同 5% 与 Bonus 10%。Full 主控耗时约 `2064.7` 秒，超过 30 分钟；None 约 `1242.8` 秒。报告：`outputs/contract_fjsp_max_time_lag_seti5xyz_20260903/contract_comparison_v1_qiming/contract_comparison.md`。

### 最大生产间隔三实例聚合结论

- 聚合使用 seti5xx Full v4、seti5c12 Full v2、seti5xyz Full v1，并分别配对对应 None v1；三组均固定 `qiming/deepseek-v4-flash`、seed 0、3 轮、每轮 3 条真实方法 lane、solver 60 秒、Worker 300 秒、一次同方向修补和各自共享冻结 baseline。
- 自动比较器全部协议门禁通过。Full 平均 makespan 为 `1162.667`，None 为 `4581.0`，聚合质量提升 `74.620%`；通过合同 5% 与 Bonus 10%。两侧最终可行率均为 100%。
- Full 有效迭代率为 `44.444%`，None 为 `88.889%`；Full 优势来自 exact/领域方法候选的晋升质量，不是更多晋升次数。由于质量不相当，不申报求解效率指标。
- Full 平均主控耗时约 `1828.7` 秒，None 约 `1112.6` 秒；Full 聚合超过 30 分钟约 28.7 秒。当前三组按已确认的相同轮次口径完成，主控时间边界保留为后续性能优化项。
- 聚合报告：`outputs/contract_fjsp_max_time_lag_three_instance_20260903/contract_comparison_v1_qiming/contract_comparison.md`。该结果完成最大生产间隔单特性 3/3 独立实例验证，并在统一自建模型下证明当前三实例总体达到合同及 Bonus 质量提升要求。

## 最小生产间隔 FJSP

### Barnes seti5x 第一组配对证据

- `fjsp.barnes.seti5x.m16j15c2.mitfjsp.seed20260714.txt` 包含 15 个作业、16 台机器、225 道工序和 48 条 minimum-lag 记录；共享冻结 baseline makespan 为 `1480`。
- Full 与 None 均固定 `qiming/deepseek-v4-flash`、seed 0、3 轮、每轮 3 条真实方法 lane、solver 60 秒、Worker 300 秒、一次同方向修补和同一冻结 baseline。None 最终 makespan 为 `1407`，Full v3 为 `1198`；两侧最终解均合法，`min_time_lag_violations=0`。
- Full 相对 None 质量提升 `14.854%`，通过合同 5% 与 Bonus 10%。Full 主控耗时 `1605.0` 秒，None 为 `1109.4` 秒，均满足 30 分钟边界。
- 比较报告：`outputs/contract_fjsp_min_time_lag_seti5x_20260903/contract_comparison_v3_qiming/contract_comparison.md`。

### Barnes seti5xx 第二组配对证据

- `fjsp.barnes.seti5xx.m17j15c3.mitfjsp.seed20260714.txt` 包含 15 个作业、17 台机器、225 道工序和 41 条 minimum-lag 记录；共享冻结 baseline makespan 为 `10046`。
- None 最终 makespan 为 `1447`，Full 为 `1398`；两侧最终解均合法，`min_time_lag_violations=0`。Full 相对 None 提升 `3.386%`，方向为正，但该单例未达到合同 5%。
- Full 主控耗时 `1414.9` 秒，None 为 `1218.0` 秒，均满足 30 分钟边界。比较报告：`outputs/contract_fjsp_min_time_lag_seti5xx_20260903/contract_comparison_v1_qiming/contract_comparison.md`。

### Barnes seti5xyz 第三组配对与方法合同传递修复

- `fjsp.barnes.seti5xyz.m18j15c2.mitfjsp.seed20260714.txt` 包含 15 个作业、18 台机器、225 道工序和 40 条 minimum-lag 记录；共享冻结 baseline makespan 为 `1401`。None 三轮最终为 `1277`，1/3 轮晋升且合法。
- 初次 Full 正式候选暴露 tournament 早退缺陷：Main 已返回三条候选时，`configure_exact_probe_tournament` 直接保留 `population_memetic`，使其在没有 minimum-lag 专用方法包、`package_id=""` 的情况下挤掉 constructive lane。该运行被中止/作废；Harness 现只在已有方法族均属于变体适配集合时保留 research tournament，否则重编译为 `coupled_local_search / exact_hybrid / constructive_search`。
- Full v2 虽修正 lane，但三轮仍保持 baseline `1401`。对比 None 的成功候选发现，minimum-lag constructive 合同遗漏了一个低风险组合：先按所有候选机器的最早合法空隙开始时刻排序，再以较大剩余最短加工时间和剩余 lag 链打破平局。方法合同已补入该候选；同时修复了方法包 `required_behaviors` 在 Main deliverable 与最终 WorkerAssignment 两层被简化为组件标题的信息丢失。v3/v4/v5 均在发现传递未闭合后及时中止，只保留诊断证据。
- exact 方法合同与技能同时明确当前 OR-Tools 目标 API 为 `model.Minimize(...)`/`model.minimize(...)`，禁止不存在的 `model.add_minimize`。修复后的 Full v6 首轮 exact 得到 Core 合法 makespan `1125`，状态 `OPTIMAL`，`cp_sat_called=true`，发布 22 条正 minimum-lag 约束，模型含 721 个变量、738 个约束和 270 个 interval，4 个搜索 worker，求解约 5.06 秒；exact execution 与 activation 均通过。constructive 同轮得到 `1345` 并报告 3 个完整候选，coupled 报告 341 个 lag-aware move，三条 activation 均通过。
- Full v6 最终 makespan 为 `1125`，1/3 轮晋升；后两轮保持 incumbent。相对 None `1277` 提升 `11.903%`，通过合同 5% 与 Bonus 10%。两侧最终解均合法，Core 记录 40 条 minimum-lag 约束、0 违反。
- Full 主控耗时 `1423.3` 秒，None 为 `1085.0` 秒，均满足 30 分钟边界；自动比较器全部协议门禁通过。正式报告：`outputs/contract_fjsp_min_time_lag_seti5xyz_20260904/contract_comparison_v1_qiming/contract_comparison.md`。

### 最小生产间隔三实例聚合结论

- 聚合使用 seti5x Full v3、seti5xx Full v1 和 seti5xyz Full v6，并分别配对对应 None v1；三组均固定同一自建 DeepSeek 模型、seed 0、3 轮、每轮 3 条真实算法方法 lane、solver 60 秒、Worker 300 秒、一次同方向修补和各自共享冻结 baseline。
- 自动比较器全部协议门禁通过，`protocol_valid=true`。Full 平均 makespan 为 `1240.333`，None 为 `1377.0`，聚合质量提升 `9.925%`；通过合同 5%，距离 Bonus 10% 尚差约 `0.075` 个百分点。两侧最终可行率均为 100%。
- Full 有效迭代率为 `33.333%`，None 为 `44.444%`；Full 优势来自领域 exact/constructive 候选的晋升质量，不是更多晋升轮次。质量不相当，因此不申报求解效率提升。
- Full 平均主控耗时 `1481.1` 秒，None 为 `1137.5` 秒，均满足 30 分钟边界。聚合报告：`outputs/contract_fjsp_min_time_lag_aggregate_20260904/contract_comparison_v1_qiming/contract_comparison.md`。该结果完成 minimum-lag 单特性 3/3 独立实例验证，并在统一模型下证明当前聚合达到合同质量指标。

## 可重入 FJSP

### 2026-09-07 三例测试计划

- 按合同特性表顺序补测独立可重入，不把已有组合回流结果计作单特性完成。
- 预先按展开工序规模选例，不使用历史解分数筛选：seti5xy（15 作业 / 17 机器 / 原始 225 道 / 展开 383 道）、seti5x（15 / 16 / 225 / 381）、seti5cc（15 / 17 / 225 / 379）。
- 固定目标：完整路线展开后最小化 makespan。固定需求与 IO 为 `docs/variants/reentrant/` 两份文档，评价器为 `examples/fjsp_reentrant_evaluator.py`。
- 每例由自建 `qiming/deepseek-v4-flash` 在 None 模式下生成合法共享 baseline，再从该冻结起点运行 None/Full 各 3 轮、每轮 3 个真实方法 lane、seed 0、solver 60 秒、Worker 300 秒、一次同方向修补。外层任务串行。
- 本轮 9 项可重入回归测试通过；运行脚本为 `tmp/run_reentrant_comparison_20260907.py`。

### seti5xy 第一组配对证据

- 共享 baseline 首次生成误解头部第三项，错误限制单作业工序数量；经过框架的一次同方向修补后，383 道展开工序完整合法，baseline makespan 为 17276。失败尝试保留，不计为正式配对。
- None 三轮最终 makespan 为 2302，合法且 2/3 轮晋升；Full 为 1879，合法且 1/3 轮晋升。质量提升 `18.375%`，`protocol_valid=true`，该例达到 Full/None 5% 质量门槛。
- Full/None 主控耗时分别为 1214.2 / 1156.1 秒，均在 30 分钟内；最终 solver 实测耗时为 48.787 / 0.230 秒。质量不等价，不据此声明求解效率提升。
- Full 三轮均由真实 Fast Main 选择耦合局部搜索、精确混合和群体/模因三种方法，加载可重入适配 Skill，没有 Main fallback。胜出 exact 首次存在 `add_no_overlap` 参数错误，框架同轮修补后真实执行 CP-SAT：1210 变量、1226 约束、443 区间、4 workers、状态 `FEASIBLE`，机制和 exact execution 门禁通过。
- 局部与群体 lane 三轮均未通过机制激活门禁。第三轮虽分别输出合法 1864 / 1877，但未晋升；群体诊断为 population_size=1、unique_fingerprints=1、offspring_accepted=0，不能把继承 CP 的波动归因于群体搜索。Worker 生成超时和候选代码可用性分别记录；最终合法不代表全部候选或全部方法成功。
- 报告：`outputs/contract_fjsp_reentrant_seti5xy_20260907/contract_comparison_v1_qiming/contract_comparison.md`。当前完成可重入 1/3 组配对，继续 seti5x，不据单例宣称稳定跨实例收益。

### seti5x 第二组配对证据

- 共享 baseline 完整覆盖 381 道展开工序，makespan 为 2465。None 三轮最终合法 2397，1/3 轮晋升；Full 三轮最终合法 2030，1/3 轮晋升。质量提升 `15.311%`，`protocol_valid=true`，该例达到 Full/None 5% 质量门槛。
- Full/None 主控耗时分别为 1639.5 / 1131.5 秒，均在 30 分钟内；最终 solver 实测耗时为 35.005 / 19.897 秒。质量不等价，不声明效率提升。
- Full 三轮均为真实 Fast Main 的局部搜索、精确混合和群体/模因三方法，未发生 Main fallback。首轮 exact 误用不存在的 `cp_model.Model`，同轮修补后真实求解：1173 变量、1217 约束、410 区间、4 workers、状态 `FEASIBLE`、目标 2030、报告下界 2006，激活与 exact execution 门禁通过。
- 第二轮局部搜索因直接解包失败解码的 `None` 而报错；同轮修补后合法 2048，并通过激活门禁，记录 1739 次邻域评估、11 次换机评估和 7 次接受移动，但未优于 incumbent。第三轮局部与群体均通过激活门禁，合法目标为 2068 / 2053，仍未晋升。不能把“机制执行”与“产生净质量收益”等同。
- None 首轮三条 Worker 均超时且无合格候选，后两轮有可用代码，最后一轮晋升到 2397；生成超时作为运行可靠性风险保留。Full 的精确 API 与局部解码错误也保留，不另加正式轮次或选择性重跑 Full。
- 报告：`outputs/contract_fjsp_reentrant_seti5x_20260907/contract_comparison_v1_qiming/contract_comparison.md`。当前可重入完成 2/3 组，继续 seti5cc，待第三例后生成聚合。

### 2026-09-07 用户要求暂停

- 15:31:25（Asia/Shanghai）已停止本次串行测试脚本及其子进程，并确认没有匹配本次测试的残留进程；保留所有结果、日志和候选文件，没有启动后续特性。
- 前两例配对已完成。第三例 seti5cc 的共享 baseline 为 3316，None 已完成 3 轮，最终合法目标为 2429；Full 已完成 round_000、round_001，已确认 incumbent 为 2609。
- Full 在 round_002 中断，该轮尚无 competition_result.json，最终 manifest 和第三例配对报告尚未生成。局部候选产物不得当作正式完成结果，也不计入三例聚合。
- 恢复时先核对现有恢复接口与中断产物，不直接重启整个脚本（脚本没有断点恢复且输出目录要求不存在），不重跑前两例和第三例 None，不额外增加正式轮次。

### seti5cc 第三组与暂停恢复证据

- 2026-09-07 用户要求继续后，仅恢复第三例 Full；前两例及第三例 None 没有重跑。三条初次候选均复用已保存的 Core 结果，沿用原第三轮 Main 计划，只补 exact 已获批的 `repair_001`，总计仍为 3 轮、每轮 3 条方法 lane。
- 原生恢复接口要求完整 `loop_result.json`，但该文件仅在整次闭环结束时写出，强制暂停后缺失。临时恢复脚本 `tmp/resume_reentrant_seti5cc_20260907.py` 从原计划、竞争结果、Core 与反思产物恢复状态；原 `full_v1_qiming` 保留不动，恢复产物独立保存于 `full_recovered_v1_qiming`。这暴露了框架缺少逐轮持久化及 attempt 级恢复的工程不足，本轮未修改生产框架。
- 中断修补暂停前已耗 85.96 秒，恢复配置剩余 214 秒。当前 OpenCode 实际超时读取运行器属性而非任务书预算，因此本次由仅针对该 Worker 的进程截止器执行剩余预算限制；实际记录 214.672 秒，包含约 0.67 秒终止延迟，不应描述为严格恰好 214 秒。脚本后续已同步运行器超时。既有模型会话无法直接恢复，使用原任务书和未改变的 solver 重新建立会话，该差异保留在 `recovery_audit.json`，不是额外完整 attempt；恢复 Worker 的 `failed_runtime` 来自外部预算截止，不能直接归因为服务端错误。
- 第三例共享 baseline 为 3316；None 三轮最终合法 2429，2/3 轮晋升；Full 最终合法 2609，1/3 轮晋升，质量提升为 **-7.410%**。该例未达到质量指标，不另加轮次或选择性重跑。
- Full 第一轮群体方法晋升至 2609；第二轮 exact 修补后实际调用 CP-SAT，但只优化 24 道工序的局部模型，目标仍为 2609，其 `OPTIMAL` 不能解释为全局最优。第三轮 exact 与局部搜索均未完成有效新实现，群体未产生目标文件改动；恢复后的 exact 修补耗尽剩余预算，Core 仍读到合法 2609，但未通过真实 exact 激活门禁，因此不晋升。
- Full 主控活动时间为 1859.6 秒（约 30.99 分钟），None 为 1009.7 秒。计时按暂停前与恢复后两个活动区间分别减去固定 Core 区间并集，明确排除回宿舍暂停；不宣称该例满足 30 分钟。本组按用户要求使用相同 3 轮比较。
- 历史反思中的压缩计划、简化 cycle 摘要和修补统计已从独立原始产物补齐，未重新调用 Worker 或 evaluator；修正前报告另行保留供审计。单例自动比较 `protocol_valid=true`，但该检查不等于证明会话恢复路径完全相同，也不等于所有候选成功。
- 报告：`outputs/contract_fjsp_reentrant_seti5cc_20260907/contract_comparison_v1_qiming/contract_comparison.md`。

### 可重入三实例聚合结论

| 算例 | 展开工序 | 目标函数 | None | Full | 质量提升 |
| --- | ---: | --- | ---: | ---: | ---: |
| seti5xy | 383 | min makespan | 2302 | 1879 | 18.375% |
| seti5x | 381 | min makespan | 2397 | 2030 | 15.311% |
| seti5cc | 379 | min makespan | 2429 | 2609 | -7.410% |

- 三组固定同一自建 `qiming/deepseek-v4-flash`、共享 baseline、seed 0、各 3 轮、每轮 3 方法 lane 和 solver 60 秒。外层任务串行，三组协议检查均通过；第三例恢复差异如上，原失败及中断尝试全部保留。
- Full 平均 makespan 为 2172.667，None 为 2376.000；按平均目标值计算质量提升 **8.558%**，达到本组三例 Full/None 对比的合同 5% 数值门槛。两侧最终可行率均为 100%，Full 有效迭代率 33.333%，None 为 55.556%。
- Full/None 平均主控活动时间为 1571.1 / 1099.1 秒；第三例 Full 超过 30 分钟，不以均值掩盖单例超时。质量不等价，不申报求解效率提升；三例中仅两例收益为正，不宣称跨实例稳定净收益，也不构成相较直接大模型生成的 Bonus 证据。
- 可重入单特性完成 3/3。聚合报告：`outputs/contract_fjsp_reentrant_three_instance_20260907/contract_comparison_v1_qiming/contract_comparison.md`。本次可重入、合同对比与恢复入口相关回归共 14 项通过，恢复脚本编译及只读预检通过。

## 后续特性队列

| 顺序 | 问题特性 | 目标函数（均最小化） | 状态 |
| ---: | --- | --- | --- |
| 1 | 标准 FJSP | makespan | DP17a、DP15a、DP18a 共 3/3 组完成；聚合质量提升 12.357% |
| 2 | SDST | makespan | oddla13、oddla14、oddla20 共 3/3 组完成；历史聚合提升 7.528%；oddla20 自建 DeepSeek 重跑提升 23.071%，尚未纳入历史聚合 |
| 3 | 工作负荷多目标 FJSP | 严格词典序 (makespan, 最大机器负荷, 总负荷) | Mk01、Mk08、Mk10 共 3/3 组完成；主目标聚合提升 10.274% |
| 4 | 单元运输 + 族 SDST + 回流 + 拖期组合 FJSP | 严格词典序 (makespan, 总拖期) | Ins21、Ins22、Ins42 共 3/3 组完成；主目标聚合提升 45.946%；不替代各特性的独立对比 |
| 5 | 作业释放时间 + 机器初始可用时间 FJSP | makespan | DP17a、DP18a、Barnes mt10c1 共 3/3 组完成；聚合质量提升 6.303% |
| 6 | 最大生产间隔 FJSP | makespan | seti5xx、seti5c12、seti5xyz 共 3/3 组完成；聚合质量提升 74.620% |
| 7 | 最小生产间隔 FJSP | makespan | seti5x、seti5xx、seti5xyz 共 3/3 组完成；聚合质量提升 9.925%；Full/None 主控均满足 30 分钟 |
| 8 | 可重入 FJSP（单特性） | makespan（完整路线展开后） | seti5xy、seti5x、seti5cc 共 3/3 完成；聚合提升 8.558%，第三例退化 7.410%；第三例 Full 主控约 30.99 分钟 |
| 9 | 替代加工路径 | makespan | 待三例配对 |
| 10 | 设备维修时段 | makespan | 待三例配对；不等同机器初始可用时间 |
| 11 | 跨厂转运 | 严格词典序 (makespan, 最大工厂负载, 总能耗) | 待三例配对 |
| 12 | 工件优先级 | 严格词典序 (makespan, 优先工件最大完工时间) | 待三例配对 |
| 13 | 并行组批 | makespan | 待三例配对 |

汇总口径：质量百分比按各组三例的平均 makespan 计算；多目标按声明的严格词典序晋升，主目标提升不代表各次级目标均改善。历史记录及比较器中的 Full/None “Bonus 10%” 标签仅表示数值阈值，不构成合同要求的“相较直接大模型生成”挑战指标证据。当前按相同演进轮次比较，实际主控耗时另行记录；历史模型和修复版本差异仍须在验收复核中说明。

## 2026-09-07 工业 JSON 多特性生成试验（未通过）

### 算例与冻结口径

- 用户确认目标：严格词典序 **最大化窗口内完成重量，再最小化正换型次数**，不是 makespan，也不是加权和。
- 原始算例：`F:/huawei_fjsp_llm/huawei_fjsp_llm/data/generated_small_cases_extended_v2_20260716_tight3840/small_20_random_seed20260716.json`；SHA-256 为 `4c48f84914749aa73351f587f462030d1d3661dff64272b1689c180039081ad9`。原文件没有改写。
- 20 个任务、59 台声明设备、47 台候选中使用的设备；所有候选路线合计 156 道工序，每任务恰选一路后为 138～141 道。3 个任务存在替代路线，全路线有 8 道批处理工序、133 条非空最小 Q-time、34 条非空最大 Q-time；涉及运输、普通机换型与维修窗口。输入总任务重量为 223.2，不是已求得的成绩。
- “全部特性”只指本数据的广泛组合覆盖：全部 Q-time 锚点为 end-start；88 份同目录数据均不含当前时刻之后释放的新任务；重复候选机器不等于强制回路展开；没有合法输出，因此不能声称选中路线和搜索机制已实际覆盖全部特性。
- 需求与 IO：`docs/variants/industrial_json/requirement.md`、`io.md`。固定原工业验证器采用有限批次、精确 family 相等、最小整数成员容量，任务全部排完（允许超过窗口），以绝对时间窗口统计重量，对全排程统计普通机正换型次数。不同于原始工业描述的模糊 family、分数容量及第三个交期目标。本次适配拒绝普通/批处理混用同一机器的输入，避免原验证器的交叉重叠检查缺口。

### 实际运行结果

| 运行 | 目标函数 | 初始生成尝试 | 最终合法解 | 正式演进轮次 | 完整试跑墙钟 |
| --- | --- | ---: | --- | ---: | ---: |
| None v1，接入排障记录 | max 完成重量，随后 min 正换型次数 | 2 | 无 | 0 | 609.578 秒 |
| None v2，修正提示并增加可读输入副本 | 同上 | 2 | 无 | 0 | 618.922 秒 |
| Full v2，从零独立生成 | 同上 | 3（包含自动追加的 exact 救援） | 无 | 0 | 938.188 秒 |

- 两侧模型均显式指定为自建 `qiming/deepseek-v4-flash`，seed 0，候选 solver 60 秒，Worker 每次 300 秒、max_steps 4、配置同方向 repair 1，Main Fast 且无子 Agent。外层任务串行；因初始解均不合法，没有进入原计划的每侧 3 轮、多方法 lane 演进。
- None v2 和 Full v2 的原始数据、共享文档、可读副本、固定验证器及依赖指纹完全一致。Full 自动 exact 救援实际产生第 3 次尝试，不能声称实际生成预算相同。没有共同合法中性起点，不进行三轮质量比较，不填质量/效率提升百分比，不申报任何验收或 Bonus 达标。
- None v1 首次生成了代码但因非法跨厂选择退出；修补后固定验证器实际检查了 20 任务、138 工序输出，报告 **133 项违规**。前 30 条展示错误为时间下界违规，不推断全部错误类别。完整排程条目不等于合法解，非法输出中的重量/换型数据不进入成绩。
- None v2 两次 Worker 均超时，没有创建目标文件；均止于 Core quick-test，未调用固定工业验证器。工具错误分别为 5/6、5/8 次，包含未授权 shell、不可用 grep、越权读取以及尝试读取尚不存在的目标文件。
- Full v2 初次确实加载 `fjsp-solver-foundation-worker` 和 `fjsp-constructive-search-worker`，生成代码后遇到 `NameError: Abram`；第一次 exact 修补加载 foundation/exact Skill，但没有代码改动；第二次 exact 修补增加了 `run_cp_sat_exact`，却删失了仍由 CLI 调用的 `build_schedule`，最终 `NameError: build_schedule`。三次均超时，均未走到固定工业验证器。安装探测确认 OR-Tools 9.15.6755 可用，但 **没有实际 CP-SAT 求解证据**，不能因函数或 Skill 名称宣称 exact 激活。

### 已修复与剩余不足

- 新增 `harness_agent/domains/industrial_json.py` 与独立工业 domain pack，保留明确工业目标/特征，不再默认补入 makespan 或标准 schedule 数组。通用 Worker smoke 支持显式 schema-only，且明确不证明合法性；Core 仍为唯一合法性与目标裁决者。
- 新增 `tools/industrial_json_evaluator.py`，用每次独立缓存调用冻结的原验证器，检查退出码、零违规、完整任务和输入/解/验证器指纹；未改原验证器、未调用旧求解算法、未向 Worker 提供历史解。
- 修正所有 baseline 任务书强制把候选机器解释为 pairs 的错误提示，改为遵循实际 IO 表示；修正独立工业 domain pack 被知识审计标成 SDST 的问题，保留既有标准族判定。
- 原始 JSON 是单行，实际 Worker read 工具发生 2000 字符长行截断。v2 增加仅按根字段分节和缩进的只读副本，并验证反序列化值不变；Worker 已成功分页读取。副本解决可读性，但与原文件共同附加后授权附件增加至约 51.6 万字符，**不是上下文压缩方案**。字符/4 的粗估不是实际计费 token，不能仅凭附件增长断言超时因果。
- 仍未解决：Worker 反复违反已声明的工具边界、生成预算内无法稳定提交可运行代码、修补破坏 CLI 调用链、exact 救援先切换方法但未完成入口接线，以及实际 baseline 尝试数超出表面 repair 配置。这些是生成/编排可靠性缺口，不应归为 solver 的 60 秒搜索性能，也没有证据可全部归因于服务端错误。
- 后续应先锁定工具调用和完整 CLI checkpoint，再在同一固定预算下重做中性初始生成；获得合法起点后才进入多 lane 的相同三轮对比。本次不把“接入文件齐全”写成工业多特性已通过。

### 测试与产物

- 回归覆盖工业画像、Full/None 共同目标/IO/特征与资料隔离、工业/其他独立 pack 审计标识、通用/标准输出 smoke、任务书与领域包加载，以及验证器 fail-closed 行为。
- 最终组合回归：`uv run python -m unittest tests.test_industrial_json_context tests.test_generic_solution_contract tests.test_industrial_json_evaluator tests.test_worker_assignment tests.test_domain_pack tests.test_context_packet`，**116 项通过**（76.283 秒）；相关 Python 文件编译及 `git diff --check` 通过。未跑全仓测试套件。
- 真实原验证器正反例：独立单任务单工序 fixture，20 分钟合法解得到 `valid=true`、重量 7.5、换型 0；改为 19 分钟后得到 `valid=false` 和 duration mismatch。没有使用旧排程或旧求解算法。
- 本次接入使用 `fjsp-variant-domain-pack` 和 `fjsp-agent-generated-solver` 技能，求解器代码始终由平台 Coding Agent 生成，未人工代写。
- 运行入口：`tmp/run_industrial_comparison_20260907.py`；审计汇总入口：`tmp/summarize_industrial_trial_20260907.py`。
- 机器可读汇总：`outputs/contract_industrial_small20_random_20260907/trial_summary.json`，其中 `three_round_quality_comparison_valid=false`，非法解的 `objective_key=null`。
- 三份完整记录保存在同目录的 `baseline_v1_qiming`、`baseline_v2_qiming`、`full_foundation_v2_qiming`；旧失败产物均保留。未改 README，未提交或推送，保留原有未提交修改。

## 2026-09-07 工业 JSON 放宽 Worker 超时重跑

- 版本 `v3timeout900`：仅将每次 Worker 生成/修补上限由 300 秒增至 900 秒；固定 solver 仍为 60 秒，Main/reviewer 超时不变。模型仍为自建 `qiming/deepseek-v4-flash`，原实例、共享需求/IO、可读输入副本、原验证器及其依赖指纹与 v2 一致。未在这次受控重跑中迁入旧 Skill 或修改生产资料路由。
- 中性 None foundation 两次 Worker 均 `completed`，没有超时；首次实际到达原验证器，19 项普通机换型间隔违规（适配器外层错误计数为 22，包含汇总错误，不能混作 22 项约束违规）；修补后原验证器零违规。
- 合法共同初始解：20/20 任务、138 道工序完整排程，5 道批工序采用 5 个单件批；窗口内完成 10 个任务，重量 **101.6**，全排程正换型 **35** 次。所有任务选择路线 `0`，因此不宣称替代路线搜索或多件合批机制已激活。生成及修补完整墙钟为 **756.406 秒**，正式演进轮次为 0。
- 此结果证明该例在 900 秒 Worker 上限下已能自主生成合法起点，不证明单独由超时修改导致成功，也不证明 Skill 净收益。基础生成没有加载 Skill；原始失败试验保留，不用非法输出目标值算提升。
- 共同起点：`outputs/contract_industrial_small20_random_20260907/baseline_v3timeout900_qiming/worker_loop/agent_generated_baseline/repair_001/candidate_worktree`。基础阶段汇总：同试验目录下 `foundation_summary_v3timeout900.json`。
- 后续配对按共同起点、相同 3 轮和每轮最多 3 lane 执行，外层 None/Full 串行。当前这条基础记录不代表配对完成，最终结果另行追加。
- Skill 审计见 `docs/industrial_skill_delivery_audit_20260907.md`：旧工程确有两个工业 Skill，但未注册到当前工业包；现有工业知识卡进入 Main 后被 baseline 资料过滤规则排除在 Worker read_set 外；旧 Full 最后一次修补是新会话，却没有重新获得完整实例和 IO。以上缺口尚未在本次重跑中修改。

### 三轮配对最终结果

| 运行 | 目标函数 | 最终完成重量 | 最终正换型 | 正式轮次 / lane 数 | Worker 尝试 | 晋升轮次 | 完整墙钟 |
| --- | --- | ---: | ---: | --- | ---: | ---: | ---: |
| None | max 窗口完成重量，其次 min 正换型 | 155.29 | 32 | 3 / 9 | 9 | 3 | 1121.579 秒（18.69 分钟） |
| Full | 同上 | 101.60 | 35 | 3 / 9 | 10 | 0 | 3389.781 秒（56.50 分钟） |

- 两侧最终返回解均合法。共同起点源文件及两侧初始副本 SHA-256 都为 `c7287d71bce2d2ae409764751d1e0d51cba91b78bfe7a6d3f1c582b94b7795ab`；原数据、固定评价器、共享文档、模型和声明预算一致，外层串行、每轮 3 lane 并行。Full 第三轮群体 lane 使用了配置内的 1 次修补，因此实际 Worker 尝试为 10，不声称与 None 实际调用次数相同。全部 9 次 Full 初次尝试均超时，最终修补正常完成。
- None 三轮最好结果依次为 `(116.6,34)`、`(135.59,35)`、`(155.29,32)`，均严格词典序晋升。Full 最终返回起点；按返回重量计算相对 None **-34.574%**。上述墙钟包含主控、Coding Agent 与 Core 评测，不冒称纯主控时间，也不报告效率达标。
- Full 最好但未晋升的合法候选为 **127.89 / 37**，来自第三轮群体修补：20/20 任务、139 工序、11 任务在窗口内完成，6 道批工序组成 4 批，原验证器零错误；任务 BA4419 选择路线 `1`。Core 留存的运行 diagnostics 为 9 代、种群规模 8、63 次后代解码、60 次路线变异评价。它优于共同起点，但仍比 None 的完成重量低 **17.644%**，不可替换为最终返回成绩。
- 该合法群体候选 `core_eligible=true`，三个方法/路线计数检查通过，唯一失败的激活项为 `best_metrics.grouped_batch_count > 0`：固定工业验证器不存在此字段，`found=false`，因此 `activation_eligible=false`。这是确认的指标对接缺陷，不能归因于 Skill 无效；也不能用包含单件批的 `batch_group_count` 直接冒充真实合批次数。未通过补改旧报告或放宽门禁追溯性晋升。
- 前两轮 Full 的主要问题是未交付新搜索：零改动、只改说明/imports、最后编辑后出现语法错误并被隔离、写出搜索函数但 CLI 未调用。第三轮群体才进入搜索，随后因非法变量引用在导出前失败，经 Agent 修补才得到上述合法候选。首轮工具执行合计不到 1 秒，数分钟事件空档不能直接证明服务端错误；第二轮还有零事件恢复与会话连续性失败。详见 `docs/industrial_timeout_rerun_audit_20260907.md`。
- 本次 improvement 的工业知识卡实际在 Worker read_set/附件中，通用方法 Skill 也有成功加载事件，与旧 baseline 阶段知识过滤问题不同。旧工程确有工业 Skill，但偏旧 preset/tuner 和流程指导，尚未形成当前自主生成链路可直接使用且语义一致的工业实现技能。本次仍未迁入旧 Skill 或修改生产路由，保持纯超时变更实验。
- 机器可读与 Markdown 报告：`outputs/contract_industrial_small20_random_20260907/pair_v3timeout900_audited.json`、同名 `.md`。其中 `protocol_valid=true` 仅表示记录的配对设置、三轮/lane 和合法返回结果一致，不表示生成的 activation 合同正确、不表示隔离出了 Skill 因果收益；缺失指标观察与未晋升合法结果已单列。旧失败与初版汇总均保留。
- 本组不满足质量提升指标，不申报验收或 Bonus。后续优先修复工业基础资料交付、增量实现的 CLI/自检闭环、激活字段与 Core 证据契约，再做独立版本复验，不能继续仅扩大超时。

### 本次变更与验证

- 仅调整试验 runner 的 Worker 超时参数，给基础/配对汇总脚本增加版本化结果、合法性/共同起点/三轮三 lane 检查和缺失指标说明，并补充审计与台账；没有人工代写求解器，没有在运行中更改生产 Skill/路由/评价器，保留用户现有未提交改动，未回滚任何用户文件。未做无关重构，未提交推送。
- 回归命令同上节，**116 项通过（78.646 秒）**；配对 reporter 自检、三个 runner/reporter 文件的编译及差异检查通过；非正 Worker 超时参数拒绝。未运行全仓套件。静态/单元测试通过不等于已修复本节记录的运行时交付与门禁缺陷。

## 2026-09-07 第一个铝加工 Skill 的增量对照

### 固定协议与接入范围

- 按用户要求，仅适配并接入 `huawei-aluminum-fjsp`，没有接入 `complex-fjsp-stepwise-optimization`。保留调度参数搜索、有限组批等待/成员选择、局部扰动和失败分类；去掉旧 preset/tuner 调用、历史案例解与 Pareto 作为最终排序的假设。当前严格词典序、硬 Q-time、有限批和完整排程约束不变。
- 本次隔离新增 Skill 的增量：**现有 Full 对照组 / 现有 Full + 铝加工 Skill 处理组**，不是重新跑 None。使用相同原始 `small_20_random_seed20260716.json`、共享文档、固定原验证器和中性起点 `c7287d71bce2d2ae409764751d1e0d51cba91b78bfe7a6d3f1c582b94b7795ab`，起点合法值为 101.6 / 35。
- 两组均为自建 `qiming/deepseek-v4-flash`，3 轮、每轮 3 lane、Worker 每次 900 秒、solver 60 秒、seed 0、max_steps 4、每 lane 配置内 repair 1、Main Fast 且无子 Agent。外层串行，同轮 lane 可并行，不强求墙钟相同。
- 在两组开始前共同修复 `grouped_batch_count` 指标桥接：只在原验证器成功、完整合法且指纹稳定后，依据输入中选中工序的批标记及输出机器/等价起止时间，统计成员数大于 1 的批次。排除单件批、普通工序和 solver 自报计数，非法结果不提供该指标。原验证器及两个目标值计算未改。此共同修复不归功于新增 Skill。
- 对照结束后才在工业 domain pack 注册该 Skill，要求 `industrial_json` 特征，不增加方法族。基础生成、改进、修补可获授权；None 与标准 FJSP 隔离。运行期生产代码和 Skill 内容未中途改动，没有人工修改生成求解器或调用旧求解算法。

### 三轮结果

目标函数均为：最大化窗口内完成重量，其次最小化全排程正换型次数。下表目标对为“重量 / 换型”。

| 组别 | 最终合法返回 | 最好已观察合法候选（非返回成绩） | 轮次 / lane | 实际尝试 | 晋升轮次 | Worker 超时 | 完整墙钟 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| Full，对照 `v4aluminumcontrol` | 101.60 / 35 | 147.65 / 32 | 3 / 9 | 12 | 0 | 6 | 4516.579 秒（75.28 分钟） |
| Full + aluminum，处理 `v4aluminumon` | 101.60 / 35 | 147.65 / 32 | 3 / 9 | 12 | 0 | 3 | 3260.906 秒（54.35 分钟） |

- 最终质量提升为 **0%**，两组最好合法候选也打平；不能声称第一个 Skill 已提高本例求解质量。两组最终均合法，不存在最终可行率提升。
- 按全部演进尝试（含修补）统计，Core 合法尝试为对照 7/12、处理 9/12；Worker 正常完成为 6/12、9/12。这是同一算例的相关候选交付统计，不是独立测试集可行满足率，不用于申报合同 8% 指标。
- 处理组完整墙钟减少约 27.80%，但包含主控、Coding Agent、修补和 Core 评测，且生成存在随机性；没有独立测得求解质量相当时的算法求解效率提升，不申报合同 10% 效率或 Bonus。
- 处理组 12 次任务书均授权铝加工 Skill，8 次尝试有可核验的新成功 `skill` 加载事件；首轮三条初次尝试均实际加载。其余 4 次（R0 群体修补、R1 群体、R2 群体、R1 构造修补）均有请求/命令/实际会话 ID 连续且前序已加载的证据，不是 4 次确认的投递失败；会话连续仍不单独证明后续保留和实际运用了该知识。对照组授权和加载均为 0；未加载第二个 Skill。
- 处理组首轮构造修补出现 **1 个多件合批**：138 道工序、4 个批次，原验证器零违规；4 次构造、2 个路线签名的运行计数通过，全部激活检查通过。但目标仍为 101.6 / 35，未严格优于起点，因此选择了该候选仍回滚，没有晋升。该证据证明本次产生了合批，不证明稳定 Skill 净收益。

### 暴露的不足

- 对照组四条改善重量的终态 lane（R0 构造/局部、R1 局部/群体）均合法，选择阶段唯一失败项为 `grouped_batch_count > 0`，且现在都是 `found=true, observed=0`。当前门禁把“必须优化某特性”与“合法且更优”绑定，导致有改进却不能返回；新增一个通用流程 Skill 尚未解决该问题。处理组仅 1/9 终态 lane 通过激活，对照为 0/9。
- 对照 R0 构造器虽有 merge 分支，但加入已有批次时同时要求新工序不早于机器尾部、又不晚于已有批次开始，正时长批次已推进尾部后无法满足，实际合批不可达。R1 群体有真实进化循环，但对扫描的候选机器累计换型计数，内部 110.45 / 95 与 Core 的 110.45 / 36 不一致；搜索还被内部限制为 2 秒、8 代。这些是 Agent 生成实现的缺陷，不是固定 Core 目标变化。
- 对照 R0 构造语义审查称“每次选择相同”，与源码的不同 route/order/policy 入口及 Core 的 5 次构造、2 个路线签名证据冲突；另有语义 JSON 不可解析。当前选择资格不包含 `semantic_eligible`，不能把这条错误审查冒充本次直接阻断原因，但它会污染修订反馈，需后续单独修复。
- 处理组 R2 构造超时且未改目标文件；R2 局部虽定义了组批/搜索函数，实际 `main` 仍只调用基础 `build`，未接入 `_coupled_search`；R2 群体合法值为 131.2 / 35，仅合批门禁失败。Skill 已加载不等于最终编辑形成了可运行的改进调用链。
- 以上缺陷本次只做证据审计，没有在两个实验臂之间修补或放宽门禁。后续应分别处理错误反馈、合法改进的保留策略，以及合批实现的实际可达性，再冻结新版本复验，不应继续只增加 Skill 文本或超时时间。

### 证据与验证

- 两组目录：`outputs/contract_industrial_small20_random_20260907/full_v4aluminumcontrol_qiming`、`full_v4aluminumon_qiming`。配对报告为同根目录的 `aluminum_pair_v4_audited.json` 与 `.md`；全部可比性检查通过，`protocol_valid=true`、`winner=tie`。历史 v3 和失败记录均未覆盖。
- runner 记录了 domain pack 快照、已注册 Skill 指纹、运行代码指纹和自身指纹；除工业 Skill 注册项外，两组公共协议及指纹一致，两组初始副本与冻结源 solver 哈希一致。报告还核对了实际三轮三 lane、模型、尝试完整性、最终合法性、Skill 加载事件和最好合法候选的 Core 证据。
- 新增 `.codex/skills/huawei-aluminum-fjsp/SKILL.md` 和 `tests/test_aluminum_skill_delivery.py`，修改工业 domain pack 注册、工业评价桥及其回归，并扩展试验 runner 指纹/新建两 Full 臂报告脚本。未做无关重构、未改 README、未提交推送，保留用户未提交修改。
- 使用 `skill-creator`、`fjsp-variant-domain-pack`、`fjsp-agent-generated-solver` 进行适配与验证，运行中的求解器仍由 Coding Agent 自主生成。
- 组合回归增加 `tests.test_aluminum_skill_delivery` 后 **128 项通过（87.808 秒）**，包含真实原验证器的合法合批与超容量反例、基础/改进/修补投递和 None/标准隔离。Skill 格式校验、Python 编译、配对报告自检及 `git diff --check` 通过。未运行全仓测试套件，未证明跨算例稳定收益，不申报本组验收达标。

## 2026-09-07 更大工业算例：基础生成阻塞

- 按用户要求改用同目录、同 random/seed 系列的 `small_90_random_seed20260716.json`，没有根据成绩挑选算例。90 个任务、59 台声明设备（候选实际涉及 47 台）、全部路线共 529 道工序，按每任务恰选一条路线为 463--474 道，约为此前 138 道的 3.4 倍。含 11 个替代路线任务、全路线 38 道批工序、428 条最小与 115 条最大 Q-time 界限，并保留换型、日历、释放、运输和跨厂许可等约束。
- 输入 SHA-256：`c8ef053a3f6ed6d6e092114c0ff0b488a17358324234733782d1428b5a9d4de0`。目标仍为先最大化窗口内完成重量、再最小化全排程正换型次数。计划比较当前 Full（包含铝加工 Skill）和通用 None，不是单独隔离铝加工 Skill 的因果实验。
- 冻结设置：自建 `qiming/deepseek-v4-flash`，两侧各 3 轮、每轮 3 方法 lane、Worker 900 秒、solver 60 秒、seed 0、max_steps 4、配置内 repair 1、Main Fast/零子 Agent。外层串行，同轮 lane 允许并行。生产运行代码、Skill、固定评价器和门禁未在本次试验中修改。

| 阶段 | 目标函数 | 实际结果 | 正式演进轮次 | 完整墙钟 |
| --- | --- | --- | ---: | ---: |
| 旧中性基础在大例上重验 `v5large90` | max 完成重量，其次 min 正换型 | 原验证器 4 项错误，包含维护窗口冲突及前驱关联失败 | 0 | 1.594 秒 |
| 大例从零生成 `v5large90fresh` | 同上 | Main 两次均未产出有效方向 JSON，Worker 未启动 | 0 | 未形成完整 manifest，不估算 |
| 相同配置重试 `v5large90retry1` | 同上 | 两次 Worker 均 completed；先因 `_raw_diff` 属性崩溃，修补后原验证器 47 项约束错误 | 0 | 552.671 秒 |
| 显式额外中性修补 `neutral_foundation_extension_001` | 同上 | Worker completed 但零编辑，Core 复验仍为 47 项约束错误 | 0 | 397.375 秒 |
| 正式 None / Full | 同上 | 均未启动，缺少共同合法起点 | 0 / 0 | 不适用 |

- 额外修补仅执行一次 900 秒上限的中性 Worker，使用 retry1 最后候选、真实 Core 反馈、原方向与父任务书，不人工改求解器、不启用知识/Skill。它超出原 foundation 的 repair-1 配置，已单独保存协议和结果，不计入任何一侧正式三轮，也不冒称所有基础尝试预算相同。原失败记录和源 solver 未覆盖，源哈希不变。
- 当前失败代码的 `setup_value` 把工序编号转成整数，而 IO 工序标识为字符串，存在换型字典键失配并退回 0 的具体缺陷；原验证器展示错误包括前工序结束与后工序开始相同、但实际需 30 分钟换型。不能把展示的前 30 条错误类型推断成全部 47 项均同因，也不能把 schema-only smoke 通过当作合法性通过。
- 额外修补事件记录为 1 次成功 read、4 次成功 bash、6 次被权限规则拒绝的 bash，最终没有编辑。Worker 推理将已修复的旧 `_raw_diff` 错误与当前换型错误混淆，并在最后承认没有落实修补。证明修补交付和错误反馈使用仍不可靠，不证明服务端故障，也不是 solver 搜索时间不足的证据。
- **本次状态为 `blocked_foundation`，无 Full/None 质量胜负或提升百分比，不申报验收达标。** 此前 small20 的 None 优于 Full 结果仍保留；更大算例没有扭转该证据。后续应优先修复工具使用闭环、已解决/当前错误分离和 IO 标识一致性，再用同一冻结版本重验。
- 本次仅扩展 `tmp/run_industrial_comparison_20260907.py` 的实例/输出参数与标识，新增一次性续修脚本 `tmp/repair_large_industrial_foundation_20260907.py` 和大例审计脚本 `tmp/summarize_large_industrial_pair_20260907.py`，补充台账。未做无关重构、未新增依赖、未改 README、未提交推送；保留现有未提交改动。
- 全部产物位于 `outputs/contract_industrial_small90_random_20260907`。报告 `large90_v5extended_final_audited.json` / `.md` 保留基础失败、额外修补谱系与中性隔离证据；非法输出不参与评分。使用 `fjsp-variant-domain-pack`、`fjsp-agent-generated-solver` 约束试验边界，铝加工 Skill 仅计划在正式 Full 授权，因未进入该阶段，本次没有新的铝加工 Skill 效果证据。
- 本次重跑相同七个测试模块，**128 项通过（88.584 秒）**；大例报告自测覆盖成功/失败基础、显式续修、协议/哈希篡改及 Skill/读取污染，全部通过。三个试验脚本编译和差异检查通过，未跑全仓套件。基础生成的实际失败未因单元测试通过而解决。

## 2026-09-07 工业大例：Full / None 各自从零三轮

### 协议

- 用户明确不提供基线，并确认两组各自从零生成。仍用 `small_90_random_seed20260716.json`，90 个任务，本次两组最终均选择 463 道工序；原输入、固定工业验证器、IO 和严格词典序目标未变。不读取上一组、旧实验或旧工程的求解器，不共享中性初始算法。
- **各 3 个总轮次**：首次生成、可行性修补、规划失败都占一轮；已有合法且被接受的起点后，剩余轮次才运行最多 3 条方法 lane。无额外 foundation 预算、无同轮追加修补、无自动追加 exact 救援。一组失败不阻止另一组执行。这是本次独立试验入口的计账方式，不冒称旧 Web/CLI 默认轮次语义也已修改。
- 两组模型均为 `qiming/deepseek-v4-flash`，Worker 上限 900 秒、solver 配置 60 秒、seed 0、Main Fast/零子 Agent。外层 None 后 Full 串行，同一优化轮的 lane 并行。请求 edit steps 为 4；现有方法预算规则使 Full 第三轮三条优化 lane 的实际 edit steps 为 6，已记录，没有声称实际步数恒为 4。
- 两组独立初始化目录只有文档/输入及说明，不含 solver 或 Python 算法。平台 ROOT 仅用于可信 Skill/资料加载，实际候选代码从隔离的无求解器 source 生成；后续源码、父任务书、反馈和 incumbent 均限制在本组目录。没有人工代写求解器，没有在两组之间修改生产运行代码、Skill、评价器或晋升门禁。

### 结果

| 组别 | 目标函数 | 最终是否合法 | 窗口内完成重量 | 全排程正换型 | 总轮次 | Worker 次数 | 完整墙钟 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| None | max 完成重量，其次 min 正换型 | 是，90/90 任务、463 工序 | 228.80 | 89 | 3 | 2 | 767.516 秒（12.79 分钟） |
| Full，含铝加工 Skill | 同上 | 是，90/90 任务、463 工序 | 757.43 | 126 | 3 | 5 | 2031.047 秒（33.85 分钟） |

- Full 相对 None 的完成重量提高 **231.04%**；正换型增加 37 次。用户指定重量优先，只有重量相同时才比较换型，因此本例词典序质量由 Full 胜出，不能写成两个目标都改善。
- None 窗口内完成 28 个任务，27 道批工序组成 27 个单件批；Full 完成 66 个任务，27 道批工序组成 12 个批，其中 **7 个为多件批**。多件批数由 Core 在原验证器零错误后从合法输出计算，不依赖 solver 自报。
- None 第一轮 Main 两次响应均未形成合法方向 JSON，编码 Worker 没启动；第二轮从零生成代码后，因本地检查报告缺失工序退出；第三轮 Agent 修补后原验证器零错误，返回 228.8 / 89。三轮完整计入，没有用免费重跑补齐丢失的第一轮。
- Full 第一轮生成了代码，但原验证器报告 33 项约束违规，展示错误主要为普通设备换型间隔；第二轮 Agent 修补后合法并被接受，达到 **627.54 / 84**。该质量已在两次实际 Worker 调用后得到，不是第三轮五次总调用才首次超过 None。
- Full 第三轮从自己的合法起点运行 `coupled_local_search`、`constructive_search`、`population_memetic` 三条方法 lane。局部 lane 因 `coverage BA4427` 自检失败退出；构造 lane 返回原起点，所需搜索计数缺失且无多件批；群体 lane 得到 **757.43 / 126**，Core、激活检查和语义审查均通过，按 `single_run / strict_objective_improvement / required_repeats=1` 晋升。未额外做独立多次复验，不伪称多种子稳定性已验证。
- 群体候选 CLI 中有可达的 `PopulationOptimizer.run` 搜索闭环，Core 留存的实际输出 diagnostics 为 236 代、种群规模 26、769 次路线变异评价、6133 次后代解码。上述计数是 solver 运行诊断，不能替代外部合法性证据；固定验证器已独立确认完整排程零违规。

### 风险与证据

- Full 五次任务书均授权且实际成功加载 `huawei-aluminum-fjsp`；None 的知识上下文、Skill 授权、加载及观察到的领域资料读取均通过隔离审计。第二个复杂 stepwise Skill 未接入。本组比较整个 Full 与 None，不是隔离第一个 Skill 的单因素因果实验。
- 生成可靠性仍有缺口：Full 第三轮三个 Worker **均超时**，当前平台保留并评测了已落盘代码，不能把最终合法晋升反写为三个 Worker 正常完成。两组都有工具调用错误，包括不可用 `grep` 和未授权 shell；None 从零 Main 包仍含读取 incumbent 的标记、目标列表为空但任务描述有目标顺序，首轮只返回读源码的文字。以上保留为后续修复线索，不据此无证据推断服务端故障。
- 同总轮次不等于同实际调用次数或相同墙钟：None 为两次 Worker，Full 为五次；Full 总耗时约为 None 的 2.65 倍。本次不申报求解效率提升，不用完整墙钟冒充纯主控时间。单一算例和 seed 的正向结果也不能推翻此前 small20 的反例，不能证明稳定净收益或整体合同验收已通过。
- 新增 `docs/variants/industrial_json/requirement_zero_start.md`、`tmp/run_industrial_zero_start_20260907.py`、`tmp/test_industrial_zero_start_20260907.py`、`tmp/summarize_industrial_zero_start_20260907.py`，补充本台账；仅改变独立试验入口和计账，不做无关重构或新增依赖，未改 README、未提交推送，保留用户原有未提交修改。
- 产物根目录：`outputs/contract_industrial_small90_zero_start_20260907`；最终审计为 `zero_start_small90_final_audited_v2.json` / `.md`，`protocol_valid=true`、`feasibility_outcome=both_feasible`、`quality_winner=full`，全部审计检查通过。首版报告保留，v2 仅精简展示精度，未变更实验结果。
- 6 项独立起跑状态机测试通过，覆盖初次成功、修补成功、连续失败、Main 失败计轮、优化异常保留 incumbent 和另一组独立执行；报告自测通过。相同七模块回归 **128 项通过（91.520 秒）**；脚本编译与差异检查通过，未跑全仓套件。
- 本次按 `fjsp-agent-generated-solver`、`fjsp-variant-domain-pack` 执行自主生成和变体边界检查，Full 通过任务书实际使用已注册铝加工 Skill。结论是本例两组都能从零写出合法求解器，Full 本次质量更好；不是要求用户预先提供基础算法。

## 2026-09-08 工业多算例：独立从零三轮扩测

### 冻结协议与范围

- 按用户“再多挑几个算例”的要求，在读取成绩前选定同目录的 `small_90_random_seed20260717.json`、`small_80_mixed_seed20260716.json`、`small_90_due_seed20260716.json`，顺序不变。分别有 90/80/90 个任务，全部路线工序数为 536/447/507，按每任务选择一条路线为 464--476 / 375--387 / 435--447 道工序。均有 59 台声明设备、47 台候选设备及 12 个替代路线任务。
- 目标统一为严格词典序：最大化窗口内完成重量，其次最小化全排程正换型次数。`due` 是采样类型，不表示改用交期目标。输入保留批处理、换型、运输、跨厂许可、日历、替代路线与上下界 Q-time；所有释放时间均不晚于当前时间，不声称覆盖未来动态到达。
- Full/None 各自无初始 solver、各 3 个总轮次，生成、修复、规划失败均计轮；合法且被接受后，剩余轮次最多 3 个候选 lane，无免费基础生成、同轮追加修复或额外救援。外层逐例、None 后 Full 串行，组内候选可并行。未向任何组提供历史 solver、对方代码或旧排程。
- 仍用 `qiming/deepseek-v4-flash`、Worker 900 秒、solver 配置 60 秒、seed 0、Main Fast/零子 Agent。六组提前准备并冻结输入、文档、运行代码、领域资料和 Skill 指纹；正式短路径批次中未改生产代码、Skill、评价器或门禁。全部实际 Worker 任务书均为 4 编辑步、900 秒、1 次最多 3 秒 smoke；相同轮次不等于相同 Worker 次数或墙钟。

### 结果

以下分数均为最终被接受且经固定 Core 验证合法的结果；非法结果不计分，重量/换型为原始数值。

| 算例 | 目标函数 | None 重量/换型 | Full 重量/换型 | Full 重量变化 | 总轮次 None/Full | Worker 次数 None/Full | 完整墙钟秒 None/Full |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 90 random，seed20260717 | max 完成重量，其次 min 正换型 | 181.30 / 89 | 155.44 / 91 | -14.26% | 3 / 3 | 5 / 3 | 1215.047 / 1410.000 |
| 80 mixed，seed20260716 | 同上 | 362.00 / 65 | 535.20 / 64 | +47.85% | 3 / 3 | 3 / 3 | 1214.531 / 1388.875 |
| 90 due，seed20260716 | 同上 | 无合法解 | 无合法解 | 不适用 | 3 / 3 | 3 / 3 | 1110.453 / 2249.891 |

- **最终可行满足率双方均为 2/3（66.67%），提升为 0 个百分点。** 分母保留第三例失败，不只统计成功算例。可比较的两例一胜一负；两例完成重量相对变化的非加权均值为 **+16.79%（n=2）**，这是双方均合法子集的条件统计，不是三例整体质量提升，更不能据此证明稳定净收益或申报整体验收通过。
- 第一例双方最终均完整调度 90 任务、464 工序，窗口内完成任务分别为 22/17；30 道批工序组成 30 个单件批。第二例均完整调度 80 任务、375 工序，窗口完成 35/53；24 道批工序组成 24 个单件批。四份合法最终结果均无多件合批，不将质量改善归因于有效合批。
- None 总计 11 次 Worker，全部正常结束；Full 共 9 次，8 次正常结束、1 次超时（第三例第二轮）。正常结束不代表交付代码或通过验证。除第一例 None 最后一轮实际启动 3 个候选 Worker 外，其他组均把三轮用于基础生成与修复。
- Full 铝加工 Skill 授权均为 3/3；成功加载事件分别为 3/3、2/3、3/3。第二例 Full 第三轮为新会话且仅观察到 foundation Skill 加载，不能解释为会话续接或声称每次均实际加载铝加工 Skill。None 的领域上下文、授权、加载和读取隔离审计通过。本组比较完整 Full 优化模块与 None，不是单独隔离铝加工 Skill 的因果实验。
- Full 的完整墙钟三例都更长；本次不申报求解效率提升，也不把墙钟当作纯 Main 演进时间。与前一日正向单例保持分开，不用历史赢家替换本次失败。
- 已有 Core 单次记录中的最终 solver 用时：第一例 None/Full 为 0.220557/0.523464 秒，第二例为 0.597027/0.487920 秒。这是原评测记录，不是新增复验；质量并不相当且样本很少，不据此申报等质量下效率提升。

### 失败机制

- 第一例 None 首轮 Worker 未交付目标文件，第二轮合法；第三轮候选分别合法但更差、最大 Q-time 自检失败、合法持平，保留原解。Full 首轮生成语法错误；第二轮求解器自检报告 47 项运输与 25 项换型错误（不是外部验证器的 72 项错误）。代码在确定候选机器前以空机器名查询运输，且间隙插入未检查后继换型；第三轮修补后合法，但质量低于 None。
- 第二例 None 首轮未交付文件，第二轮外部验证器报告 33 项约束错误。代码把路线和工序标识转成整数，与 setup 表字符串键不匹配，换型查询退回零；第三轮修正后合法。Full 前两轮将机器到时间线的字典当作一条时间线，拿机器名字符与整数相加；第一次修复只改数值转换和增加 traceback，未解决根因，第三轮正确索引机器时间线后合法。
- 第三例 None 三轮实际提交源码哈希相同，两次修复均无有效修改，持续在 `float + dict` 的换型表读取错误处退出。Full 首轮因最大 Q-time 自检失败；第二轮超时且提交源码不变；第三轮正常结束但只增加调试变量/打印，没有修复约束处理，仍未合法。两侧均无过程中最佳合法候选，不能以未验证分数挽救比较结论。
- 主要短板仍是从零交付、自检与反馈修复闭环、IO 标识/类型一致性及运输、换型、Q-time 组合时序。加载更多 Skill 不能替代实际代码修复；本批只扩测和记录，没有在各组间改算法或放宽门禁。

### 基础设施与审计修正

- 第一批使用较长的输出前缀，在第一例 Full 第二轮触发 Windows/Git `Filename too long`，构造与局部 lane 在启动 Worker 前失败。已停止该批进程树，保留全部现场并写入 `outputs/contract_industrial_zero_start_suite_20260908/aborted_run.json`，不纳入算法质量比较。随后实际验证短路径 Git 初始化成功（测试路径最长相关 hook 为 233 字符），以相同冻结程序在 `outputs/izs0908` 将六组全部重新从零执行；没有继承中止批的代码或较好分数。
- 串行入口复用已有独立起跑 runner，调用时显式指定短输出目录：`uv run python tmp/run_industrial_zero_start_suite_20260908.py --output-dir outputs/izs0908`。未来另跑必须使用新的短目录，不覆盖本批，也避免再次增加目录层级；此次没有修改冻结脚本或系统全局 Git 设置来掩盖问题。
- 配对报告新增显式算例名，保持旧默认与严格匹配。审计发现“首轮没有交付 solver、下一轮修复”被错误要求拥有非空 solver 哈希，已仅修正报告：必须是本组前一轮被拒绝且无合法结果的来源、空哈希、无 solver、源不变；唯一允许的 Python 文件是内容与冻结平台源码完全一致的 smoke helper。已有合法起点/优化轮仍必须有匹配哈希；新增反例测试拒绝错误来源、额外 solver、篡改 helper 等。没有修改原始试验证据。
- 完整报告为 `outputs/izs0908/suite_final_audited_v2.json` / `.md`，各算例目录有同名配对报告。三对均 `protocol_valid=true`，总体 `suite_integrity_valid=true`；v1 保留，v2 仅将无胜者显示为 `not applicable`，避免与 None 组混淆。

### 修改与验证

- 新增 `tmp/run_industrial_zero_start_suite_20260908.py`、`tmp/test_industrial_zero_start_suite_20260908.py`、`tmp/summarize_industrial_zero_start_suite_20260908.py`，泛化 `tmp/summarize_industrial_zero_start_20260907.py` 并补本台账。复用原 runner，不重构生产编排，不人工代写 solver，不新增依赖、修改 README、提交或推送；保留用户未提交修改。
- 6 项原独立起跑测试、2 项串行批次测试、配对与总体报告自测通过；七个相关回归模块 **128 项通过（95.766 秒）**。四个本次脚本编译与差异检查通过，未运行全仓测试套件。使用 `fjsp-agent-generated-solver`、`fjsp-variant-domain-pack` 约束自主生成及固定变体验证边界。

## 2026-09-08 上级目录大算例：500 任务从零对照

### 规模核对

用户指出上一级仍有更大算例，本次实际检查 `F:/huawei_fjsp_llm/huawei_fjsp_llm/data`，没有继续局限于此前 80--90 任务子目录。下表工序数按每任务恰选一条完整路线统计，不将所有备选路线都算成必排工序。

| 数据目录 | 算例 | 任务数 | 选定路线工序范围 | 全路线工序数 |
| --- | --- | ---: | ---: | ---: |
| `generated_large_scale_cases_20260725_tight3840` | `small_300_mixed_seed20260725.json` | 300 | 1127--1138 | 1172 |
| 同上 | `small_500_mixed_seed20260725.json` | 500 | 1937--1971 | 2064 |
| 同上 | `small_800_mixed_seed20260725.json` | 800 | 3392--3470 | 3663 |
| 同上 | `small_1000_mixed_seed20260725.json` | 1000 | 4563--4655 | 4901 |
| `generated_xlarge_scale_cases_20260810_tight3840` | `small_1200_mixed_seed20260810.json` | 1200 | 5849--5975 | 6372 |
| 同上 | `small_1500_mixed_seed20260810.json` | 1500 | 7762--7909 | 8377 |
| 同上 | `small_1751_mixed_seed20260810.json` | 1751 | 9642--9797 | 10288 |

本次仅选择 500 任务实例进行 Full/None 实验，其余六例只完成输入画像，不能写成已测。选例依据规模，不依据目标成绩。该例含 59 台声明设备、54 台实际候选设备、31 个替代路线任务；全路线共 119 道批工序、1533 条最小与 347 条最大 Q-time 界限，保留换型、运输、跨厂许可、日历和有限批容量等约束。当前时间为 0、绝对截止为 3840，无未来释放任务；此前小例当前时间为 480，因此不能直接跨例比较原始完成重量。

### 协议与结果

- 两组各自从零、各 3 个总轮次，仍为生成/修复计轮、无额外基础预算、无同轮追加修复。已有合法起点后才允许剩余轮次使用最多三条候选 lane。本次六轮均用于基础生成/修复，没有进入多方法优化。
- 模型均为 `qiming/deepseek-v4-flash`，配置 Worker 900 秒、solver 60 秒、seed 0、Main Fast/零子 Agent，实际任务书均为 4 编辑步、1 次最多 3 秒 smoke。外层 None 后 Full 串行，未提供旧 solver、历史排程或对方代码。没有修改原始大算例、评价器、Skill 或运行算法，未放宽 Q-time。
- 使用短输出根 `outputs/iz500a`；新增入口启动前检查最长候选 Git hook 路径并实际执行 Git 初始化。两组均提前准备空起点，冻结实例、需求/IO、运行代码、领域资料、Skill 及试验脚本。最终计划实例、输入哈希和全部额外资产指纹均一致，配对报告所有审计通过。

| 组别 | 目标函数 | 最终合法结果 | 重量/换型 | 总轮次 | Worker 次数 | completed/timeout | 完整墙钟 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| None | max 窗口完成重量，其次 min 全排程正换型 | 无 | 不计分 | 3 | 3 | 3 / 0 | 1121.796 秒（18.70 分钟） |
| Full，含铝加工 Skill | 同上 | 无 | 不计分 | 3 | 3 | 3 / 0 | 994.547 秒（16.58 分钟） |

- 两组均无最终合法结果、无过程中最佳合法候选。本例最终可行率均为 0/1，不计算质量胜者、质量提升或等质量效率提升。Full 墙钟较短不代表求解效率更高，因为两组都失败。也不能据此证明算例本身不可行。
- Full 三次任务书均授权且实际成功加载 `huawei-aluminum-fjsp`，None 授权和加载均为零，领域资料隔离审计通过。Skill 已投递仍不能保证生成正确代码，更不能证明已实现有效合批。

### 具体失败

- None 第 1 轮生成了代码，但调用 `feasible_route` 漏传必需参数，运行失败。第 2 轮修补后进入构造，无法安排 `('NQ0847', '0', '2')`；第 3 轮增加诊断后仍失败。日志显示候选机器空档晚于当前 Q-time 允许上界，例如可用开始 1011、大于上界 987。说明当前贪心构造没有找到满足约束的时序，不是不可行性证明。
- Full 第 1 轮 `SetupMap` 把嵌套换型字典直接转为浮点数，触发 TypeError；第 2 轮修复后，在 `_apply_qtime_decidable` 对尚未赋值的时间做减法，触发 `NoneType - int`；第 3 轮修复该异常后，主构造和备用构造均返回空值，调用方仍执行 `placed.items()`，触发 AttributeError。
- 六次均为 solver 运行失败，没有取得可交给固定外部验证器确认的完整合法输出；不能把这些异常写成外部验证器的约束错误数。失败过程中也没有证明算法用满 60 秒后仍无法改善，主要缺口是 IO/类型实现、组合时序构造及失败解传播处理。

### 文件与验证

- 新增 `tmp/run_industrial_large_zero_start_20260908.py`、`tmp/test_industrial_large_zero_start_20260908.py`，复用已冻结的三轮 runner 和可指定算例名的配对审计，更新本台账。无生产重构、无人工修改 solver、无新依赖、无 README 修改或提交推送，保留用户现有改动。
- 证据为 `outputs/iz500a/large_case_plan.json`、`pair_result.json` 和 `large500_final_audited.json` / `.md`，审计 `protocol_valid=true`、`feasibility_outcome=neither_feasible`。本次按自主求解器与变种适配技能维持算法自主生成和固定验证边界。
- 2 项大例入口测试、6 项原起跑状态机测试及配对报告自测通过；新增脚本编译与最终差异检查通过。相关七模块回归 **128 项通过（95.233 秒）**，未运行全仓套件；这些平台测试通过不代表生成求解器的实际失败已解决。

## 2026-09-08 300 任务从零对照与工业 Skill 原因核查

### 协议和规模

- 按用户要求改跑同一大例目录的 `small_300_mixed_seed20260725.json`，原文件未裁剪。300 任务、59 台声明设备、53 台候选设备、全路线 1172 工序，选定路线工序范围 1127--1138；10 个替代路线任务，全路线 53 道批工序、862 条最小和 196 条最大 Q-time 界限。当前时间 0、绝对 horizon 3840，没有未来释放；Q-time 锚点均为 `end-start`。
- 保留有限批、换型、日历、运输、跨厂许可、替代路线及硬 Q-time。目标为严格词典序：先 max 窗口内完成重量，再 min 全排程正换型次数。所有任务必须完整调度，不能丢弃截止后任务。
- 复用冻结入口 `tmp/run_industrial_large_zero_start_20260908.py`，显式指定 `--instance` 和短输出目录 `outputs/iz300a`。Full/None 独立空起点、各三轮，初始生成、修复均计轮；无共享或历史 solver、无额外救援。None 后 Full 外层串行，合法起点后的优化轮允许内部最多三个候选并行。
- 模型 `qiming/deepseek-v4-flash`、Worker 900 秒、solver 配置 60 秒、seed 0、Main Fast/零子 Agent；任务书均为 4 编辑步、一次最多 3 秒 smoke。实际 OpenCode 总模型步骤为 12，读取、编辑、检查共用该上限；不能将任务书的 4 编辑步理解成读取结束后另保留四次编辑。

### 最终结果

| 组别 | 目标函数 | 最终合法 | 完成重量 / 正换型 | 总轮次 | Worker 次数 | completed / timeout | 完整墙钟 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| None | max 窗口完成重量，其次 min 全排程正换型 | 是 | 2441.48 / 211 | 3 | 5 | 5 / 0 | 1203.422 秒（20.06 分钟） |
| Full，含铝加工 Skill | 同上 | 否 | 不计分 | 3 | 3 | 3 / 0 | 1866.937 秒（31.12 分钟） |

- 本例最终可行率 None 1/1，Full 0/1；Full 无过程中合法候选。只说明本次独立从零对照 Full 未成功，不计算质量提升、质量胜者或等质量求解效率。不得将 Full 非法结果记作重量零后计算 -100% 质量差。
- None 最终完整合法调度 300 任务、1127 工序，窗口内完成 192 任务；43 道批工序组成 43 个单件批，无多件合批，外部验证错误数为零。不能把其成绩归因于有效合批。原 Core 记录 solver 用时 1.360189 秒、evaluator 用时 0.931910 秒；没有追加求解复验或免费演进。
- None 第一轮生成失败；第二轮修复后合法，重量/换型 1299.60/186；第三轮三个候选分别为低换型（合法持平）、负载平衡（最大 Q-time 自检失败）、重量前瞻（合法 2441.48/211，晋升）。实际候选均为派工构造改进，不能仅因多 lane 就声称执行了 CP 或局部搜索。
- Full 三轮均为基础生成/修复，未进入正式优化；三次工业 Skill 均获授权且实际成功加载。None 领域授权/加载与输入隔离审计通过。比较的是整个 Full 模块与 None，不是单独隔离工业 Skill 的因果实验；同轮次不等于同模型调用次数。

### 失败和修复闭环

- None 首轮固定选择最短加工机器后才检查跨厂，实际在 `ED5620` 第 3 道工序触发未许可跨厂。第二轮改成依据前一道工序的许可过滤机器后再选择，完整结果通过 Core。其代码没有上界 Q-time 链回退，成功只证明该次贪心路径可行，不能证明回溯必需。
- Full 首轮比较机器空档并选最早完工，在 `YT1032 / 0 / 3` 无候选时直接失败，未撤销上游选择。日志没有逐机器拒绝原因，不能唯一归因于 Q-time，更不能认定输入不可行。首轮 Worker 正常交付，完整墙钟 789.344 秒。
- Full 第二轮正常退出但 `changed_files=[]`，源码哈希与首轮一致，同一构造错误重现。实际配置 `agent.algoforge-worker.steps=12`，事件有 12 个 `step_finish`、无编辑事件；最终模型响应承认步骤耗尽，只提出后续建议。完整墙钟 674.422 秒，不是 900 秒超时。读取/诊断消耗了步骤，不能算已尝试其口头建议中的修复算法。
- Full 第三轮有一次编辑，仅去掉冗余释放表达式、调整空机器名运输查询处的就绪计算，没有实现替代排序或链回退。Core 仍报同一工序无法构造，完整墙钟 402.219 秒。这是有差异但未消除故障的修复。
- 首轮与修复中存在未授权命令、目录读取或检索被拒的事件，同时获准资料读取和 Skill 加载成功。保留这些执行开销，不把它们混成服务端错误。八次 Worker 全部正常退出，所有失败候选均为运行失败，不是外部验证器报告了若干约束错误。

### Skill 核查结论和证据

上游工业 Skill 主要是调用已有 preset solver/tuner、在合法基线后调参的工作流；当前适配版保留派工/组批/诊断经验，但未充分展开从零构造与冲突回修步骤。500 Full 最终代码的上界回修留有 no-op，300 Full 则出现完整修复轮无代码修改。这些是知识实现颗粒度及执行闭环的缺口，不能概括为模型永远学不会。

基础任务书禁止 Beam、多起点、局部搜索是一项实际限制，但其对可行率的因果影响尚未验证。300 None 的合法贪心结果说明本例不必然需要复杂搜索；后续可独立验证构造/回退伪代码、更明确的机制检查和为编辑预留模型步骤。本次未改 Skill 或生产算法来挽救成绩。

- 核查文档：`docs/industrial_zero_start_skill_gap_20260908.md`。
- 计划与成绩：`outputs/iz300a/large_case_plan.json`、`pair_result.json`、`large300_final_audited.json` / `.md`。
- 审计 `protocol_valid=true`、`feasibility_outcome=none_only`；原输入哈希一致，额外冻结资产不匹配数为零，运行结束无遗留 Python/OpenCode 进程。
- 本轮只新增原因核查文档并追加台账，运行已有实验/报告脚本；没有修改生产代码、Skill、README、输入、评价器或冻结脚本，没有提交推送、新增依赖或清理用户改动。
- 实验入口 2 项和独立起跑流程 6 项检查通过，配对报告自测及最终协议审计通过；未重复运行全仓或此前 128 项模块回归。文档差异检查通过。平台检查通过不表示 Full 的求解失败已解决。

## 2026-09-08 新 industrial-fjsp-from-scratch Skill 接入与 300 任务复测

### 接入范围与冻结协议

- 按用户“先直接把 skill 接进来试试”接入个人目录的 `industrial-fjsp-from-scratch`，新增项目本地 Skill 主文件和六份 references；六份算法参考内容保持来源版本，仅主文件增加 Harness 权限、当前 IO、固定 Core 评价及预算边界。不携带 solver、旧排程、preset 或可执行脚本。
- 工业领域包注册新 Skill，工业 feature 门禁、`always_include=true`、优先级 860；保留旧 `huawei-aluminum-fjsp`（850）。因此比较的是“原 Full 加新 Skill”与 None，不是新 Skill 单因素消融。None 和标准 FJSP 不授权、不复制该包。
- 独立新批为 `outputs/iz300b`，沿用 `small_300_mixed_seed20260725.json`，规模仍为 300 任务、选定路线 1127--1138 工序。None 后 Full 外层串行，各自从零、各 3 个总轮次，生成/修复均计轮；合法起点后才允许剩余轮次最多三 lane 并行。
- 模型 `qiming/deepseek-v4-flash`，Worker 900 秒、solver 配置 60 秒、seed 0、Main Fast。实际生成/修复任务书为 4 编辑步，优化为 6 编辑步；1 次最多 3 秒 smoke。没有更换模型、放宽评价器或追加免费修复。159 项额外冻结资产及输入哈希核对一致。

### 最终结果

目标严格词典序：最大化绝对 horizon 3840 内完成的成品重量，其次最小化全排程正换型次数。所有任务及选定路线必须完整输出，包括窗口外完成的任务。

| 组别 | 目标函数 | 最终合法结果 | 窗口重量 / 正换型 | 总轮次 | Worker 次数 | completed / timeout | 完整墙钟 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| None | max 窗口重量，再 min 全排程正换型 | 无 | 不计分 | 3 | 3 | 3 / 0 | 1499.907 秒（25.00 分钟） |
| Full，旧工业 Skill + 新 Skill | 同上 | 有，300 任务 / 1127 工序 | 2360.12 / 179 | 3 | 5 | 4 / 1 | 2007.719 秒（33.46 分钟） |

- Full 第二轮取得合法结果，最终保留该轮代码；窗口完成 195 任务，外部错误数为零，43 道批工序为 43 个单件批，无多件合批。该轮原 Core solver 用时 1.350068 秒、evaluator 用时 0.935167 秒，没有追加求解复验。
- 本配对最终可行满足率为 Full 1/1、None 0/1。只说明该例本次 Full 成功；样本为单例单次，不证明稳定净收益。None 无合法结果，因此不计算质量改善百分比或等质量效率提升；33.46 分钟是完整墙钟，不能当作合同的纯 Main 演进时间。
- 历史 `iz300a` 的 None 2441.48 / 211、Full 失败全部保留，不将历史 None 分数替换本批失败，也不把跨批随机结果当新 Skill 的因果证据。

### 各轮与 lane 的实际行为

- None 首轮 Worker 正常结束但未交付 solver，`failed_quick_test`；第二轮交付源码，自检发现机器重叠后退出，`failed_runtime`；第三轮有代码修改并输出排程，自检声称通过，但固定外部验证器发现 215 项约束错误，包括时间下界与未许可跨厂，`failed_validation`。没有最终或过程中合法候选。
- Full 首轮交付源码，但内部自检报告多处换型间隔不足。第二轮只新增 `safe_setup`、重写 `compute_start`：枚举候选空档，检查日历、重叠、前驱和后继换型，取得首个完整合法解。该版本没有 Q-time 上界回推/回退、快照 DFS 或有效合批，不能把成功归因于这些未实现机制。
- Full 第三轮实际同时运行 `coupled_local_search`、`constructive_search`、`population_memetic` 三候选。局部 lane 新增搜索函数，但 `main()` 未调用，仍返回原构造成绩；Core 合法持平，但缺少局部移动/路线切换证据且多件批数为零，激活检查失败。构造 lane 900 秒超时，源码未变，评测继承代码合法持平，但同样未通过激活门禁。群体 lane 生成修改后在批族检查中将列表放入集合，报 `TypeError: unhashable type: 'list'`，未得到合法结果。
- 三条优化候选均无晋升资格，`competition_result.status=no_eligible_candidate`，平台保留第二轮合法 incumbent。不能将两个 Core 合法持平结果记成两条方法已有效执行，也不能声称本轮执行了 CP。

### 新 Skill 投递与使用审计

- Full 共 5 次 Worker，5/5 明确授权、5/5 出现新 Skill 的成功 `skill` 加载事件；旧工业 Skill 同样 5/5 加载。None 3 次均无领域 Skill 授权和加载。
- 核对全部 5 份 Full `opencode_events.jsonl`：没有新 Skill 六份 references 的成功 `read` 记录，也没有 bash 命令引用这些参考文件的记录。成功读取路径见配对审计 JSON 各 attempt 的 `successful_read_paths`。主 Skill 的加载不递归证明参考资料已读；现有证据仅能证明主文件投递成功，不能证明从零构造/冲突回修细节已被消费。
- 投递测试证明七份 Markdown 均实际复制、内容一致、嵌套读取权限可用。本批观察到的缺口是“资料已到达但参考内容未读取，以及生成函数没有接入执行路径”，不能据此认定新参考算法无效，也不能凭本次成功宣称新 Skill 已完全落地。

### 修改、测试与证据

- 本次新增 `.codex/skills/industrial-fjsp-from-scratch/` 七文件和 `tests/test_industrial_from_scratch_skill_delivery.py`，修改工业 `domain_pack.json`，追加本台账及原因文档。未改通用编排、生产求解算法、评价器、输入、README 或原实验脚本，未提交推送，保留所有既有未提交改动。
- 本轮 53 项测试通过：原铝加工投递、领域包、工业上下文三个模块共 40 项；大例入口 2 项；独立起跑流程 6 项；新技能投递 5 项。新增测试覆盖四方法/五阶段授权、工业双门禁、None/标准隔离、七文件复制及嵌套读取权限。另通过 Skill 结构检查、来源 references 一致性检查及差异检查；未运行全仓套件，不将历史 128 项回归算成本轮执行。
- 配对证据：`outputs/iz300b/large300_newskill_final_audited.json` / `.md`，`protocol_valid=true`、`feasibility_outcome=full_only`；原报告中的 Aluminum 列仅指旧技能，新技能授权/加载依据详细 attempt 字段单独核查。
- 最终复核输入、冻结资产和配对协议；Python/OpenCode 实验进程均已退出。本批已结束，不覆盖历史结果、不追加免费轮次。

## 2026-09-08 新 Skill 扩测：500 任务（iz500b）

用户要求继续测试其他算例，事先选择同目录的 500、800 任务两例，外层依次串行。本节记录已结束的 500 任务对照；800 任务另节记录，不将尚未结束的实验记作已测。

- 算例 `generated_large_scale_cases_20260725_tight3840/small_500_mixed_seed20260725.json`，原输入 SHA256 为 `46a890422f3c2ceacd525f1137e6bdd2f35b910cc836ae74d8f91bef3d72286f`。500 任务、59 台声明/54 台候选机器、全路线 2064 工序，选定路线必排 1937--1971 工序；31 个替代路线任务、全路线 119 道批工序、1533 条最小/347 条最大 Q-time。当前时间 0、horizon 3840，无未来释放，Q 锚点均为 end-start。
- 与 `iz300b` 的 159 项额外冻结资产完全一致。仍为新旧工业 Skill 同时启用的 Full 与 None，各自从零、各三总轮次，生成/修复计轮、无免费救援；None 后 Full 串行。模型 `qiming/deepseek-v4-flash`，Worker 900 秒、solver 配置 60 秒、seed 0；六次均为基础生成/修复，任务书 4 编辑步、一次最多 3 秒 smoke，没有进入多 lane 优化。

| 组别 | 目标函数 | 最终合法结果 | 窗口重量 / 正换型 | 总轮次 | Worker 次数 | completed / timeout | 完整墙钟 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| None | max 窗口重量，再 min 全排程正换型 | 无 | 不计分 | 3 | 3 | 3 / 0 | 1448.046 秒（24.13 分钟） |
| Full，含新 Skill | 同上 | 无 | 不计分 | 3 | 3 | 3 / 0 | 982.829 秒（16.38 分钟） |

双方均无最终或过程中合法候选，本例可行率均为 0/1。不计算质量提升、质量胜者或等质量效率提升；Full 墙钟较短不是求解效率优势。旧 `iz500a` 保留为另一历史版本实验，不混入本批统计。

实际失败：

- None 首轮未交付 solver；第二轮已交付并输出排程，但外部验证器报告 4 项最大 Q-time 超界，涉及 NQ1025、NQ1037、NQ1244、NQ1250；第三轮实际修改代码，外部错误增至 193 项，包括未许可跨厂、选定路线工序缺失。完整性要求未放宽，不以部分排程计分。
- Full 三轮均正常退出但 `changed_files=[]`，目标 solver 始终未创建，三次均为 `failed_quick_test`。每份事件记录均有 12 个 `step_finish`、零成功编辑；最终响应均明确承认步数耗尽、未创建文件。这不是 900 秒墙钟超时，也不是已实现构造器运行失败。
- Full 新 Skill 授权 3/3、实际成功加载 3/3。旧工业 Skill 授权 3/3、成功加载 2/3，首轮无其新加载事件。第二、三轮各成功读取新 Skill 的 `parsing-state.md` 与 `feasible-construction.md`；未见 `conflict-repair.md` 等其他参考的成功读取。因此本例不能继续概括为“所有参考都没读”，但读到解析/构造仍未转化为代码交付。
- None 三轮均无领域 Skill 授权/加载，隔离审计通过。本例反复暴露的是读取/诊断消耗总步骤而未预留代码交付，不能据此认定参考构造算法本身已被实现并证伪。

证据为 `outputs/iz500b/large500_newskill_final_audited.json` / `.md`；`protocol_valid=true`、`feasibility_outcome=neither_feasible`，输入与冻结资产复核通过，实验进程退出后才启动下一例。本轮运行已有 runner 和配对审计，只追加测试台账；没有修改生产代码、Skill、README、固定评价器或输入，也没有新增依赖、提交或推送。未重跑上一轮 53 项投递回归或全仓套件，不将历史测试数记成本次执行。

## 2026-09-08 新 Skill 扩测：800 任务（iz800b）及三例汇总

### 800 任务协议与结果

- 按事先确定的顺序，500 两组全部结束并审计后才启动 800；没有同时跑不同算例。输入为 `generated_large_scale_cases_20260725_tight3840/small_800_mixed_seed20260725.json`，SHA256 `d460db30c0fa618ea1acf8635c95287c5c30e2d4ceeb10b4a02c06552b509b04`，启动前与预选时指纹核对一致。
- 800 任务、59 台声明/54 台候选机器、全路线 3663 工序，选定路线必排 3392--3470 工序；70 个替代路线任务、全路线 246 道批工序、2793 条最小/615 条最大 Q-time。当前时间 0、绝对 horizon 3840，没有未来释放，全部 Q 锚点为 end-start。保留日历、换型、运输、跨厂许可、有限批、替代路线等原始约束。
- 三例 `iz300b`、`iz500b`、`iz800b` 的 159 项额外冻结资产完全一致。沿用同一 Full/None 配置、模型 `qiming/deepseek-v4-flash`、seed 0、Worker 900 秒、solver 配置 60 秒和各自从零的三总轮次。800 的六轮均为基础生成/修复，任务书均 4 编辑步、一次最多 3 秒 smoke，未取得合法起点、未进入多 lane 优化。

| 组别 | 目标函数 | 最终合法结果 | 窗口重量 / 正换型 | 总轮次 | Worker 次数 | completed / timeout | 完整墙钟 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| None | max 窗口重量，再 min 全排程正换型 | 无 | 不计分 | 3 | 3 | 3 / 0 | 1188.031 秒（19.80 分钟） |
| Full，含新 Skill | 同上 | 无 | 不计分 | 3 | 3 | 3 / 0 | 1212.625 秒（20.21 分钟） |

双方均无最终或过程中合法候选，本例可行率均为 0/1，不计算质量或效率改善。没有放松全部任务完整调度的约束，不将失败输出的原始指标用于比较。

### 实际失败与 Skill 使用

- None 首轮没有交付 solver；第二轮生成源码和输出，但外部验证器报告 351 项错误，样本包括未许可跨厂、与维修窗口重叠及无合法前驱记录；第三轮有实际修改，仍有 354 项错误，样本包括最大 Q-time 超界及普通工序重叠/换型间隔违规。报告中展示的错误样本可能被截断，不能按样本推算全部错误类别占比，也不把连带错误数当独立根因数。
- Full 第一、二轮均 `changed_files=[]`、无 solver、`failed_quick_test`。第三轮成功写入源码，但 Core 运行在 `earliest_valid_start` 第 352 行访问 `last_key[machine]` 时抛出 `KeyError: '1#冷轧机'`，属于 `failed_runtime`，未交付外部评价器可接受的完整排程。
- 最终代码的 `construct` 第 377--378 行初始化两张空机器状态字典，第 413 行将完整映射传入 `earliest_valid_start`，后者首次访问尚未登记的机器 key。同一接口在第 349/353 行还把 `last_end` 映射当数值比较/相加，这是静态连带缺陷，不能写成该轮已实测的第二个异常。
- 最终代码有基于已排锚点的 Q-time 下/上界过滤，失败时拒绝当前安置；没有上界冲突后撤销前段、重排锚点或快照 DFS。`main` 静态写有三种整批排序重试，但 KeyError 未捕获、会中断首个构造，不能声称三种排序在本次都执行。方法代码存在不等于实际机制执行成功。
- Full 三次均授权并成功加载新 Skill 与旧工业 Skill。首轮成功读取新 Skill 的 `parsing-state.md`、`feasible-construction.md`、`conflict-repair.md`；第二轮无新 Skill reference 的成功读取记录；第三轮读取上述三份及 `sources.md`。None 均无领域 Skill 授权/加载，隔离审计通过。
- Full 三份事件均有 12 个 `step_finish`，前两轮零成功编辑、第三轮两次成功编辑，最终文本均提到步骤上限；三次 Worker 全部正常退出，不是 900 秒超时。本例证明关键参考可访问、部分轮次已实际读取，仍未形成预算内的正确实现，不能继续将所有失败归为“Skill 没送到”或“参考都没读”。

### 当前新 Skill 版本三例汇总

| 算例 | 目标函数 | None 最终合法结果 | Full 最终合法结果 | 每组总轮次 |
| --- | --- | --- | --- | ---: |
| 300 mixed，seed20260725（iz300b） | max 窗口重量，再 min 全排程正换型 | 无 | 2360.12 重量 / 179 换型，完整合法 | 3 |
| 500 mixed，seed20260725（iz500b） | 同上 | 无 | 无 | 3 |
| 800 mixed，seed20260725（iz800b） | 同上 | 无 | 无 | 3 |

Full 最终可行率 1/3（33.33%），None 0/3；这是当前三个已选算例各一次运行的描述统计，不是稳定收益或合同验收通过证明。三例中没有双方同时合法的配对，平均求解质量提升无可用分母，不能记作零改善或把失败分数设为零计算百分比；也没有等质量效率比较依据。历史 `iz300a` 的 None 合法成绩及各旧批全部保留，不用旧赢家替代当前失败。

本轮只按原配置扩测并追加台账，没有修复暴露的问题。主要后续改进方向有明确证据：为实际写代码和检查保留模型步骤，避免多轮反复读取却不交付；统一机器状态接口与内部自检；让 Q-time 冲突回修进入真实执行路径。读取参考、代码出现某个函数名、Worker 正常退出均不能代替完整合法性和机制执行证据。

### 验证和文件

- 800 配对报告：`outputs/iz800b/large800_newskill_final_audited.json` / `.md`；`protocol_valid=true`、`feasibility_outcome=neither_feasible`。500 配对报告同样通过。两例原输入与 159 项冻结资产均无漂移，Python/OpenCode 实验进程已退出。
- 本轮共完成两算例、四组、12 个计入预算的总轮次、12 次 Worker；全部 Worker 正常结束，零 Worker 超时，四组均未进入正式多 lane 优化。完整墙钟含模型、编排、检查和评价时间，不等同合同纯 Main 演进时间。
- 只修改本测试台账并运行既有实验/审计脚本，生成两组新输出目录；保留原未提交改动，未改 README、生产代码、Skill、评价器、输入或测试脚本，未提交推送。最终配对复核及差异检查通过；未新增单元测试或重复执行历史 53 项回归。

## 2026-09-08：800 任务 Full 单臂扩大实现预算（if800a）

状态：已完成，2026-09-08 16:17 启动、21:22:32 结束，共 12 总轮次。仅运行 Full，从独立空源码开始；不重跑 None，
不把旧 None 的较小预算成绩用于公平消融或合同净收益计算。

17:15 左右运行环境中断，第 5 轮未完成 Core；18:56 恢复，从第 6/20 轮继续。
前四轮完整记录保留，第五轮作为中断尝试计预算。其 Agent 已落盘但未同步的修改
原样复制到带来源指纹的新恢复目录，标记未验证，随后由正常计轮的 Worker/Core 检查。
输入、验证器、Skill、框架冻结校验均通过；离线间隙不计为活动实验时间。
恢复工具为 `tmp/resume_industrial_full_budget_20260908.py`，恢复来源和轮次记录在
`outputs/if800a/recoveries/20260908T105636192656Z_next005/`。

| 算例 | 目标函数 | 总轮次上限 | 每次 Worker | 自检预算 | 固定 Core 求解预算 |
| --- | --- | ---: | --- | --- | --- |
| 800 mixed，seed20260725 | max 窗口完成重量，再 min 全排程正换型次数 | 20 | 16 请求编辑步、64 模型步骤、1800 秒 | 最多 3 次，各 60 秒，失败也计数 | 60 秒，seed 0 |

生成、修复、失败均计轮；首次接受完整合法代码后再尝试两轮优化，在总计 20 轮内停止。
优化轮最多三个 Main 选择的方法 lane；外层没有其他并行算例。全部任务和硬约束保持，
固定验证器不变。工业 Worker smoke 只验输出结构，合法性仍由固定 Core 判定。

本次先修复了预算链路：允许任务显式覆盖原 16 步上限；检查按次数和秒数真实执行；
失败消耗次数，运行前清除旧输出；上下文压缩保留可信预算。未向后端或候选源码人工补入求解算法。
生成、修复和优化三阶段的离线任务书/运行配置核对通过。

全量回归首次为 794 项通过、1 项旧“一次 smoke”文案断言失败；更新该断言后，
相关回归 105 项（含 23 子测试）通过，预算压缩与上下文联合回归 33 项通过；
Python 编译与差异检查通过。环境未安装独立 Ruff/Mypy，未新增依赖。

运行证据：`outputs/if800a/full_case_plan.json`、`full/protocol.json`、逐轮
`round_record.json`/Worker 事件/Core 产物。启动脚本为
`tmp/run_industrial_full_budget_20260908.py`，只读审计脚本为
`tmp/audit_industrial_full_budget_20260908.py`。旧实验产物保留；旧 runner 的默认预算仍为
4/900/3，本次对其参数化后的源码指纹仅用于新实验，不改写旧实验记录的历史指纹。

### 19:13 反馈链修复与框架版本切换（尚未完成实验）

第 6 轮未修改源码，两次 schema smoke 通过，但 Core 仍拒绝输出；第 7 轮也未获合法结果。
只读复核发现两个通用反馈缺陷：修复目标混入全部历史失败，已解决的运行异常挤占当前阻塞项；
Core 错误列表先以分号拼接、汇总再按分号拆分，破坏单条错误与实际值/要求值的关联。
例如 `violates lower time bound; start=80, lower=200` 的数值会与标题分离并被前十项截断。
这解释了反馈质量问题，但并不预先证明修复后求解器必然合法。

在生产修改前，原协议声明的 242 项资产已完整归档到
`outputs/if800a/framework_versions/v1_original/` 并逐项核验原始哈希。
原协议 SHA256 为 `e542c8a14d8ebc184e5944b3ea6953df7ec9d9b8dc4ca37cab51a148ecb727b0`。
修复目标改为实际修复源码 anchor 的当前证据，历史失败仍保留；严格执行列表/字典上限。
Worker 提示明确要求追踪输入、解析、检查和输出之间的具体反例，不能用函数存在或 schema
通过推翻 Core 拒绝。初步相关回归 48 项、2 子测试通过，其余修复与验证进行中。

第 8 轮于 19:12:59 开始，生产修改发生于其运行中，因此此轮属于版本切换期间的尝试，
不能标为纯旧版或纯新版冻结结果。保留其原始产物、合法性证据和消耗轮次；后续以显式新版
协议继续，总轮次不清零。输入、固定验证器、目标、模型和候选求解时间预算保持不变。
本次是分版本的 Full 单臂工程修复试验，不能作为单一冻结版本的公平 Full/None 对照。

19:22:44 第 8 轮完成并失败，旧 runner 在下轮前因预期的冻结版本变化退出。
19:50:18 以 `framework_versions/v2_feedback/version_overlay.json` 从第 9/20 轮恢复，
剩 12 轮，未补发隐藏尝试。新版仅改变四个通用框架文件：
`orchestration/loop.py`（当前 anchor 及容量上限）、`core/runner.py`（结构化原始错误与旧记录恢复）、
`context/worker.py`（基础构建重试优先具体 Core 交付要求）、`workers/opencode_worker.py`
（Core 反例优先于 schema/local 自检）。没有修改候选 solver、Skill 或固定评价器。

新版回归：当前反馈及任务书 49 项、2 子测试；OpenCode 与 Worker loop 198 项；
错误证据及 Core runner 20 项、2 子测试全部通过。另通过差异检查、旧资产归档验证、
新协议校验和恢复预检。7 次有 Core 记录的历史尝试已只读重建反馈表示，原 ledger/metrics
哈希、源码路径和目标 key 未改变；来源指纹保存在恢复记录中。第 9 轮实际任务书已核实：
交付要求含同一条错误的 `start=80` 与 `lower=200`，旧异常不再挤占清单，扩大的预算正确。
审计工具已按 v1、第 8 轮混合版本、v2 分段展示；当前归档/历史/新版冻结联合检查通过。

### 首次合法结果：第 10 轮（20:19:30）

第 9 轮有 4 次编辑但仍为 1952 项错误；第 10 轮 Agent 自主修正 Q-time 的路线层读取及
相关检查，3 次编辑后固定 Core 返回 `valid=true`、`error_count=0`、验证器退出码 0。
全部 800 任务、3392 工序合法覆盖；窗口内完成 35 任务，完成重量 424.5，正换型次数 571。
176 道批工序均为单件批，尚未利用组批改善质量。此前非法输出的重量不用于计算提升比例。

合法源码：`outputs/if800a/full/total_round_009/candidate_worktree/examples/agent_generated_industrial_solver.py`；
SHA256 `5007e848963a4f533e270a96f7027d2ed1f09052741b0500f6399f27e477ed11`。
Core 证据在同轮 `harness_smoke/experiments/round_000__small_800_mixed_seed20260725__seed_0/metrics.json`。
本轮活动墙钟 810.453 秒。第 11 轮已进入合法 incumbent 上的优化，计划再尝试两轮后结束，
总 20 轮上限不变。这里只证明本算例本次从零实现最终可行，尚非稳定净收益或公平消融结论。

### 最终结果：21:22:32 完成（12 总轮次）

| 阶段 | 总轮次 | Core 完整合法 | 窗口完成重量 | 全排程正换型 | 结果 |
| --- | ---: | --- | ---: | ---: | --- |
| 首个合法 solver | 10 | 800/800 任务，3392 工序，0 错误 | 424.5 | 571 | 接受 |
| 优化 1：构造搜索胜出 | 11 | 800/800 任务，3470 工序，0 错误 | 424.5 | 570 | 接受；同重量少 1 次换型 |
| 优化 2 | 12 | 构造候选同分；群体候选换型回退至 571 | 424.5 | 570（保留 incumbent） | 无进一步改善 |

两轮均为三个 Main 选择的方法 lane：`coupled_local_search`、`constructive_search`、
`population_memetic`。第 11 轮局部/群体 Worker 超时，构造 Worker 正常结束；第 12 轮
局部 Worker 超时，构造/群体 Worker 正常结束。超时 lane 中出现合法 Core 输出，不等于其
新增方法按时交付或被晋升，不能据此将其记为成功优化。最终使用第 11 轮构造搜索源码，
第 12 轮没有覆盖 incumbent。所有外层任务串行，只有同轮方法 lane 并行。

最终解窗口内完成 35 个任务，全部 800 任务在完整排程中合法；二者不是同一指标。
选中路线共 3470 工序，其中 246 道批工序、212 个批、21 个多人组批。
源码 SHA256：`7d212abec98cbffc45fc5106e7d63c9f269a84b715a7459951f9044245f7b797`。
位置：`outputs/if800a/full/total_round_010/worker_loop/round_000/candidates/family-01-constructive_search/candidate_worktree/examples/agent_generated_industrial_solver.py`。

共 12 个已消耗总轮次，含第 5 轮环境中断；16 次 Worker 调用（12 次正常结束、3 次超时、
1 次环境中断），事件观察到 364 个完成模型步骤、111 次成功编辑工具、30 次实际 smoke。
所有观测到的模型/检查预算与显式配置一致，检查次数/模型步骤上限均未超过；16 编辑步为
请求预算，不将其误写成逐工具硬上限。全部活动轮次合计 10605.400 秒，约 176.76 分钟；
中断轮按产物活动估算，离线间隙排除，含模型、Worker、编排与评测，不等同合同纯 Main 时间。

Skill 审计也保留缺口：第 6 轮和第 11 轮构造 lane 未观察到全部已授权工业 Skill 的成功加载，
不能声称每次 Worker 都完整加载了所有 Skill。基础生成及成功修复阶段有实际工业 Skill
加载记录；最终代码继承本试验自身合法 incumbent，不引入历史 None 或旧 solver。

结论：扩大实现预算并修正反馈链后，本 800 任务算例从零生成了完整合法可交付代码；
但两轮优化没有提高窗口完成重量，只减少一次换型，优化净收益仍弱，不能据此宣称合同验收
达标或 Full 稳定优于 None。残余问题包括模型等待、工具误用、实现与检查时间分配、
工业 Skill 加载完整性，以及候选方法对第一目标的实际改善能力。

最终审计：`tmp/if800a_final_audit_v2.json` / `.md`；归档原资产、历史证据、新版冻结、
预算和最终代码对应固定 Core 合法证据均通过。修复相关 267 项回归、4 子测试通过，
Python 编译与差异检查通过；未新增依赖，未改 README、输入、固定评价器或 Skill，
未人工编辑生成 solver，未提交或推送。实验 Python/OpenCode 进程已结束。

### 质量不足的只读复核

用户指出 424.5 的质量不足后，复核最终接受源码及原 Core 记录：

- `setup_delay` 第 386 行起以机器最后已排普通工序的结束加换型作为下界；
  `_place_ops_rec` 第 309 行附近先施加该下界，再调用空隙查找。因此普通工序不能插入
  最后工序之前的空闲间隙，函数名中的 gap insertion 不代表完整插入机制生效。
- `_place_ops_rec` 第 289 行按加工时长排序机器，并在第 324 行遇到首个完整合法链就返回，
  没有按含排队、运输和换型的实际完成时间比较全部可行机器。
- `batch_merge` 第 698 行起只移动批成员的时间，不对其后续工序做整体重解码，
  组批所节省的时间不一定转化为任务在窗口内完成。
- `solve` 第 782 行起固定试 6 组构造；原输出确认 6 候选均合法、0 失败。
  `--time-limit-sec` 和 `--seed` 只被 CLI 解析，未用于该求解路径的搜索时限/随机搜索。
  原 Core ledger 记录 solver 27.605 秒、evaluator 3.925 秒，不能把 60 秒预算写成
  已被持续优化充分使用。不能因计数非零而声称方法已具备有效目标改进能力。

只读分析脚本 `tmp/diagnose_if800a_quality_bound.py` 对每任务每路线建立乐观事件时间差约束：
保留释放、最短可选加工时间、precedence 和 Q-time 上下界；放松机器竞争、日历、换型、
运输及机器资格耦合。713 个任务在此放松模型下有可能于 3840 内完成，其重量合计
8641.65；真实排程仅完成 35 个、424.5，另 678 个未被该乐观分析排除。
8641.65 仅为放松上界，不是可实现分数，不据此计算真实最优差距；它不能证明全部
713 任务可同时完成。没有额外运行 solver/Core、没有改动既有实验或生成源码。

后续质量改进应聚焦：窗口内任务组合与瓶颈收益、比较机器实际完工时间、保持两侧换型
合法的空隙插入、组批后完整重解码，并以第一目标的真实改善验证效果。此轮仅完成诊断。

## 2026-09-09：缩小工业规模的 Full 单臂测试（20例完成，50例用户暂停）

用户要求换小算例。事先选定同目录 `generated_small_cases_extended_v2_20260716_tight3840`
的 20/50 mixed、seed20260716，依次执行，均独立从零，不继承 800 算例 solver。
保持当前框架及上一轮预算/停止配置，以观察规模影响；此前诊断出的 Main 任务过弱、
Skill 加载与方法覆盖检查缺口尚未修复，不能将本轮写为这些问题的修复验证。

| 算例 | 必排工序范围 | 目标函数 | 输入总重量 | 输出目录 |
| --- | --- | --- | ---: | --- |
| 20 mixed | 80–81 | max 窗口完成重量，再 min 全排程正换型 | 232.14 | `outputs/if20a` |
| 50 mixed | 209–214 | 同上 | 557.37 | `outputs/if50a` |

输入 SHA256 分别为 `ff7760e43afb39bbeb71781d107846788a36e1dd8250d34423b8934fdd843a43`
和 `c493adbb65657fd2f1ef4e80e1b59db1dc2f908a93cb4e32a45e4834b8c7ac0e`。
都保留 Q-time、换型、运输、日历、替代路线和有限批；current_time=480，绝对 horizon=3840，
实际剩余窗口 3360 分钟，与 800 算例 current_time=0 不同，不能只按绝对重量跨规模比较。
20 例有 65 条最小/21 条最大 Q-time；50 例有 184/55 条。

每次 Worker 16 请求编辑步、64 模型步骤、1800 秒，3 次各 60 秒 schema smoke；固定 Core
solver 60 秒、seed0，模型 `qiming/deepseek-v4-flash`。每例总轮次上限20，首次接受合法解后
尝试两轮优化停止；合法前生成/修复及失败均计轮。只运行 Full，方法 lane 同轮最多3个。
20 例于 12:52:21 通过预算预检并启动首轮；50 例需等其完成后启动。

只读时间约束放松分析（不执行 solver/Core）给出窗口重量上界：20例 232.14（20任务），
50例 534.37（48任务）。这只是忽略资源竞争等约束的乐观上界，不是承诺可达分数。
最终同时报告合法任务覆盖、窗口完成任务/重量、输入重量占比、换型、轮次及逐轮改善。

20例首轮（13:09:50结束）固定 Core 已确认合法：20/20任务、80工序、零错误；
窗口内17任务，重量215.84，占输入总重量232.14的92.98%，全排程正换型9次。
本轮 Worker 完成30模型步骤、7次编辑和3次 schema smoke，两份工业 Skill 有实际加载记录。
首轮约17.5分钟，随后按预设进入两轮优化；此处是初始成绩，不代表最终优化结果。
证据：`outputs/if20a/full/total_round_000/worker_loop/agent_generated_baseline/harness_smoke/experiments/round_000__small_20_mixed_seed20260716__seed_0/metrics.json`。

20例第2轮（13:42:21结束）三条方法 lane 均通过固定 Core：构造搜索与群体方法候选均为
232.14/10（窗口重量/正换型），局部搜索为215.84/8。前两者实现20/20任务全部在窗口内
完成，第一目标相对首轮提高7.55%，达到该输入总重量上界；换型10并未证明最优。
第3轮为预设最后一轮优化，仍从本试验被接受的 incumbent 出发。

注意：第2轮 `competition_result.json` 为 `no_eligible_candidate`，未晋升任何候选。
232.14/10是固定验证合法的最佳候选成绩，**不是框架最终接受成绩**；正式 incumbent 仍为
215.84/9。构造候选仅因必需 `pbpm_grouped_batch_count > 0` 未满足被挡住（实际为0）；
其10次构造和2个路线配置检查通过。必须区分实际解质量、机制符合性与晋升结果。

20例于14:13:14结束，共3轮、7次 Worker 调用、194个完成模型步骤；6次优化 Worker
均为timeout。最后一轮构造候选合法（221.24/10），另两条 lane 因TypeError未完成求解。
最终接受仍为首轮215.84/9，即正式接受重量净提升0%；全程最佳合法候选232.14/10，
相对首轮重量提高7.55%，但未通过机制门槛，不能记为自动演进成功晋升。
从12:52:21预检启动到14:13:14结束约80.9分钟，是整体墙钟时间，不是合同主控净时间。
最终源码SHA256：`4e9542eaf31d34b8e7ecaaad7116a86e9a833e60a0a5cf61326d0036e0f3a2c1`。
审计 `tmp/if20a_final_audit_20260909.json` / `.md` 确认冻结资产不变、Worker预算一致、
最终源码与固定Core合法证据一致。没有人工修改被测solver。

50例于14:16:24预算预检通过、14:16:25启动首轮；输出`outputs/if50a`，独立从零，
20例进程已结束后才启动，沿用同一预算、模型、框架版本和停止规则。

50例首轮14:29:27结束，Worker正常交付但Core运行失败：solver自己的检查发现多个最小
Q-time违例并拒绝写出排程。第2轮14:39:51结束，已能输出但固定验证器退出码1且没有报告。
不能把缺少`setup_count_positive`等指标误解释成换型/完整性违例；审计脚本的关键词分类
在此不可靠。只读检查该轮`solution.json`发现`task[J].process_path`下面额外嵌套路线ID，
与IO所需的直接工序映射不符；固定验证器`load_solution_records`直接读取工序时间字段，
因此该输出结构无法被正常读取。桥接器未保留子验证器stderr，不能伪称已有完整异常栈。
第3轮已自动进入修复；尚无正式合法成绩。未手工修改生成代码或补跑未计轮的Core。

用户要求停止后，已终止50例runner及其cmd/OpenCode子进程，核验原PID均退出。
50例共完成6轮，第7轮中断，尚无接受合法解；第2至第6轮反复出现固定验证器退出码1、
无报告，已检查的输出持续将路线ID错误嵌套到`process_path`下。
暂停审计：`tmp/if50a_paused_audit_20260909.json` / `.md`；审计状态incomplete，不能记为
完成20轮或正常结束。7次已启动Worker、96个完成模型步骤，观察预算一致。

只读根因核查：`harness_agent/domains/industrial_json.py`声明工序字段，但
`harness_agent/orchestration/cycle.py`的schema-only smoke仅检查顶层对象；
`tools/industrial_json_evaluator.py`捕获子验证器输出后未把结构化异常保留下来，导致
Worker只收到泛化失败。第6轮solver的`build_output`注释声称直接工序映射，实际代码仍
写入`process_path[route][seq]`。这是接口门禁与诊断反馈不足，不是扩大实现次数便已解决。

用户明确：不要求最终排程必须多件合批，不合适时可保持单件批。硬门槛来自
`harness_agent/agents/main.py:_pbpm_activation_checks`的`grouped_batch_count > 0`且
`required=True`，由batching特征自动附加，并非合同或固定工业验证器要求。工业IO及
铝加工Skill均承认singleton合法。此门槛把能力验证混同为最终解形态要求，应视为待修
框架缺陷；本次仅诊断及整理证据，未修改运行规则或追溯性晋升232.14候选。

## 2026-09-09 恢复未完成单特性测试：替代加工路径

用户重新授权继续未完成的单特性测试。历史报告保留，不覆盖历史结果。
顺序：替代加工路径 → 设备维修时段 → 跨厂转运 → 工件优先级 → 并行组批。
本批先执行替代加工路径三例 seti5cc、seti5xxx、seti5xyz，均15工件、225道候选工序，
分别17、18、18台机器；按路线选择，最少排程工序数分别206、179、192。
目标为 min makespan；缺少该变体已核实的LB/UB，不能借用原标准问题的界值。

协议沿用单特性历史组：None模式Agent生成共同合法baseline，Full/None从该同一源码
分别演进3轮，单轮3个竞争方法lane，seed0，固定Core单次60秒；Worker每次300秒、
4步、同轮修补1次，Main fast模式、子Agent上限0。Main/Worker均固定
`qiming/deepseek-v4-flash`。各算例、各臂严格串行，lane内部允许并发。
运行脚本：`tmp/run_alternative_path_comparison_20260909.py`。
输出：`outputs/contract_fjsp_alternative_path_{case}_20260909/`；
三例汇总目标路径：`outputs/contract_fjsp_alternative_path_three_instance_20260909/`。
启动前替代路径解析/验证9项回归通过。当前状态：已启动，尚未形成配对结果；
不能将共同baseline的合法性视作Full收益，也不根据质量优劣挑选重跑结果。

首例seti5cc完成：None=1218，Full=1288，质量提升=-5.747%。比较器协议检查通过，
但Full第3轮三个lane均无事件流，自动重试耗尽且未改变源码；前两轮另有实现超时。
因此该对须附运行异常说明，不能仅以比较器protocol_valid视作无故障的优化净收益证据。
None三轮九个候选均Core合法；Full最终保留合法1288。没有增加Full轮次或切换模型。
配对报告：`outputs/contract_fjsp_alternative_path_seti5cc_20260909/contract_comparison_v1_qiming/contract_comparison.md`。
后续seti5xxx的Main及Worker已有正常事件，Worker初始候选Core合法1523，尚待基线结算。
直连网关诊断返回HTTP403 HTML，但OpenCode随后可返回内容，尚不能判定为模型全面中断。

第二例seti5xxx完成：共同合法baseline1419，None=1193，Full=1229，质量提升=-3.018%。
双方均完成3轮且最终合法；Full多次实现超时及精确混合修补未取得改进。
配对报告：`outputs/contract_fjsp_alternative_path_seti5xxx_20260909/contract_comparison_v1_qiming/contract_comparison.md`。
第三例seti5xyz已按预选队列启动共同baseline，前两例负面结果不覆盖、不选优重跑。

三例现已全部完成：seti5xyz共同baseline1730，None=1299、Full=1466（退化12.856%）。
聚合None均值1236.667、Full均值1327.667，Full平均质量提升=-7.358%，双方最终3/3合法。
协议检查通过，但全部合同提升门槛均未通过；Full最终timeout lane为23/27，None为10/27，
有源码改动lane分别17/27与24/27，实际晋升轮数分别3/9与6/9。
Full首例3个零事件lane，另两例无最终零事件lane，但实现超时仍频繁。
主控扣除Core平均Full29.988分钟、None17.444分钟；不满足等质量前提，不主张效率优势。
完整解释与原始数据索引：`docs/deliverables/alternative_path_test_summary_20260909.md`；
指纹、预算、全部lane状态审计：`docs/deliverables/alternative_path_test_audit_20260909.json`。
三例聚合：`outputs/contract_fjsp_alternative_path_three_instance_20260909/contract_comparison_v1_qiming/`。
本批已正常退出；后续设备维修、跨厂、优先级、组批未在本批启动。保留原Word报告和历史证据包。

### 2026-09-10 替代加工路径 Skill/知识库修订后原三例复测

v1的三例退化结果完整保留。按用户要求修订替代路线Skill、搜索知识卡和三个方法包，
去掉未授权foundation请求、未来阶段混入当前交付与强制重写表示等负担；明确gap/剩余工作量、
路线切换完整重解码、CP路线条件模型与真实CLI接线。通用runtime同时修复smoke输出只读授权。
固定评价器及solver算法不由主Agent手改；两臂都使用修后runtime，从原v1共同baseline重新演进。

| 算例 | 目标 | None | Full | Full质量提升 |
|---|---|---:|---:|---:|
| seti5cc | min makespan | 1470 | 1029 | 30.000% |
| seti5xxx | min makespan | 1193 | 989 | 17.100% |
| seti5xyz | min makespan | 1175 | 1092 | 7.064% |
| 均值 | min makespan | 1279.333 | 1036.667 | 18.968% |

双方最终3/3合法，共同baseline源码SHA256、输入/评价器指纹、模型、实际三轮三lane及预算检查通过。
每候选CLI48秒、Core进程硬限60秒；Worker300秒/max_steps4/同轮repair1，seed0，自建DeepSeek。
不同臂和算例串行，单轮lane并发。质量5%数值门槛通过；非直接大模型对照，不主张Bonus。
Full/None最终超时24/27和12/27、有源码改动23/27和25/27、晋升6/9和8/9，Full1条最终零事件。
主控扣Core平均Full30.023分钟、None17.079分钟；Full两例超过30分钟，本批按等三轮，
不能主张等质量效率改善，也不能称跨随机种子稳定收益或整体交付可靠性已达标。

复测额外揭示smoke_solution单行JSON截断，Full读不到行尾diagnostics并反复尝试被禁提取命令。
该输出形态修复在v2三例全部结束后另行应用与验证，不归因为v2质量收益来源。
原生崩溃0xC000070A与零事件问题未确证解决，原始失败完整保留。

结果与修订解释：`docs/deliverables/alternative_path_skillfix_20260909.md`。
审计：`docs/deliverables/alternative_path_skillfix_audit_20260909.json`。
聚合：`outputs/contract_fjsp_alternative_path_three_instance_20260909/v2_skillfix_qiming/contract_comparison/`。
逐例：原目录下`none_v2_skillfix_qiming`及`full_v2_skillfix_qiming`；包含manifest、候选、repair及Core原始数据。
版本哈希与协议：上述聚合父目录`protocol.json`。后续四个单特性未在本轮启动。

### 2026-09-10 替代加工路径三个新算例完成（12:20:51）

按用户“跑别的算例”请求，预选未测seti5xy、seti5xx、seti5x，均15工件、225候选工序；
机器分别17/17/16，最少所选工序195/191/196。固定目标min makespan，没有可核实变体LB/UB。
双方自建qiming/deepseek-v4-flash，各三轮×三真实方法lane；不同臂/算例串行，内部lane并发。
每例由None模式Agent新生成共同合法baseline，两臂共享冻结源码；不复用None演进后结果给Full。
本批在前轮修订基础上启用双方共同的stdout自检摘要，运行中不修改生产版本。

| 算例 | 目标函数 | 共同baseline | None | Full | Full质量提升 |
|---|---|---:|---:|---:|---:|
| seti5xy | min makespan | 1387 | 1120 | 947 | 15.446% |
| seti5xx | min makespan | 1407 | 1407 | 1017 | 27.719% |
| seti5x | min makespan | 7509 | 6697 | 997 | 85.113% |
| 三例均值 | min makespan | — | 3074.667 | 987.000 | 67.899% |

逐例百分比平均42.759%；比较器67.899%是均值之比，受到第三例弱None高分主导。
双方最终3/3合法、合法率提升0个百分点；三例质量5%数值门槛通过，但不等于稳定净收益或整体验收。
最终超时Full17/27、None19/27，零事件5/27与8/27，改码18/27与17/27，晋升5/9与3/9。
平均主控扣Core为Full26.416分钟、None18.905分钟；Full第三例30.295分钟，不能称全部30分钟内完成。
每候选CLI48秒/Core60秒、Worker300秒、请求max_steps4、同轮repair1、seed0。
实际任务书None为4编辑步，Full局部/构造6步、CP8步；比较器只检查请求预算一致。
因此是等轮次端到端优化模块比较，不是等内部计算量的纯Skill消融，不主张效率提升或Bonus。

seti5xx首次baseline零事件失败后在独立retry1目录恢复一次，失败目录保留。
该例None有6/9最终零事件、Full3/9，不能忽视故障对质量比较的影响。
用户追加提交源码期间，825项unittest于10:46:16至约11:01:06执行，与第二例None末轮/Full首轮重叠；
存在主机资源干扰，不能将第二例描述为完全隔离负载的性能对照。第一例已结束，第三例随后开始。

seti5x基线/None解析只保留最后候选机器，整工件尾插；None最终搜索还截到约2.4秒。
Full首轮通过工序级派工/空隙插入降至1276，第二轮恢复全机器候选并实际执行CP后997合法晋升。
997为可行解、非最优性证明；最终代码仍有极早deadline的finalize前向调用与CP剩余预算边界风险。
未手改任何计分solver、追加Core或给运行中的Agent额外提示，原始候选与负面证据保留。

最终审计含共同baseline哈希、输入/评价器指纹、三轮三lane、模型、实际任务书预算及自检摘要事件。
报告：`docs/deliverables/alternative_path_newcases_20260910.md`；
审计：`docs/deliverables/alternative_path_newcases_audit_20260910.json`；
原始逐例：`outputs/contract_fjsp_alternative_path_{seti5xy,seti5xx,seti5x}_20260910/`；
聚合：`outputs/contract_fjsp_alternative_path_newcases_20260910/contract_comparison_v1_summary_qiming/`；
协议在该聚合父目录`protocol.json`。活动runner正常退出，当前无未完成本批计分臂。

按用户追加要求，68个框架源码/Skill/知识/测试/接口文档文件已提交并推送GitHub
`codex/fjsp-variants-snapshot-20260817`：`354cbf1f0a56ed62d7b8a5d2e49c804205fe356d`。
825项回归、compileall、静态格式/敏感信息检查通过；实验报告、exports、运行输出及本地配置不在提交中。
这是当前源代码快照，工业单件批晋升、浅层schema smoke等已知缺口尚未修复，不是工业完整验收交付。

### 2026-09-10 下一个单特性：设备维修时段（启动记录）

用户授权继续下一个，按表顺序启动设备维修时段三例。预选工序数最大的FFCR19/20/18：
20工件，机器10/15/11，工序240/240/225，维修窗口63/97/47，目标min makespan。
原目录20例均解析为正确问题族，3项维修窗口半开边界回归通过；技能/方法包授权只读复核无阻塞。
每例新None合法baseline→None三轮→Full三轮，单轮三真实方法lane可并发，任务串行。
沿用自建DeepSeek、CLI48/Core60秒、Worker300秒、请求4步、repair1、seed0、Main fast/subagents0。
实际方法族编辑步数与选择以任务书审计，非等内部计算量；不在实验期间并跑全量回归。
当前无新结果，不把启动或Skill可匹配当作性能通过。
报告：`docs/deliverables/machine_availability_test_20260910.md`；
队列：`tmp/run_machine_availability_comparison_20260910.py`；
协议/聚合：`outputs/contract_fjsp_machine_availability_three_instance_20260910/`。

### 2026-09-10 设备维修时段三例完成

15:25:12队列正常退出（码0），六臂完成各3轮×3方法lane；共同基线哈希、输入/文档/评价器指纹、
模型、最终合法性和54个lane位置核验通过。冻结253项生产文件，整个批次无其他计分/回归任务并跑。

| 算例 | 目标函数 | baseline | None | Full | Full质量提升 |
|---|---|---:|---:|---:|---:|
| FFCR19 | min makespan | 1625 | 1009 | 997 | 1.189% |
| FFCR20 | min makespan | 1444 | 1386 | 1055 | 23.882% |
| FFCR18 | min makespan | 1257 | 1141 | 1179 | -3.330% |
| 三例均值 | min makespan | — | 1178.667 | 1077.000 | 8.626% |

逐例百分比平均7.247%，均值之比8.626%，均超过5%质量数值门槛；双方最终合法率3/3，维修违例0，
合法率提升0个百分点。两例改善、一例退步，不能宣称稳定净收益、整个合同验收完成或直接大模型Bonus。
None/Full最终超时9/27与16/27、零事件2/27与0/27、改码24/27与22/27、晋升6/9与5/9。
平均主控扣Core15.845/26.458分钟；Full逐例27.987/24.482/26.904分钟，不含共同基线生成。
请求4步、实际None4/Full普通4及CP8步，等外层轮次不等内部计算量；不主张效率提升。

FFCR18首轮CP对同机重叠原始维修interval直接NoOverlap，导致假不可行，回退合法1257；
第二轮局部修补1179晋升，第三轮无改善。该适配实现缺陷与会话超时均完整保留，未手改solver。
后两轮exact分别未调用CP、仍重叠维修且漏ExactlyOne，问题未自动解决；最终local也有仅计数不扰动的伪重启。
FFCR19/20最终CP成绩有实际Solve与Core合法证据，但FFCR19未合并窗口、horizon不够通用的潜在缺陷保留。
本轮新增实验编排/审计参数适配和本地报告，无生产代码或评价器改动；旧Word/ZIP未更新，不新增GitHub上传。

报告：`docs/deliverables/machine_availability_test_20260910.md`；
审计：`docs/deliverables/machine_availability_audit_20260910.json`；
聚合：`outputs/contract_fjsp_machine_availability_three_instance_20260910/contract_comparison_v1_qiming/`；
逐例输出在`outputs/contract_fjsp_machine_availability_{FFCR19,FFCR20,FFCR18}_20260910/`。
累计10个问题族/特性组完成独立三例，33对独立算例；跨厂转运、优先级、并行组批仍待独立三例。

## 2026年9月17日 完整报告更新

上述条目是9月10日快照，不覆盖后续进展。工件优先级与PBPM已各完成三例三轮配对，
分别见`docs/deliverables/priority_pbpm_test_summary_20260916.md`。原始三轮主表为12个问题族、36对；
PBPM补充与修复复测单独统计，不替换原始负结果。

跨厂转运v8现完成DFM31、DFM32、DFM33三例一轮Full/None配对。自建DeepSeek V4 Flash、
编码预算900秒/Worker、24个Agent steps、最多3次3秒smoke，正式求解48秒、Core硬超时60秒。
同一合法历史Agent代码起点，不是从零。三例最终如下：

| 算例 | baseline makespan | None | Full | Full相对None改善 |
|---|---:|---:|---:|---:|
| DFM31 | 1383 | 1361 | 864 | 36.517% |
| DFM32 | 1496 | 1441 | 897 | 37.752% |
| DFM33 | 1370 | 1270 | 840 | 33.858% |

均值由1357.333降至867.000，改善36.125%；双方最终合法率3/3，Full九个Worker均完成、激活通过，
每例发生一次正式晋升。最终晋升算法均为启发式耦合局部搜索，未使用CP-SAT。
一轮组不与原三轮组混算。当前报告覆盖13个问题族、39对主结果；单次配对不代表独立重复验证。
早期无收益、停止及Windows长路径导致protocol_invalid的批次仍保留。

完整报告：`docs/deliverables/FJSP算法自演进框架测试报告_20260917.docx`。
专项索引：`docs/deliverables/distributed_transfer_test_summary_20260917.md`。
DFM32原始目录：`outputs/contract_fjsp_distributed_transfer_DFM32_20260917_v8/`。
DFM31/33原始目录：`outputs/contract_fjsp_distributed_transfer_{DFM31,DFM33}_20260917b/`。
相关历史证据包：`exports/FJSP相关实验结果_20260917.zip`，范围与排除项见包内阅读说明及校验清单。
