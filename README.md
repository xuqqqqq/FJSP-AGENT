# FJSP Harness Agent

面向标准 FJSP 及其变种问题的算法生成与持续演进平台。平台后端不内置
portfolio、局部搜索、AWLS 等具体求解算法；算法知识放在知识库、Skill 和
method package 中，由 Coding Agent 根据需求、IO、算例诊断和历史证据写出
独立 solver，固定 Core 只负责合法性验证、评测和晋升。

## 核心边界

- **Main Agent**：第一阶段根据任务与实例画像选择一个主方法族和必要的兼容补充方法族，第二阶段读取定向知识后签发 `WorkerAssignment`。
- **Coding Agent**：通过 OpenCode 自主加载 Harness 精确匹配并授权的 Worker Implementation Skills，按需读取 `read_set` 中的合同、骨架和知识卡，在所选方法族内设计实现组合。
- **候选预检**：非 Agent 的确定性门禁，只检查编译、修改范围、受保护文件、后端导入和明显硬编码。
- **Core**：唯一结果裁决者；固定 parser/validator/evaluator 负责合法性、指标、promotion/rollback 和证据记录。
- **知识层**：domain pack、知识卡、Skill、method package 和已验证经验。

Core 不提供 FJSP 搜索代码。`knowledge/method_packages/` 中可以保存完整方法参考，
但这些参考只能作为 Coding Agent 的学习材料，不能被编排层直接调用。

## 目录结构

```text
harness_agent/
  agents/          Main Agent、incumbent 审计和历史兼容组件
  context/         契约、Context Packet、RAG、压缩、项目扫描
  core/            固定执行器、evaluator 协议、账本和证据
  domains/         FJSP/FJSP-SDST IO、算例诊断、domain pack
  orchestration/   baseline、逐轮演进、同轮修补、晋升/回滚
  slots/           可选代码槽插件协议，默认 Web 流程不启用
  web/             Web API、任务历史和静态前端
  workers/         OpenCode / DeepSeek Coding Agent 适配器
  cli.py           命令行入口
  worker.py        通用 Coding Worker 协议

.opencode/
  agents/          Main、专用只读子 Agent 和隔离 Worker 角色
  skills/          Main-to-Worker 任务书执行协议

domain_packs/      问题族能力、特征到知识的映射
knowledge/         方法知识、论文卡、经验卡和 method package
examples/          固定 evaluator、示例文档与小算例
tests/             单元与闭环回归测试
```

更详细的职责和数据流见 [架构说明](docs/architecture.md)。

## 环境

项目使用仓库内 `uv` 环境：

```powershell
uv sync
uv run python -m harness_agent.cli worker-status
```

DeepSeek 配置放在仓库根目录 `.env` 或 `.env.local`：

```text
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
```

OpenCode 由 `OPENCODE_EXECUTABLE` 和 `OPENCODE_MODEL` 配置。未设置模型时默认使用
`deepseek/deepseek-v4-pro`，避免非交互任务在新 worktree 中等待模型选择。OpenCode 是
Coding Agent 运行时，DeepSeek 是其中使用的模型/provider，两者不是两个并列 Coding Agent。

OpenCode Main 可使用最多四个原生只读子 Agent 审核需求、证据、计划和候选策略；Coding Worker 的 `task`
权限被硬禁用。baseline、正式轮和每次同轮修补都会保存独立任务书，缺少合法任务书时
Worker 不启动。完整 Context Packet 不会传给 Worker。

一次方向可以由最多四个隔离 Coding Worker 竞争实现。每个候选从同一 incumbent 开始，分别经过
确定性候选预检和固定 Core，只有 Core 合法且目标最优的候选进入 promotion check；默认仍为 1 个候选。

Main 与 Worker 当前均使用独立的 `opencode run` 新 session。已评估常驻
`opencode serve --attach`，但 OpenCode 1.17.11 的服务配置在启动时加载，不能为每个
assignment 可靠切换精确的 `read_set`、`target_file` 和工具权限。为避免跨任务权限泄漏，
在 OpenCode 支持请求级 runtime permission 前不启用 attach。

## 启动 Web

```powershell
uv run python -m harness_agent.cli serve-web --host 127.0.0.1 --port 7860
```

浏览器访问 <http://127.0.0.1:7860/>。页面只保留平台参数：

- 需求文档、IO 文档、算例和可选 LB/UB/BKS；
- 迭代轮次、随机种子、Core 并行数和单次超时；
- Worker 单轮时间、步数、同轮修补次数和晋升复验次数；
- Main 子 Agent 上限和竞争 Coding Worker 数量（均不超过 4）。

页面不再要求用户选择求解器、AWLS 参数、邻域或代码槽。算法方向由 Main Agent
根据实际任务自动选择。

## 命令行闭环

```powershell
uv run python -m harness_agent.cli run-standard-worker-loop `
  --doc examples/fjsp_sdst_fattahi_requirement.md `
  --doc examples/fjsp_sdst_fattahi_io.md `
  --instance-dir examples `
  --pattern fjsp_sdst_hudata_tiny.txt `
  --output-dir outputs/agent_generated_smoke `
  --worker opencode `
  --iterations 3 `
  --seeds 0 `
  --max-steps 4 `
  --max-runtime-seconds 120 `
  --in-round-repair-attempts 3 `
  --apply-worker
```

这条命令始终执行 Agent-generated baseline：

1. 构建任务契约和 Context Packet。
2. Main Agent 根据任务与实例画像选择一个方法族和 `knowledge_query`，此时看不到具体方法包。
3. Harness 定向检索详细实现卡和匹配包，Main 再形成完整方向并签发最小 `worker_assignment.json`。
4. Coding Agent 只按 assignment 创建初始 solver。
5. 确定性预检拒绝越权修改、编译错误和明显安全风险。
6. 固定 Core 运行 parser/validator/evaluator，给出唯一合法性与目标结论。
7. 合法且严格提升才 promotion，否则 rollback。
8. 只有具体阻塞证据才由 Main 签发 `assignment_revision_XXX.json` 做同方向修补。
9. 轮次证据写入假设图、经验记忆和知识使用记录。

## 通用命令

```powershell
# 校验契约
uv run python -m harness_agent.cli validate-contract `
  --contract configs/task_contract.example.json

# 构建有界上下文
uv run python -m harness_agent.cli build-context-packet `
  --contract configs/standard_fjsp_tiny.example.json `
  --output outputs/context_packet.json

# 查看可用 worker
uv run python -m harness_agent.cli worker-status

# 运行固定 Core 契约
uv run python -m harness_agent.cli run `
  --contract configs/task_contract.example.json `
  --output-dir outputs/core_smoke
```

代码槽仍作为可选插件协议保留在 `harness_agent/slots/`，但标准 domain pack 和
默认 Web 流程不预设任何 AWLS 槽。

## 关键产物

每次 Agent 闭环主要生成：

```text
standard_worker_contract.json
context_packet.json
standard_worker_loop_manifest.json
standard_worker_loop_report.md
worker_loop/loop_result.json
worker_loop/loop_report.md
worker_loop/hypothesis_graph.json
worker_loop/hypothesis_graph.md
worker_loop/experience_memory.json
worker_loop/experience_memory.md
worker_loop/skill_usage_records.json
```

经验分三层维护：候选经验、Core 验证经验、跨任务可召回经验。只有问题变种、方法包
和语义验证状态兼容的经验才会在下一任务中召回；具体算例分数和解不得沉淀为方法知识。

## 验证

```powershell
uv run python -m compileall -q harness_agent tests examples
uv run python -m unittest discover -s tests
```

固定 Core 相关测试覆盖 parser/evaluator、候选隔离、确定性预检、结果复验、同轮修补、
promotion/rollback、上下文压缩、经验分层、Web 历史任务和报告自动展示。
