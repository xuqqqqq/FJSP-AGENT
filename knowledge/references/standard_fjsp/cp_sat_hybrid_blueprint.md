---
id: standard-fjsp-cp-sat-hybrid-blueprint
type: implementation_blueprint
title: 标准 FJSP CP-SAT 与局部精确混合实现蓝图
tags: [fjsp, cp-sat, exact-method, trust-region, hybrid]
status: curated_reference
---

# 标准 FJSP CP-SAT 与局部精确混合实现蓝图

本卡用于小中型实例的完整精确建模，或大型实例的有界局部精确修复。使用前先确认运行
环境确实提供 CP-SAT 依赖；缺少依赖时不能在 Worker 阶段擅自安装。

## 1. 完整模型的最小闭环

对每道工序建立一个主 `start/end` 和多个候选机器可选区间：

```python
for op in operations:
    start[op], end[op] = int_vars(horizon)
    for machine, duration in candidates[op]:
        present[op, machine] = bool_var()
        interval[op, machine] = optional_interval(
            start[op], duration, end[op], present[op, machine]
        )
    add_exactly_one(present[op, machine] for machine in candidates[op])

for job in jobs:
    add(end[job[k]] <= start[job[k + 1]])
for machine in machines:
    add_no_overlap(intervals_on[machine])
minimize(max(end[last_operation[job]] for job in jobs))
```

必须检查 `OPTIMAL`、`FEASIBLE`、`UNKNOWN`、`INFEASIBLE` 等状态。只有可行状态可以导出
调度；其余状态返回原 incumbent。

## 2. 使用 incumbent 提示

- 为已选机器的 presence literal 提供提示。
- 为 `start/end` 提供一致提示。
- 提示不是约束；模型仍需自行验证可行性。
- 导出结果后重新走独立 schedule validator。

## 3. 机器分配信任域

大型实例不宜无条件释放所有机器选择。选择关键、近关键、过载机器上的少量柔性工序：

```python
changed = []
for op in operations:
    if op not in candidate_ops:
        force_machine(op, incumbent.machine_of[op])
    else:
        changed_op = link_choice_change(op, incumbent.machine_of[op])
        changed.append(changed_op)
add(sum(changed) <= max_changes)
```

可进一步固定 trust region 外的机器相对顺序，或只释放关键窗口。区域过小会原样返回，
区域过大会失去 incumbent 引导并超时，因此区域大小必须是可回退实验参数。

## 4. 时间控制

- 所有 CP 调用共享 solver 总时间上限。
- 启动前检查剩余时间是否覆盖建模、求解、导出和验证。
- 短探测只判断入口响应，不直接替代正式 evaluator。
- 主要最终入口应获得连续预算，避免大量极短调用重复支付建模成本。

## 5. 与启发式的职责边界

- 构造法提供合法提示和 fallback。
- 关键结构诊断选择信任域。
- CP-SAT 负责区域内联合分配与排序。
- 独立 decoder/validator 负责最终合法性。
- incumbent 永远独立保存，`UNKNOWN` 或异常不能清空它。

## 6. 常见伪实现

- 为每个候选机器创建 interval，却没有 `exactly_one`。
- optional interval 的 start/end 与主工序变量没有一致绑定。
- 只建 precedence，没有每台机器 `NoOverlap`。
- 把 `UNKNOWN` 当成可行结果读取。
- 每次局部 move 都重建完整模型且没有时间上限。
- 使用已知最优值固定 makespan 上界，形成答案泄漏。

## 7. 验收证据

- 小实例模型输出通过独立 evaluator。
- 强制某个 assignment 后，模型确实遵守该机器选择。
- trust region 外工序保持不变，区域内改变数不超过上限。
- 超时、UNKNOWN 和异常均返回输入 incumbent。

## 8. 参考来源

- Demir & Isleyen, *Evaluation of mathematical models for flexible job-shop
  scheduling problems*, DOI: `10.1016/j.apm.2012.03.020`。
- Seiler et al., *Choosing constraint programming solvers through machine
  learning*, DOI: `10.1016/j.ejor.2022.01.034`。
- Google OR-Tools Job Shop 文档中的 interval、precedence 与 NoOverlap 建模模式仅作为
  API 参考；活动 IO/evaluator 仍是本项目的权威合同。
