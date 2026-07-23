# Worker 实验执行模板

## 最小插桩

把计数器绑定到机制真正发生的位置，不绑定到函数存在或配置开启：

```python
stats = {
    "generated": 0,
    "evaluated": 0,
    "accepted": 0,
    "improved": 0,
    "decode_failed": 0,
}

for move in generate_moves(state):
    stats["generated"] += 1
    candidate = apply_and_decode(state, move)
    if candidate is None:
        stats["decode_failed"] += 1
        continue
    stats["evaluated"] += 1
    if accept(candidate, state):
        stats["accepted"] += 1
        state = candidate
        if candidate.makespan < incumbent.makespan:
            stats["improved"] += 1
            incumbent = candidate.clone()
```

计数器必须有确定 schema、有界输出，并且关闭机制时保持为零。不要用 makespan 改善代替 `generated/evaluated/accepted`。

## 可归因开关

仅为 Main 指定的因素提供内部开关，默认路径保持候选的完整实现：

```python
config = SearchConfig(use_gap=True, use_reassign=True, use_tabu=False)
```

若 assignment 要求消融，可在相同输入、seed、预算和代码基线上关闭一个因素。不得把开关暴露成无界调参入口，也不得在正式 Core 后反复搜索参数。

## 证据回报

至少报告：实际改动符号、运行命令、返回码、合法性、activation counters、阶段耗时、异常、是否触发 deadline、是否保留 incumbent，以及哪些结论仍只是局部观察。Worker 不宣布 promotion。
