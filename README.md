# FJSP Harness Agent

面向标准 FJSP 及其变种问题的算法生成与持续演进平台。平台后端不内置
portfolio、局部搜索、AWLS 等具体求解算法；算法知识放在知识库、Skill 和
method package 中，由 Coding Agent 根据需求、IO、算例诊断和历史证据写出
独立 solver，固定 Core 只负责合法性验证、评测和晋升。

## 核心边界

- **Main Agent**：从需求、IO、算例特征、知识检索和历史反馈中选择一个改进方向。
- **Coding Agent**：通过 OpenCode 读取上下文并创建或增量修改 solver。
- **Judgment Agent**：执行前检查语法、修改范围、完整性和确定性安全风险。
- **Semantic Reviewer**：用检索到的方法契约检查“声明与实现是否一致”，不按函数名猜实现。
- **Core**：固定 parser/evaluator、实验执行、promotion/rollback 和证据记录。
- **知识层**：domain pack、知识卡、Skill、method package 和已验证经验。

Core 不提供 FJSP 搜索代码。`knowledge/method_packages/` 中可以保存完整方法参考，
但这些参考只能作为 Coding Agent 的学习材料，不能被编排层直接调用。

## 目录结构

```text
harness_agent/
  agents/          Main Agent、JA、语义审查、经验图谱
  context/         契约、Context Packet、RAG、压缩、项目扫描
  core/            固定执行器、evaluator 协议、账本和证据
  domains/         FJSP/FJSP-SDST IO、算例诊断、domain pack
  orchestration/   baseline、逐轮演进、同轮修补、晋升/回滚
  slots/           可选代码槽插件协议，默认 Web 流程不启用
  web/             Web API、任务历史和静态前端
  workers/         OpenCode / DeepSeek Coding Agent 适配器
  cli.py           命令行入口
  worker.py        通用 Coding Worker 协议

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

OpenCode 由 `OPENCODE_EXECUTABLE` 和 `OPENCODE_MODEL` 配置。OpenCode 是 Coding
Agent 运行时，DeepSeek 是其中使用的模型/provider，两者不是两个并列 Coding Agent。

## 启动 Web

```powershell
uv run python -m harness_agent.cli serve-web --host 127.0.0.1 --port 7860
```

浏览器访问 <http://127.0.0.1:7860/>。页面只保留平台参数：

- 需求文档、IO 文档、算例和可选 LB/UB/BKS；
- 迭代轮次、随机种子、Core 并行数和单次超时；
- Worker 单轮时间、步数、同轮修补次数和晋升复验次数。

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
2. Main Agent 选择一个方法方向。
3. Coding Agent 创建初始 solver。
4. JA 和语义审查先检查候选。
5. 固定 Core 运行 parser/evaluator。
6. 合法且严格提升才 promotion，否则 rollback。
7. 失败可在同一方向内修补，不浪费新的用户可见轮次。
8. 轮次证据写入假设图、经验记忆和知识使用记录。

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

固定 Core 相关测试覆盖 parser/evaluator、候选隔离、JA、语义审查、同轮修补、
promotion/rollback、上下文压缩、经验分层、Web 历史任务和报告自动展示。
