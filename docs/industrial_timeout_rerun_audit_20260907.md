# 工业 JSON 900 秒重跑审计

审计日期：2026-09-07。范围为 `outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_000`、`round_001`、`round_002` 及第三轮 population 的 `repair_001`。三轮和最终修补均已结束，修补结果与最终配对核验见文末；各轮初次失败证据仍按原始状态保留。

本次为只读源码和运行产物审计，没有复跑候选、模型或验证器。目标为严格词典序：最大化窗口内完成重量，再最小化全时域普通机正换型次数。起点指标为重量 `101.6`、换型 `35`，Core 比较键为 `(101.6, -35)`。

## 结论

前两轮六条候选的 Core 结果均保持起点，但不能解释为“三种算法实际搜索后没有改善”。证据显示：第一轮没有交付可执行的新搜索机制；第二轮两个方法已写入较多代码，却没有在真实 CLI 中启用搜索，群体方法仍无新增实现。

两轮各候选的 Core 校验均合法、错误数为 0，求解约 0.25 秒；结果 diagnostics 仍只有 seed、路线和工序数，没有方法激活计数。合法输出证明保留了可行起点，不证明新方法运行。

第三轮初始候选中，constructive/coupled 仍只执行原 build，结果保持起点；population 已在真实 CLI 中执行群体搜索路径，但在最终重解码调用处发生 NameError，未交付可供 Core 接受的新解。不能将它与前两轮一样概括成“根本没有搜索”，也不能把内部搜索当成合法改进结果。

## 第一轮：round_000

| Lane | Worker / 改动 | 真实执行与根因 | 工具 error / 总调用 |
| --- | --- | --- | ---: |
| constructive_search | timeout；没有 write/edit；最终零差异 | 没有完成新机制，Core 执行保留的起点 | 2 / 6 |
| coupled_local_search | timeout；2 次 edit，但未接受任何差异 | 新 decoder、搜索和 CLI 修改含语法错误，`target_sync_reason=invalid_python`；改动被隔离，Core 执行旧起点 | 2 / 11 |
| population_memetic | timeout；1 次 edit，净增约 435 字节 | 只改 docstring 并添加 random/time imports；没有群体循环，CLI 仍调用原 build | 2 / 8 |

Coupled 隔离代码的第 541 行是未注释自然语言 `activation captured but ignored (incumbent retained)`。其成功的 `py_compile` 事件发生在两次 edit 之前，不能证明新增代码通过编译。

证据：

- [round_000 竞争结果](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_000/competition_result.json)
- [constructive cycle_result](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_000/candidates/family-00-constructive_search/cycle_result.json)
- [coupled cycle_result](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_000/candidates/family-01-coupled_local_search/cycle_result.json)
- [coupled 隔离源码](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/.algoforge_opencode_session/d000-family-6955b4668a7651df9e29/quarantine/1788776984570588300-invalid_python/examples/agent_generated_industrial_solver.py)
- [coupled 工具事件与编译时序](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_000/candidates/family-01-coupled_local_search/worker/opencode_events.jsonl)
- [population 完整 patch](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_000/candidates/family-02-population_memetic/worker_changes.patch)

## 第二轮：round_001

| Lane | Worker / 改动 | 真实执行与根因 | 工具 error / 总调用 |
| --- | --- | --- | ---: |
| constructive_search | timeout；4 次 edit；净增 7,864 字节，synced | build 新增规则、路线和合批分支，但 CLI 仍无参数调用 build；默认 natural/low、batch_merge=False，没有多候选循环 | 2 / 11 |
| coupled_local_search | timeout；7 次 edit；净增 13,881 字节，synced | 已定义 run_coupled_search，但 CLI 未调用；只执行默认 build | 2 / 16 |
| population_memetic | timeout；没有 write/edit；unchanged | 继续原起点，没有新增搜索实现 | 6 / 20 |

本轮没有 `invalid_python` 隔离。新增代码量不等于新增方法已执行：constructive 的参数入口在源码第 130 行，CLI 第 433 行仍使用默认调用；coupled 的搜索定义在第 388 行，CLI 第 545 行仍只调用 build。

证据：

- [round_001 竞争结果和 activation 检查](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_001/competition_result.json)
- [constructive 候选源码](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_001/candidates/family-00-constructive_search/candidate_worktree/examples/agent_generated_industrial_solver.py)
- [coupled 候选源码](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_001/candidates/family-01-coupled_local_search/candidate_worktree/examples/agent_generated_industrial_solver.py)
- [population 工具事件](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_001/candidates/family-02-population_memetic/worker/opencode_events.jsonl)

## 第三轮初始候选：round_002

| Lane | Worker / 改动 | 真实执行与 Core 结果 |
| --- | --- | --- |
| constructive_search | timeout；零差异 | CLI 第 250 行仍调用原 build；合法，重量 101.6、换型 35，求解 0.255287 秒 |
| coupled_local_search | timeout；仅修改 solver.py，净增 590 字节 | patch 只给构造器添加路线/机器选择参数、horizon 与状态字典；CLI 第 258 行仍只 build；合法，重量 101.6、换型 35，求解 0.248038 秒 |
| population_memetic | timeout；修改 solver.py，净增 10,745 字节 | CLI 已接通群体初始化、路线/顺序变异和选择循环；导出前 NameError，Core 为 failed_runtime，best_metrics 为空 |

Population 源码的 `main` 第 446 行评估起点，第 464 行起执行群体初始化，第 482–509 行执行代际/后代评估和排序选择。真实 Core traceback 到达其后的第 513 行 `builder.decode(best[0], best[1], record_solution=True好自己的)`，证明本次沿该控制流走到了搜索之后，而不是只定义了未调用函数。`True好自己的` 是合法 Unicode 标识符，所以错误是运行时 NameError，不是 Python 语法错误或 invalid_python 隔离。

局部验证位于第 516 行、activation 字段构造位于第 527 行以后、JSON 写出位于第 552 行，均在失败点之后。当前没有这些最终证据，不能报告实际最优重量、换型、合法性或已导出的激活计数。本文不推测修复后成绩。

证据：

- [round_002 constructive 初始 cycle_result](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_002/candidates/family-00-constructive_search/cycle_result.json)
- [round_002 coupled 初始 cycle_result](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_002/candidates/family-01-coupled_local_search/cycle_result.json)
- [round_002 coupled 完整 patch](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_002/candidates/family-01-coupled_local_search/worker_changes.patch)
- [round_002 population 初始 cycle_result](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_002/candidates/family-02-population_memetic/cycle_result.json)
- [round_002 population 初始候选源码](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_002/candidates/family-02-population_memetic/candidate_worktree/examples/agent_generated_industrial_solver.py)
- [round_002 population Core stderr](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_002/candidates/family-02-population_memetic/harness_smoke/experiments/round_000__small_20_random_seed20260716__seed_0/solver.stderr.txt)

## Activation 合同缺口

第二轮三条候选都被 activation gate 拒绝，`eligible_candidate_count=0`。构造候选数与路线配置数、局部搜索 moves 与路线切换数、群体 generations/population size/路线变异数均为字段缺失，而非已观测为零。

另有独立的协议缺口：三条均要求 `best_metrics.grouped_batch_count > 0`，但冻结工业验证器输出的是 `batch_ops`、`batch_group_count`，不输出 `grouped_batch_count`，因此该项无法从当前 Core 报告解析。不能改用 `batch_group_count` 冒充真实合批数，因为它也统计单件批。

这是需要单独修复的字段契约问题，不应归因模型；但本轮其他方法证据同样缺失，所以它不是本轮失败的唯一原因。本文仅记录问题，没有修改评测或门禁。

## 超时、会话与知识证据

每轮每 lane 配置约 900 秒总 Worker 预算。第一轮实际为 900.110 / 900.093 / 900.110 秒；第二轮每条先经历约 45.1 秒 `zero_event_stream_timeout`，再使用约 854 秒剩余预算。重试共享该轮预算，不是两次各 900 秒。

第二轮 constructive/coupled 明确记录 `materialized_workspace_fresh_session_after_stalled_worker`，即保留物化工作区但启动新模型会话。Population 的 requested/command session 为旧 ID，而 observed session 为新 ID，竞争结果为 `continuity_failed`，不能宣称成功在原模型会话续写。

第一轮各 lane 实际工具耗时合计不到 1 秒，最大相邻事件空档为 256.742 / 256.952 / 326.283 秒；初始附件约 53.2 万字符。可确认等待或生成推进缓慢，不能据此区分模型生成、服务排队、上下文处理或网络原因。第一轮 provider retry 为 0，没有服务端错误的直接证据。

本次 improvement 三条 lane 均有相应方法 Skill、foundation Skill 和 experiment Skill 的成功加载事件，工业 `search_adaptation.md` 也在授权 read_set 和命令附件中。这与此前 baseline 阶段知识过滤/不可见问题不同，不能用旧 baseline 诊断解释本次“没有 Skill”。有授权、有附件、有加载仍不等于算法已实现和激活。

证据：

- [round_000 constructive Skill/工具事件](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_000/candidates/family-00-constructive_search/worker/opencode_events.jsonl)
- [round_000 population Skill/工具事件](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_000/candidates/family-02-population_memetic/worker/opencode_events.jsonl)
- [round_000 授权及附件命令](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_000/candidates/family-00-constructive_search/worker/opencode_command.json)
- [round_000 附件预算](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_000/candidates/family-00-constructive_search/worker/opencode_context_budget.json)
- [round_001 constructive 重试预算](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_001/candidates/family-00-constructive_search/worker/opencode_provider_retries.json)
- [round_001 coupled 会话记录](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_001/candidates/family-01-coupled_local_search/worker/opencode_session.json)
- [round_001 population 会话记录](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_001/candidates/family-02-population_memetic/worker/opencode_session.json)

## 修补与最终结果

第三轮 population 的 `repair_001` 正常完成，原固定验证器确认 20/20 任务、139 工序完整合法，窗口完成重量 127.89、正换型 37 次。6 道批工序形成 4 批，确有非单件批；BA4419 采用路线 1。运行 diagnostics 留存 9 代、种群规模 8、63 次后代解码及 60 次路线变异评价。该结果是实际合法搜索候选，不是前两轮的旧起点回放。

最终竞争记录中，该候选 `core_eligible=true`；代数、种群规模及路线变异三项 activation 通过，唯一失败项为不存在的 `best_metrics.grouped_batch_count`，`found=false`，故未晋升。该事实将字段契约缺口从潜在风险落实为本次真实的晋升阻塞。没有修改旧门禁或追溯性替换结果。

两侧均从相同冻结起点执行 3 轮、9 lane：None 9 次 Worker 尝试、3 次晋升，最终 155.29 / 32，墙钟 1121.579 秒；Full 10 次尝试（含 1 次修补）、0 次晋升，最终返回 101.60 / 35，墙钟 3389.781 秒。Full 返回重量相对 None 为 -34.574%；即便看未晋升的 127.89，其重量仍低 17.644%，不能认为只修门禁就能逆转 Full/None 结果。墙钟含评测，不是纯主控时间。

配对设置检查通过不等于 activation 合同正确，也不构成 Skill 净收益或验收达标证据。当前只放宽试验超时并记录诊断，工业资料交付、CLI 实现闭环及字段契约缺陷仍待独立版本修复验证。

- [合法修补 cycle_result](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_002/candidates/family-02-population_memetic/repair_001/cycle_result.json)
- [第三轮最终竞争与唯一失败的 activation 项](../outputs/contract_industrial_small20_random_20260907/full_v3timeout900_qiming/worker_loop/round_002/competition_result.json)
- [完整配对报告](../outputs/contract_industrial_small20_random_20260907/pair_v3timeout900_audited.json)
- [简表](../outputs/contract_industrial_small20_random_20260907/pair_v3timeout900_audited.md)
