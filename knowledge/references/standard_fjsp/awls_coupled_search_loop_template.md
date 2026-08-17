# AWLS/HGTSA 耦合搜索模板

来源依据：`knowledge/method_packages/standard_fjsp_awls_hgtsa/behavior_contract.md`、`knowledge/method_packages/standard_fjsp_awls_hgtsa/reference_solver.py` 和 `knowledge/references/standard_fjsp/standard_fjsp_awls_hgtsa_execution_skeleton.md`。

## 邻域闭环

```python
current = decode_or_fail(initial_state)
incumbent = current.clone()
tabu = SequenceTabuList(problem.machine_count)

while before_deadline():
    critical = extract_critical_path_and_blocks(current)
    moves = []
    moves += n7_n8_same_machine_moves(critical, current)
    moves += alternative_machine_insertions(critical, current, problem)

    chosen = None
    for move in bounded_moves(moves):
        if tabu.contains(reverse_signature(move)) and not aspiration(move, incumbent):
            continue
        candidate = apply_to_clone_and_decode(current, move)
        if candidate is None:
            continue
        score = objective(candidate) + adaptive_penalty(candidate, move)
        chosen = min_by_score(chosen, (score, move, candidate))

    if chosen is None:
        current = bounded_restart_or_perturb(incumbent)
        continue
    move, current = chosen.move, chosen.candidate
    tabu.add(reverse_signature(move), dynamic_tenure(problem, current))
    update_operation_weights(current, move)
    if current.makespan < incumbent.makespan:
        incumbent = current.clone()
return incumbent
```

同机移动包括关键块内部相邻交换、块首/块尾插入和有界短块重排；换机移动必须同时选择合法目标机器与插入位置。所有移动都要应用到克隆状态并完整重解码，不能只更新局部 `start/end`。

## AWLS 关键状态

- `current` 可按接受策略暂时变差，`incumbent` 只保存严格更优合法解。
- tabu 使用可逆移动/局部序列签名，并允许严格改善 incumbent 的 aspiration。
- operation weight、cooldown、critical status 和移动工序必须真正进入评分/更新路径；只有变量存在不算 AWLS 激活。
- 关键块可从单条关键路径提取，也可在停滞时有界扫描多条/全部关键分支，但不能每轮无界枚举。

必须输出各邻域的 generated/evaluated/accepted/improved 计数、同机/换机分布、tabu 命中、aspiration、迭代、重启、阶段耗时和 best trajectory。
