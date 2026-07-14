# Knowledge Quality Audit 2026-07-12

## 结论

当前知识库不是“没内容”，而是凝练质量不均衡：SDST 方法卡已经有较多可迁移经验，标准 FJSP 的基础 IO 卡反而偏薄；一些经验卡更像实验记录，缺少 worker 当轮必须执行的 `must / avoid / check` 条目。结果是 coding agent 会读到很多背景，但不一定能把最关键的规则落实到代码。

## 已确认有效的部分

- `fjsp_variant_domain_pack_rag` 的边界是对的：算法知识放在 domain pack、knowledge、skill，后端只做选择、上下文和评测。
- `agent_generated_variant_quality_contracts` 的方向是对的：要求 parser、稳定操作身份、构造器、覆盖、合法性和自检证据。
- SDST 经验卡已经把“操作级 ready-list、setup-aware dispatch、multi-start、完整 decoder、critical/local-search 先后顺序”写成了方法级经验，没有要求复现某个具体算例分数。
- RAG 选择现在已经能区分标准 FJSP 和 SDST，标准 FJSP 不再默认混入 SDST priority cards。

## 主要缺口

- 标准 FJSP IO 卡之前没有明确强调 packed job-line 解析，导致 worker 可能按“一个物理行一个工序”写 parser。
- 一些知识卡偏长，缺少短的执行清单。worker 在长上下文里容易抓住不重要的 tie-break 描述，而漏掉 parser/decoder 这种地基约束。
- 经验沉淀还没有形成分层：一次运行内反馈、跨运行方法经验、失败反模式、正式 domain-pack 知识没有清晰的晋升条件。
- 检索效果只有 `knowledge_selection` 这样的选择记录，缺少“本轮实际引用了哪张卡、实现了哪条规则、评测后是否有效”的闭环追踪。
- 审查器以前更擅长抓硬编码 toy parser，但对真实标准 FJSP parser 反模式识别不足。

## 本次已落实的修正

- 强化 `knowledge/benchmarks/standard_fjsp_format.md`：加入标准 FJSP packed job-line token cursor 规则和反模式。
- 强化 `solver_contract.md`：把 packed-line parser 作为 agent-generated solver 的 active IO parser 合同之一。
- 强化 DeepSeek worker prompt：标准 FJSP 无 setup 时必须按 job-line token cursor 解析，完整 solver JSON 要保持短小可解析。
- 强化 JA 审查：标准 FJSP 场景下，如果 parser 在 operation loop 内按 `lines[idx]` 读取并推进物理行索引，会被标记为 packed-line parser 反模式并进入同轮修补反馈。
- 加入 DeepSeek API 外层 deadline，避免本地代理或网络卡住时让任务长时间悬挂。

## 后续建议

- 把知识卡拆成三层：IO/合法性硬规则、方法策略、失败反模式。worker prompt 优先放硬规则和最近失败反模式。
- 每轮报告记录 knowledge card hit、worker declared use、code evidence、evaluation effect，形成“知识/Skill 使用与效果追踪”。
- 经验晋升要有门槛：单次有效只进 run memory，多次跨种子有效才进 method card；不写具体解和目标 makespan，只写方法条件、适用场景和反例。
