# AlgoForge 参考实现差距分析与迁移建议

## 1. 分析目的与范围

本文对比当前 FJSP 算法自演进框架与公开仓库
[Qiming01/AlgoForge](https://github.com/Qiming01/AlgoForge)，目标不是替换现有系统，
而是识别可提升任务可靠性、可观测性和易用性的通用平台能力，并给出适合当前
Python Harness 的迁移顺序。

本次参考版本为提交
[`254389e`](https://github.com/Qiming01/AlgoForge/commit/254389ebd9b86ff09eb02992a2cdf3c0a60c781b)
（2026-08-03）。该仓库根目录未提供 `LICENSE` 文件，因此本文只借鉴架构思想和
功能边界，后续代码应结合当前项目自主实现，不直接复制参考仓库源码。

## 2. 总体判断

两个项目解决的问题不同，能力不是简单的“谁更完整”。参考 AlgoForge 更接近通用
算法优化平台，长于任务事实管理、恢复、运维和交互；当前项目已经形成面向 FJSP 的
专用演进内核，长于变种识别、Runtime Contract、方法族路由、候选合法性审查以及
多 lane 晋升与回滚。

因此，合理路线是保留当前 FJSP Main/Coding Agent/Core 闭环，选择性补入参考项目的
平台能力。若整体迁移其 TypeScript、React、Node 和容器运行时，不仅工作量大，还会
削弱当前已验证的 FJSP 领域边界。

## 3. 已有能力对比

### 3.1 当前项目应保留的领域优势

| 能力 | 当前状态 | 结论 |
| --- | --- | --- |
| FJSP 及变种识别 | 根据实例、需求和 IO 形成实例画像与 Runtime Contract | 保留 |
| Domain Pack 与知识路由 | 已覆盖释放时间、停机、运输、优先级、可重入、组批、时间间隔及多特性组合 | 保留 |
| 方法族竞争 | 构造搜索、耦合局部搜索、群体/模因、Exact Hybrid 分 lane 竞争 | 保留 |
| 固定 Core | 固定 evaluator 负责合法性、指标和正式晋升，诊断结果不得替代 Core | 保留 |
| 机制激活门禁 | 区分“代码中出现方法”与“方法实际运行”，并检查 no-op、changed files 和验证组件 | 保留 |
| 同源重基与回滚 | 每轮 lane 从正式 incumbent 重基，失败候选不污染正式版本 | 保留 |
| FJSP 专项 Skills | 方法 Skill、变种适配 Skill、知识卡和方法包已经比参考仓库更细 | 不覆盖 |

### 3.2 参考 AlgoForge 的平台优势

| 能力 | 参考实现 | 当前项目现状 | 差距判断 |
| --- | --- | --- | --- |
| 追加式事件事实库 | `events.jsonl`、单调 sequence、投影重建视图 | `web_job_status.json` 覆盖式快照 | 明显差距 |
| 增量事件推送 | SSE、`Last-Event-ID`、断线续传与缺口恢复 | 浏览器每 1.8 秒全量轮询 | 明显差距 |
| 任务备份与恢复 | 会话、证据、工作区、清单和 SHA-256；导入前预检 | 只有新任务项目 ZIP 导入，不是完整任务备份 | 明显差距 |
| 任务完整性检查 | 检查事实、事件、工作区、Agent 会话和证据引用 | 无统一完整性报告 | 明显差距 |
| 显式优化计划 | 计划和步骤有 revision、状态及 Run/Candidate/Evaluation 引用 | 有方向计划和假设图谱，但没有独立步骤投影 | 部分具备 |
| Agent 安全续接 | 按错误类别、会话空闲、悬空工具和预算决定是否续接 | Worker 有 provider retry、context-limit 新会话和工作区保留 | 部分具备 |
| 模型提供商管理 | 动态 provider/model、模型发现、连接与工具调用测试 | 环境变量加固定模型下拉，只报告配置存在性 | 明显差距 |
| 诊断导出 | 可导出任务诊断信息 | 有产物浏览，无一键诊断包 | 明显差距 |
| 资源监控 | 容器 CPU、内存等运行资源展示 | 无统一资源面板 | 次要差距 |
| 后继任务 | 可从 Best 或研究路线创建新任务 | 只能在同一任务从正式 incumbent 追加轮次 | 部分具备 |
| Skill 挖掘 | 用户触发后从会话与证据归纳 CREATE/UPDATE/REUSE/NONE | 有 Skill 总结归纳，但不是完整会话挖掘闭环 | 部分具备 |
| 知识版本与索引 | Revision、Docling、Meilisearch/Orama | 本地 Markdown/Skill 与 Domain Pack 注册 | 规模扩大后再补 |

## 4. 关键差距的代码证据

### 4.1 当前项目

- `harness_agent/web/server.py` 的 `write_job_status` 每次覆盖
  `web_job_status.json`，该快照同时承担浏览器状态和后端重启恢复职责。
- `harness_agent/web/static/app.js` 使用 `setInterval(refreshJob, 1800)` 全量轮询。
- `harness_agent/web/server.py` 已支持任务停止、正式 incumbent 续跑、20 秒换向确认、
  provider retry 结果展示和 context-limit 后保留工作区并换新会话。
- `harness_agent/web/server.py` 已接受 `main_planning_mode=fast|research`，CLI 也已支持，
  但本次对比前网页表单没有选择入口。
- Web API 目前没有任务备份下载、恢复预检、任务完整性检查、SSE 和主动模型测试接口。

### 4.2 参考项目

- [JSONL Event Store](https://github.com/Qiming01/AlgoForge/blob/254389ebd9b86ff09eb02992a2cdf3c0a60c781b/packages/core/src/storage/jsonl-event-store.ts)
  把事件作为事实，使用连续 sequence，并支持按 sequence 增量读取。
- [Optimization Plan Projection](https://github.com/Qiming01/AlgoForge/blob/254389ebd9b86ff09eb02992a2cdf3c0a60c781b/packages/core/src/events/optimization-plan-projection.ts)
  将计划发布和步骤更新建模为显式事件，不从运行日志猜测计划进度。
- [Agent Recovery](https://github.com/Qiming01/AlgoForge/blob/254389ebd9b86ff09eb02992a2cdf3c0a60c781b/packages/agent-runtime/src/agent-recovery.ts)
  仅对传输和 provider 容量错误自动续接，并检查会话、工具调用和预算。
- [Task Backup](https://github.com/Qiming01/AlgoForge/blob/254389ebd9b86ff09eb02992a2cdf3c0a60c781b/apps/api/src/task-backup.ts)
  把导出、清单哈希、导入预检和确认恢复拆成不同阶段。
- [Task Integrity](https://github.com/Qiming01/AlgoForge/blob/254389ebd9b86ff09eb02992a2cdf3c0a60c781b/apps/api/src/task-integrity.ts)
  提供独立于运行流程的只读一致性检查。

## 5. 推荐迁移项

### 5.1 P0：先解决可靠性和可解释性

| 项目 | 当前项目接入位置 | 自主实现方案 | 工作量 | 主要风险 |
| --- | --- | --- | --- | --- |
| 追加式任务事件账本 | `harness_agent/web/server.py`，新增 `harness_agent/web/events.py` | 保留状态快照，同时双写带 sequence 的 `web_job_events.jsonl`；先用于 Web 事件和状态变更，不迁移 Core 原始产物 | 中 | 双写一致性、历史任务兼容 |
| SSE 增量事件流 | `AlgoForgeWebHandler.do_GET`、`static/app.js` | 新增 `/api/jobs/<id>/events?after=`；断线后按 sequence 补齐，快照只做首次加载和缺口兜底 | 中 | 标准库 HTTP 长连接、代理超时 |
| 任务完整性检查 | 新增 `harness_agent/web/integrity.py` 与只读 API | 检查状态文件、loop result、incumbent、候选目录、Core 回执、changed files、session 和引用路径 | 中 | 旧任务字段不齐，需要分级而非一律失败 |
| 完整任务备份/恢复 | 新增 `harness_agent/web/backup.py` 和 Web 导入预检 | 导出输入、状态、正式产物、候选证据及清单哈希；先预检，确认后分配新 job id 恢复 | 大 | 活跃任务一致性、绝对路径重写、敏感信息过滤 |
| 显式计划步骤 | Main 输出协议、loop 结果、Web 实验页 | 把每轮方向拆成有限步骤，显式记录 pending/running/completed/failed，并引用 lane、candidate 和 Core 回执 | 中至大 | 不应从日志推断状态，也不能让计划状态参与晋升 |
| 恢复策略统一 | `opencode_main.py`、`opencode_worker.py`、loop | 在现有 provider retry/context-limit 机制上增加统一错误分类和续接决策记录 | 中 | 重复副作用、错误类别误判 |

业务优先级上，备份恢复最容易被用户感知；工程依赖上，应先建立事件 sequence 和
完整性检查，再做可恢复备份。这样恢复后的任务能够证明“文件存在且引用一致”，而不只是
把目录压缩后重新展开。

### 5.2 P1：改善实验操作与运维

| 项目 | 接入建议 | 工作量 |
| --- | --- | --- |
| 模型提供商管理与主动测试 | 后端只保存 provider 引用和密钥来源，不向前端返回密钥；分别测试文本生成和工具调用 | 中至大 |
| 从指定正式节点创建后继任务 | 首版只允许正式 incumbent 或已通过 Core 的候选，禁止直接继承未验证 lane | 中 |
| 一键诊断包 | 导出脱敏状态、事件、provider retry、session 映射、进程状态和关键回执，不含 API key | 小至中 |
| 浏览器完成/异常通知 | 用户授权后仅通知终态、等待输入和异常，不对普通进度刷屏 | 小 |
| Fast/Research 网页选择 | 已在本次变更中补齐，提交后写入任务 config | 小 |

### 5.3 P2：规模扩大后再引入

| 项目 | 暂缓原因 |
| --- | --- |
| CPU/内存/子进程资源面板 | 有价值，但不影响候选合法性和任务可恢复性 |
| 富文档解析 | 当前多数需求与 IO 是 Markdown/TXT，先保证合同抽取与人工确认 |
| 独立搜索服务 | 当前知识规模可由标签、Domain Pack 和文件索引承载，引入 Meilisearch 运维成本偏高 |
| 会话 Skill 挖掘 | 应先建立稳定事件和证据引用，否则容易把偶然经验写成通用 Skill |
| 参数设计空间探索 | 值得增加为受控实验 Skill，但必须服从固定 Core、预算和去重，不应成为后端内置求解算法 |

## 6. 不建议迁移的内容

1. 不整体搬入 TypeScript/React/Node/Docker 技术栈。当前 Python 编排、FJSP evaluator 和
   大量测试已经形成稳定边界，重写不能直接提高算法质量。
2. 不用参考项目的通用 Agent 流程替换当前 Main/Coding Agent/Core。当前系统的同源重基、
   多 lane 竞争、机制门禁和晋升回滚是 FJSP 实验可信度的核心。
3. 不覆盖当前 `fjsp-solver-optimizer` 和变种 Skills。参考仓库中的 FJSP Skill 更通用，
   当前版本已经加入低/高柔性判断、方法族竞争、局部 CP、Core 证据和变种适配约束。
4. 不在第一阶段引入 Docling、Meilisearch 或多个独立 Runtime 服务。它们会增加部署和故障面，
   应由真实文档规模和检索延迟数据驱动。
5. 不允许从任意失败 lane 直接创建后继任务。只有正式 incumbent 或独立 Core 验证通过的候选
   可以成为继承点，避免绕过现有竞争与回滚规则。

## 7. 推荐实施顺序

### 阶段一：事件与完整性基础

1. 定义最小任务事件模型：`event_id`、`sequence`、`time`、`type`、`source`、`job_id`、
   `round_index`、`candidate_id`、`payload`。
2. 在保留 `web_job_status.json` 的前提下双写 JSONL；为写入增加进程内锁、幂等 event id 和
   尾部损坏检测。
3. 将浏览器事件区改为 SSE 增量消费，仍保留快照重载作为兜底。
4. 增加只读完整性报告，先覆盖新任务，对旧任务返回 warning/unknown，而不是伪造 passed。

### 阶段二：可移植任务

1. 定义备份 manifest 和版本号，列明每个文件的相对路径、大小和 SHA-256。
2. 导出前生成一致性报告；运行中任务首版只允许“诊断导出”，不承诺可续跑备份。
3. 导入分为上传预检和确认恢复两步；预检不写正式任务目录。
4. 恢复时分配新 job id，重写工作区绝对路径，清除凭据和失效进程信息。

### 阶段三：计划与运维

1. 给 Main 计划增加显式 revision 和步骤状态，步骤引用现有 round/lane/candidate/Core 证据。
2. 增加 provider 主动测试、诊断包、浏览器通知和受约束的后继任务创建。
3. 有了稳定事件证据后，再实现会话 Skill 挖掘和参数设计空间实验 Skill。

## 8. 本次已完成的迁移

### 8.1 Candidate Pool、Best revision 与顺位晋升

当前框架已将每轮所有 lane 的结果写入跨轮 `candidate_pool.json`。通过 changed files、Core、
机制激活和 exact execution 门禁的 lane 标记为正式 Candidate；未通过门禁的 lane 也保留为
审计记录，但不能参加晋升。合格 Candidate 按固定目标键排序，第一名 promotion repeat 失败后，
继续复验第二、第三名；一旦当前候选已不严格优于正式 incumbent，后续更差候选不再消耗复验预算。
每次真实晋升追加到 `best_history.json`，未通过复验的候选不会形成 Best revision。

Main 的下一轮反馈中还会列出同一父版本、方法或改动具有互补性的候选组合。该组合只是
`synthesis_opportunity`，不能直接拼接或晋升 loser patch；Main 必须新建综合 lane，由新的
Coding Worker 从唯一正式 incumbent 实际实现，再重新通过固定 Core 和全部晋升门禁。普通 lane
下一轮仍统一从正式 incumbent 重基，原 session 可以续用，但落后代码 checkpoint、stage 和
verified components 不能继承。

### 8.2 当前项目的 Fast Main 与 Research Main

网页任务配置现已增加 `Main 规划模式`：

- `Fast Main（快速规划）`：Main 只执行一次有界方向调用，读取压缩后的 Fast Planning Packet，
  不加载 Skill、不调用专业分析子 Agent，也不编写候选级实现细节。Harness 绑定兼容方法包后，
  各 Coding Worker 读取领域 Skill 和 incumbent，自主选择可区分机制。该模式适合合同已经明确、
  需要控制 Main 延迟的正式批量实验。
- `Research（研究规划）`：Main 先做方向选择，再做实现规划；方向阶段可调用 evidence analyst 或
  requirements-method analyst，实现阶段可调用 Skill 总结、计划批判和候选策略分析角色。它会读取
  更完整的 Planning Packet、incumbent 源码和历史证据，适合新变种、合同歧义、方法选择困难或长期
  停滞场景，代价是更多模型调用和更长规划时间。

该字段沿用后端 `main_planning_mode` 合同，不改变 solver、固定 Core 或候选门禁。当前 OpenCode
Main 明确实现了两条路径；结构化 API Main 仍走其既有规划实现，不能把网页字段误解成模型本身的
“快速/深度思考”开关。

### 8.3 参考 AlgoForge 的对应做法

参考 AlgoForge 没有 `fast|research` 两套 Main。它始终使用一个持久 Lead Agent：普通优化通过
`delegate_optimizers` 从同一 Best 并行委派独立 Optimizer；只有遇到开放式知识发现、论文筛选、
精确阅读或跨来源比较时，Lead 才按需委派 `algorithm-researcher`，Reviewer 和 Recorder 也不是
每轮必经。互补路线通过 `synthesize_research_routes` 新建实际 Optimizer 进行源码综合。

参考项目界面中的 `researchMode=autonomous|continuous` 是“研究结束策略”，不是 Main 的规划深度：
`autonomous` 允许 Lead 在证据充分或收益不足时主动结束；`continuous` 在用户未停止、预算未耗尽且
不存在不可恢复阻塞时禁止因短期收益递减而结束。可借鉴的方向不是照搬名称，而是在当前显式
Fast/Research 选择之上增加证据触发的阶段升级：平时走 Fast，出现新变种、连续回滚、候选同质化、
知识缺口或综合机会时，只对当前阶段升级到 Research，然后回到轻量规划。

### 8.4 主控净时间匹配的 Full/None 对照

当前框架新增 `controller_budget_seconds`，累计 Main、Coding Worker、语义审查、反思和编排的墙钟
时间，并扣除固定 Core 评测区间的并集。它与单次 solver 的 `timeout_seconds`、单次 Coding Worker
的 `max_runtime_seconds` 和轮数上限相互独立。预算在新一轮、方向规划后和每次本地 trial/repair 前
检查；已开始的原子调用和固定 Core 不被强杀，因此最多允许一个原子调用粒度的软超额，并在
`controller_budget.overshoot_seconds` 中记录。预算耗尽时保留正式 incumbent，以 `status=ok`、
`terminal_reason=controller_budget_exhausted` 正常结束。

公平对照可以先让 Full 固定运行三轮，再让 None 读取 Full manifest 中的实测主控净时间，并取消
轮数上限：

```powershell
python -m harness_agent.cli run-standard-worker-loop `
  --guidance-mode none `
  --unlimited-rounds `
  --match-controller-budget-from outputs/full/standard_worker_loop_manifest.json `
  <其余与 Full 完全相同的参数>
```

此时 None 可以完成多于或少于三轮，但两侧的输入、模型、seed、solver 单次超时、Worker 单次上限、
repair 次数、并行数和 lane 数必须保持一致。合同比较器同时接受传统的同轮数协议与这种主控净时间
匹配协议；若 None 的预算与 Full 实测时间不一致，则报告为 `protocol_invalid`。

## 9. 验收建议

后续每个迁移项都应以可观察验收条件结束：

- 事件流：服务重连后从最后 sequence 续传，不丢失、不重复展示。
- 完整性：人为删除一份 Core 回执或修改候选文件后，报告必须定位具体引用。
- 备份恢复：恢复任务的输入、正式 incumbent、回合历史和哈希一致，凭据不进入备份。
- Agent 续接：只有可重试类别自动续接；context limit 使用新会话但保留已物化工作区；悬空工具
  调用不得盲目重放。
- 后继任务：继承点必须能追溯到固定 Core 的合法回执。
- 规划模式：Web 创建的 `fast` 与 `research` 任务在 `web_job_status.json` 中准确持久化。
