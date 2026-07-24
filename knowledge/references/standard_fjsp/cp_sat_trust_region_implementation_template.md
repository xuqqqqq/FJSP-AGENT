# CP-SAT 与信任域模板

来源依据：`knowledge/references/standard_fjsp/cp_sat_hybrid_blueprint.md`。API 名称按 OR-Tools 风格表达，只有 runtime contract 已提供相应依赖时才能使用。

```python
for op in operations:
    start[op] = model.new_int_var(0, horizon, f"s_{op}")
    end[op] = model.new_int_var(0, horizon, f"e_{op}")
    choices = []
    for machine, duration in eligible[op]:
        present[op, machine] = model.new_bool_var(f"p_{op}_{machine}")
        interval[op, machine] = model.new_optional_interval_var(
            start[op], duration, end[op], present[op, machine], f"i_{op}_{machine}"
        )
        choices.append(present[op, machine])
    model.add_exactly_one(choices)

for u, v in job_arcs:
    model.add(end[u] <= start[v])
for machine in machines:
    model.add_no_overlap(intervals_on[machine])
model.minimize(makespan)
```

局部精确修复只释放明确区域：区域外固定 incumbent machine，必要时固定机器相对顺序；区域内用 `sum(choice_changed) <= max_changes` 限制 assignment trust region。hint 不是约束，导出后仍走独立 validator。

只从 `FEASIBLE/OPTIMAL` 导出候选；`UNKNOWN/INFEASIBLE/MODEL_INVALID`、超时或异常均返回原 incumbent。短 probe 只测试模型响应，主要求解应获得连续预算，避免大量重建模型。
