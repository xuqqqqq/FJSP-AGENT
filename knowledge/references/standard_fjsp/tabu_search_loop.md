---
id: operator-tabu-search-loop
type: operator
title: 禁忌搜索主循环
tags: [operator, tabu-search, local-search, metaheuristic]
source: derived_from_fjsp_literature
status: seed
---

## 作用

禁忌搜索用于在局部邻域中持续探索，避免普通贪心局部搜索陷入局部最优。

## 输入

1. 初始合法解。
2. 邻域生成器。
3. 评价函数。
4. 禁忌期限。
5. 时间或迭代预算。

## 输出

搜索过程中发现的最优合法解。

## 伪代码

```text
best = current = initial_solution
tabu_list = {}
while budget remains:
    moves = generate_neighbors(current)
    candidates = []
    for move in moves:
        if move is tabu and not aspiration(move):
            continue
        candidate = apply_and_redecode(current, move)
        if feasible(candidate):
            candidates.append(candidate)
    current = best candidate by objective
    update tabu_list with reverse move
    if objective(current) < objective(best):
        best = current
return best
```

## 约束安全性

禁忌搜索本身不保证可行，安全性来自：

1. 邻域移动的可行性过滤。
2. 移动后的重解码。
3. 外部校验器。

## 适用阶段

1. 标准 FJSP 缩小 best-known gap。
2. 华为工业算例的第二阶段局部修复。
3. LLM 自演进框架中的可插拔元启发式骨架。

## 失败模式

1. 邻域过弱，只在同质解附近打转。
2. 邻域过强，生成大量不可行解。
3. 禁忌期限固定不适合不同规模实例。

