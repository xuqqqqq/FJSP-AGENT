# 方法来源、验证依据与迁移边界

## 1. 审计范围与证据等级

审计日期：2026-09-08。来源项目为 Huawei 工业铝加工 FJSP 工作区。本表路径均为**来源仓库相对路径**，只供审计定位；本 Skill 的解析、公式、算法与例子已自包含，接收 Agent 不需要这些文件。

先读实际执行分支，再与验证器及机器可读报告核对。原有 `huawei-aluminum-fjsp` 和 `complex-fjsp-stepwise-optimization` Skill 仅帮助确认工作流背景，未把其中“先生成 baseline”“修复”等概述当作实现证据。未将调用包装、设计注释或论文文字视为 solver 功能。`strategy_kernel_solver` 是原内核的子类/策略入口，不能当另一份独立构造器。

证据分层：源码审计证明当前函数确有相应操作；归档零错报告证明指定输出曾被相应评价器接受；受控消融支持特定实例上的效果。三者都不能证明所有约束组合/未来实例必然成功。报告未记录输入内容哈希、源码版本及全部口径开关时，明确保留复现缺口。

## 2. 源码到机制映射

| 来源文件与函数（当前行号） | 已读到的行为 | 本 Skill 对应位置 / 不可直接迁移部分 |
|---|---|---|
| `scripts/rl_relaxed_solver.py:405` `SetupRowStore.ensure`，`:478` `get` | 按 setup 外层工序键读嵌套行，int 叶值；无前驱/缺边返回 0 | parsing-state：嵌套解析；不需要迁移 SQLite/pickle 缓存 |
| 同文件 `:500` `choose_path`、`:561` `build_task_spec` | 路线负载代理排名后只保留一条，工序数值排序，分别读 Q 两端锚点 | 保留排名机制；本 Skill 的多路线回退、分层可达性为补齐设计 |
| 同文件 `:682` `build_instance`（`:734` 起时间处理、`:753` 起日历） | 读取绝对 horizon；可重置 current_time/平移维修；原维修结束点加 1 | parsing-state：必须重新核对当前 IO，禁止迁移历史平移口径 |
| 同文件 `:64` `maintenance_violation`、`:914` `fit_after_maintenance` | 半开加工冲突检查；默认允许维修结束前最后一分钟启动；向后跳出日历 | feasible-construction：严格日历区间减法是精确化推广；只有当前评价器允许才开容差 |
| 同文件 `:945` `transfer_allowed`、`:952` `compute_bounds` | 前一道工序的有向厂区许可；机器间运输；全部入 Q 边按独立锚点求 L/U | 公式可迁移；旧构造假定来源已排，本 Skill 增加出边逆向界和传播 |
| 同文件 `:981` `has_forward_compatibility` | 只检查下一道至少一台可转入机器 | 不能称为全路线可达性或时间前瞻；完整分层可达性是补齐设计 |
| 同文件 `:1043` `evaluate_candidate`、`:1111` `record_schedule` | 机器尾部追加，前驱 setup，日历后起点不超 Q 上界；提交记录和尾状态 | 没有任意空档插入及固定右邻 setup 检查；本 Skill 显式补齐 |
| 同文件 `:1040` `batch_family`、`:1137` `record_batch_group` | 精确 tuple，同机、共同 S/E、人数<=最紧容量、D=max p；候选等待与时长模式 | 实装有限批机制；批时长变化后的所有成员 end 锚点重算不充分，不能声称联合窗口已通用正确 |
| 同文件 `:1259` `score_candidate`、`:1293` `score_candidate_phase2`、`:1321` `selected_phase_pool` | 重量/密度、连续性、真实零 setup、正 setup 固定/时长惩罚、Q slack；最早开始附近候选窗口 | optimization：窗口是派工过滤而非多步 rollout；不迁移数值系数、任务奖励或能耗扩展目标 |
| 同文件 `:1527` `solve`、`:1426` `run_phase2_completion_loop` | 第一阶段按乐观完工延后，第二阶段排完整尾部，无候选可标 infeasible | 第二阶段意图补全但并非保证；本 Skill 不以函数返回代替完整验证 |
| 同文件 `:1344` `rebuild_state_with_kept_tasks`、`:1408` `repair_incomplete_tasks` | 删除未完成任务前缀，保留完整任务，重建机器尾/索引并重排；轮数/停滞终止 | conflict-repair：真实粗粒度修复；不包含通用前段时间/机器回退或路线回退 |
| 同文件 `:1478` `find_internal_machine_violations`、`:1507` `repair_setup_violations` | 删除后检查新普通邻接边，删冲突两端任务再补齐 | 可迁移“删除也要查新 setup”；旧按任务保留会破坏混时长批，整批原子恢复为补齐设计 |
| `scripts/validate_batch_solution.py:76` `load_batch_metadata`、`:119` `validate_batch_solution` | 完整选路/资格、日历、转运、独立 Q；`:405` 起按 `(m,S,E)` 组批检查 family、容量、max 时长、批间不重叠 | 定义本文有限批口径；固定评价器不替换、不修改 |
| 同文件 `:386` 普通时间线、`:379` horizon 统计、`:456` metrics | 全排程普通机正换型次数；最后工序 E<=绝对 H 才计重量 | 指标函数不实现词典序搜索接受规则；必须在新算法中显式实现 |
| `scripts/rl_relaxed_solver.py:1728` `validate_solution` | 不检查有限批容量/族/批间占用，单条 duration 必须等于单件 duration | relaxed 报告不可当 finite 合法性；合法混时长有限批也可能不通过 relaxed |
| `scripts/sequence_slot_swap_postprocess.py:255` `local_setup_metrics`、`:278` `find_swap_candidates`、`:335` `apply_swap`、`:650` `validate_candidate` | 同机等时长交换，双侧 setup 局部评估，deepcopy 候选，完整有限验证 | 迁移邻域与事务模式；`:889` 附近重量阈值+降 setup 接受可能损失重量，必须替换 |
| `scripts/strategy_kernel_solver.py:318` `StrategyScheduler` | 继承旧构造器，扩展策略特征、评分和池选择 | 不作为独立求解器或更强回修证据 |

## 3. 具体历史验证依据

这些是报告级事实，不附解文件，不迁移任何任务安排或调好的数值参数。报告文件名中的历史参数字样仅用于唯一定位证据，不是推荐配置。

| 原始归档报告 | 实际检查结果 | 可支持的结论与边界 |
|---|---|---|
| `outputs/test_case1_24480/finite_batch_singleton_validation_report.json` | finite，完整=总任务=1751，error_count=0；538 批工序/538 批 | 历史单成员有限批完整输出获接受；不证明该方法对任意紧 Q 实例必成功 |
| `outputs/test_case1_24480/finite_batch_group_validation_report.json` | finite，完整=总任务=1751，error_count=0；538 批工序/373 批 | 真实多成员有限批曾通过；报告缺完整日历开关/源码哈希，不能追认为严格 tolerance=0 |
| `outputs/tolerance1_case1_finite_resume_refine/fam750_wait400.tolerance1.report.json` 与同目录 `summary.json` | 同一输出 finite，完整任务1751，0错，宽容验证返回码0 | 宽容日历下有合法证据；文件名与汇总帮助确认口径但不补全源码版本 |
| 同目录 `fam750_wait400.strict.report.json` | 同一输出严格验证25错，首类为维修结束前一分钟起工；汇总返回码1 | 直接否定“宽容合法可直接迁移为严格合法” |
| 同目录 `fam750_wait400.solver.stdout.txt` | 内置 relaxed 自检30错，含混时长批 duration mismatch；对应 finite 报告0错 | 不能要求 finite 先通过 relaxed；两验证模式不是简单的强弱包含关系 |
| `outputs/test_case1_24480/relaxed_best_batch_validation_report.json` | 有限验证483错，含族、容量、批重叠；完整记录数仍等于总任务数 | 有完整记录不等于合法；relaxed 解不可直接交付 finite |
| `outputs/cross_case_ablation_summary_20260716.json` | 两基线分别1751/1751、1845/1845，均0错；关 family reward 两例减重且增 setup，关 setup penalty 同样方向且0错 | 支持这两类排序机制值得迁移；不是新输入性能保证，也不证明紧 Q 回退机制 |
| 同消融 JSON 的 zero-setup-off 对应 case2 | error_count=1 | 非法消融点不进入收益论证；后期论文整理遗漏错误字段不覆盖原始报告 |
| `outputs/baseline_comparison_20260716/baseline_comparison_summary_20260716.json` | singleton/basic family batch/raw finite 在 case1 有完整零错结果 | 支持组合构造机制的实证可用性，不能把组合收益全部归因于一个等待参数 |

`docs/cross_case_controlled_ablation_summary_20260716.md` 用于解释上述受控比较；`docs/case1_tuning_process.md:303` 起记录有限批等待/混时长策略比较；`docs/算例1有限组批自动调参过程_20260512.md:364` 起记录批最大时长失败。它们是辅助解释，遇到与机器可读报告不一致时，以具体报告及口径为准。

## 4. 明确未被旧验证证据覆盖的组合

1. **通用有界冲突回退**、任意空档双侧插入、STN 传播、自动更换失败路线、逐起点枚举：本 Skill 的补齐设计，不是旧内核功能，也尚无本设计的全量工业运行证据。
2. **长短合批 + end 锚点 Q**：旧 `record_batch_group` 将 D 取 max，却沿用成员原 upper，并未以新 D 重算所有上下界。固定 finite 评价器使用实际共同 duration 检查，因此可能拒绝内核自认为可行的批。
3. **按任务撤销 + 批中最长成员被删除**：旧重建没有批成员闭包；幸存成员保留旧 D 会破坏 max 时长规则。本 Skill 用快照整批恢复/闭包撤销修补。
4. **同物理机普通/批混用**：旧 finite 评价器分开两种时间线，没联合检查交叉占用；且按工序级 `proc.is_batch` 分类，而当前构造器支持候选级 batch 覆盖。历史证据只在其口径/覆盖范围内有效；新模型统一占用时间线并核对分类。
5. **换型活动本身避开维修**：旧评价器只留时间 gap 并检查加工避维修，未检 setup 段日历；不能宣称支持这一更强约束。
6. **严格词典序**：旧评分、tuner/Pareto 和若干后处理有不同的接受目标。本文只迁移候选生成机制，最终接受必须保持重量第一、全排程 setup 第二。

对每一项给新 Agent 的要求是“实现并验证”，而不是“假定已由旧算法保证”。固定评价器即便有覆盖缺口，也不得利用缺口输出违反当前明确要求的解；使用额外本地一致性检查，同时保留固定评价器原样。

## 5. 当前源码指纹与复现限制

审计时 git HEAD：`a935726537637b25a65f3a01ba4d25f5aca5f16e`。工作区已有用户修改，包括核心求解器及有限验证器；本任务没有修改这些文件。下列 SHA256 指纹定位**所读工作区内容**，不代表历史报告对应的代码版本：

```text
scripts/rl_relaxed_solver.py
19FD2D9D12176EB5B8D4FE222CC667B3794E365E14922627601872F9FEDA5E54
scripts/validate_batch_solution.py
6CC2DD5696FD698B21072DAEC370200CB40DA5C8B284E52D2085CA4319666F27
scripts/sequence_slot_swap_postprocess.py
99D82D0EE3ECB22C073699CE71DAFCB8EEF45B2A7378B05EB1EE73E5C306D4F4
scripts/strategy_kernel_solver.py
47778F8037F2B77D9CA3F9BB920203677B841D5EA7D983B26A5B539DA5C3D733
```

本次核对了源码执行逻辑及原始报告，未重新跑全部工业历史排程；不能将历史0错扩大为当前 dirty 源码在所有输入上的验证通过。轻量运行了当前日历判断函数的5项边界断言，确认 tolerance=0/1 的具体区别。Skill 格式通过 `skill-creator` 的 `quick_validate.py`，相对 Markdown 引用全部可解析。

## 6. 本 Skill 的独立行为验收

另一 Agent 仅阅读本 Skill，在隔离临时目录以 Python 标准库独立实现测试内核，未读取或调用旧仓库的 solver、脚本、解或评价器。作者再次运行该程序，退出码0，末行 `ALL SYNTHETIC ACCEPTANCE CHECKS PASSED`。测试程序是验收工装，未作为初始求解器放入本包。

| 覆盖 | 实际执行结果 |
|---|---|
| M1/M2 解析与资格 | 嵌套有向 setup、缺边规则、前工序控制跨厂、运输最早6、深链只保留 MA→MC→MD，全部通过 |
| M3 真正有界回退 | 从 A@0 开始，试 A@0…11，再 B@20；13节点、11次回退；结果 A=[11,16)、B=[20,23)；首冲突记录 Q 上界9和维修结束20 |
| M4/M5 联合时间窗 | 双侧换型+日历交集[13,15]；非相邻双锚点正向[9,11]、逆向[0,2]；非法起点被拒绝 |
| M6 有限批 | 最小容量、精确族、共同 D；end 上界重算为-1拒绝合批；end 下界从4变2；必须合批例实际经历两个 singleton 失败，再提交共同[1,4) |
| 未定批 duration 安全性 | 实际运行 Bellman–Ford：不安全的单件上界4错排除合法 D=6；撤销旧 duration 约束后重建通过 |
| M7 恢复 | 实际修改多个 State 字段后失败，完整 dataclass 相等；兄弟分支与从原状态直接执行一致；执行批与成员后继撤销闭包；删除后的新 setup 冲突被拒绝 |
| M8 与终止 | 绝对 H、H 外 setup、完整性、严格词典序、内存 JSON 往返均通过；节点/墙钟/深度预算真实触发并返回 NOT_FOUND_WITHIN_BUDGET；有限 T_search 扩展重启成功 |

验证局限：这是小型独立合成验收，M3 路线和机器固定；没有实现完整工业 IO 适配或运行当前工业固定评价器。Bellman–Ford 单独测试，未集成到每个安置分支；没有证明通用搜索完备性、工业性能或所有优化邻域效果。新增有界回退与批修补设计仍须接收 Agent 在目标算例和固定评价器上重新验证。
