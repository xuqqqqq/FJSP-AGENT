---
id: standard-fjsp-population-memetic-blueprint
type: implementation_blueprint
title: 标准 FJSP 群体与 Memetic 搜索实现蓝图
tags: [fjsp, population, genetic, memetic, diversity, local-search]
status: curated_reference
---

# 标准 FJSP 群体与 Memetic 搜索实现蓝图

群体方法适合需要跨盆地探索且预算足够的场景。它不是“随机生成很多解”；强实现必须让
编码、解码、交叉、变异、局部改进、去重和替换形成闭环。

## 1. 编码

常用双层编码：

- `machine_gene[op]`：该工序候选机器中的索引。
- `order_gene`：按作业号重复的 operation sequence，某作业第 k 次出现代表其第 k 道工序。

解码时按 order gene 依次取该作业下一道工序，并按 machine gene 选择机器，在满足作业
precedence 和机器可用时间的最早位置安排。任何非法 gene 都应拒绝或确定性修复。

## 2. 初始化

种群应混合：

- 少量最早完成/负载平衡启发式个体。
- 有界随机 machine assignment。
- 有界随机 operation sequence。
- 当前 incumbent（改进轮必须保留）。

按 assignment/order 指纹去重。初始化后若唯一指纹很少，应减少种群或改变生成机制。

## 3. 交叉与变异

```python
def reproduce(parent_a, parent_b, rng):
    child_machine = uniform_machine_crossover(parent_a, parent_b, rng)
    child_order = precedence_preserving_order_crossover(parent_a, parent_b, rng)
    if rng.random() < machine_mutation_rate:
        mutate_one_credible_machine(child_machine, rng)
    if rng.random() < order_mutation_rate:
        mutate_bounded_order_window(child_order, rng)
    return decode_or_repair(child_machine, child_order)
```

order 交叉必须保持每个作业出现次数正确；machine 变异只能选该工序合法候选机器。

## 4. Memetic 局部改进

不要对每个子代运行完整深搜索。可只对以下个体做短局部改进：

- 当前代最好个体。
- 与精英结构距离较大且质量接近的个体。
- 新产生、未见过的关键结构。

局部搜索可使用关键块、机器重分配或 ILS 卡，但必须共享 deadline。

## 5. 替换与精英保留

- 独立保存全局 incumbent。
- 精英保留至少一个最好合法个体。
- 替换同时考虑 makespan 与结构重复度。
- 连续多代无新指纹或无改进时，执行有界重启/扰动，而不是只增加代数。

## 6. 预算与可复现性

- 使用单一 seeded RNG；所有随机分支从它派生。
- 每代和每次局部改进前检查绝对 deadline。
- 预留最终验证和输出时间。
- 相同 seed/输入/预算应复现；Core 仍只接受自己的正式测量。

## 7. 常见伪实现

- 交叉后 operation 出现次数错误，却靠丢工序继续解码。
- 机器基因改变但 order/decoder 不支持插入。
- 种群只是同一个构造器的浅拷贝。
- 只按 makespan 选父代，种群很快坍缩。
- 每个个体都运行昂贵局部搜索，导致只完成极少代。
- 没有独立 incumbent，重启时丢失最好解。

## 8. 验收证据

- 所有解码个体包含每道工序恰好一次。
- machine gene 始终合法，order gene 保持作业 precedence 计数语义。
- 交叉和两类变异在固定单测中产生可观察结构变化。
- 去重、精英保留、停滞重启和 deadline 分支均可到达。
- 任意失败子代不会覆盖全局 incumbent。

## 9. 参考来源

- Pezzella, Morganti & Ciaschetti, *A genetic algorithm for the Flexible
  Job-shop Scheduling Problem*, DOI: `10.1016/j.cor.2007.02.014`。
- Kacem et al., *Pareto-optimality approach for flexible job-shop scheduling
  problems*, DOI: `10.1016/S0378-4754(02)00019-8`。
- Li et al., *An effective hybrid genetic algorithm and tabu search for flexible
  job shop scheduling problem*, DOI: `10.1016/j.ijpe.2016.01.016`。
