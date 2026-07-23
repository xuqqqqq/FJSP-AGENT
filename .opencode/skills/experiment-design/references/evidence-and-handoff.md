# 证据与交接

## Main 到 Coding Worker

只交付编译后的 `WorkerAssignment`：目标瓶颈、可证伪变异、目标文件与符号、保留和禁止项、实现顺序、activation checks、预算、停止条件，以及按方法族精确匹配的 Worker Implementation Skill ID。不要注入完整知识目录、Main 研究历史或 experiment-design 正文。

## Coding Worker 到 Main

通过现有候选产物返回实际 diff、运行状态、activation telemetry、diagnostic smoke、Core evaluator 和耗时。声明机制未实现或未激活时必须如实保留该结论；不得用函数名或配置项存在代替运行证据。

## Main 的复盘规则

逐候选建立如下证据链：

1. Worker 是否完成预定改动。
2. 候选是否通过预检和 Core 合法性。
3. activation checks 是否证明声明机制执行。
4. 正式 makespan 与耗时如何相对 incumbent 变化。
5. 结果支持、反驳还是无法区分原假设。
6. 下一轮应 `probe`、`scale`、`pivot` 还是 `research_tournament`。

promotion 只由冻结 Core 比较决定。Main 可以解释和规划，但不能覆盖 evaluator 事实。
