# AWLS-SDST 适配说明

## 执行模式

- AWLS 方法参考：`knowledge/method_packages/standard_fjsp_awls_hgtsa/reference_solver.py`。
- 平台参考验证可以使用 `harness_agent.domains.io`；通用编排绝不能导入该方法参考。
- 独立自主生成的 solver 必须在生成产物内部实现活动 IO 派生的 parser 与 setup 查询；不得导入 `harness_agent` 或 evaluator 内部实现。
- 无论采用哪种模式，冻结的 evaluator 都是合法性裁判，setup 区间必须服从活动 IO 契约。

## 已知失效模式

当 AWLS 实现按下面方式传播机器弧时，它对 SDST 就是无效的：

```text
start(current) >= end(previous_on_machine)
```

对 SDST，这里必须改为：

```text
start(current) >= end(previous_on_machine) + setup_time(machine, previous, current)
```

因此，第一步有效的 AWLS-SDST 适配必须先修正 AWLS 内部时间传播，再去调 N7/NK 或 `zi`。

## 应保留的 AWLS 机制

- 关键路径与关键块选择。
- N7/N8 风格的同机关键块 move。
- NK/RK/LK 风格的换机插入窗口。
- sequence tabu 与 aspiration。
- 自适应工序权重和 `zi` 扰动。

## 组件设计指引

完整方法应保持耦合，但每个组件都要有清晰的输入与输出：

- 含 setup 语义的图时间传播与排程输出；
- 含 setup 语义的同机与换机 move 评价；
- 在合法性闭合后启用的自适应评分特征与更新策略。

不要让 worker：

- 新建一个脱离契约的 SDST parser；
- 修改 `standard_fjsp_evaluator.py`；
- 修改 solution JSON schema；
- 在输出记录里隐藏 setup interval；
- 绕过 Core evaluator 报错。

## Benchmark 梯度

1. 编译：`python -m compileall knowledge/method_packages/standard_fjsp_awls_hgtsa/reference_solver.py harness_agent/domains/io.py`。
2. 标准 FJSP smoke：使用 AWLS 运行 Brandimarte Mk01。
3. SDST 合法性 smoke：一个来自当前任务的小实例，固定 seed，短时间限制。
4. SDST 质量 probe：一个有界且结构具有代表性的子集，外部 LB/UB 只用于汇报。
5. 只有在 smoke 合法性稳定后，才进入更广的 benchmark 评估。
