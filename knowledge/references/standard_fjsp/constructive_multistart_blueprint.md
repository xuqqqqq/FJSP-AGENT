---
id: standard-fjsp-constructive-multistart-blueprint
type: implementation_blueprint
title: 标准 FJSP 多起点构造与可行解码实现蓝图
tags: [fjsp, construction, initialization, multi-start, decoder, load-balance]
status: curated_reference
---

# 标准 FJSP 多起点构造与可行解码实现蓝图

本卡用于从零建立合法 baseline，或修复“初解单一、入口坍缩”的问题。它不是完整深搜索
方法，也不能替代后续局部搜索。

## 1. 必须实现的状态

- `assignment[op] -> (machine, duration)`。
- `machine_sequences[machine] -> [op, ...]`。
- 作业内固定 precedence。
- 独立 `incumbent`，任何失败构造不得覆盖它。

只保存 start/end 的列表不是可修改搜索状态。后续换机和重排必须能回到 assignment 与
machine sequence。

## 2. 构造器组合

保留少量互补入口，不要生成许多同一规则的参数变体：

1. 最早完成时间入口。
2. 负载平衡入口。
3. 剩余作业工作量/关键压力入口。
4. 有界随机化入口：只在得分接近的候选中按 seed 采样。

```python
def construct(problem, rule, rng, deadline):
    state = empty_state(problem)
    ready = first_operations(problem)
    while ready and monotonic() < deadline:
        choices = []
        for op in ready:
            for option in op.candidates:
                score = rule.score(op, option, state)
                choices.append((score, stable_op_key(op), option))
        op, option = choose_bounded_rcl(choices, rng)
        assign_and_append(state, op, option)
        release_job_successor(ready, op)
    return decode_or_none(problem, state)
```

## 3. 解码合同

解码器同时加入作业边和机器相邻边，再做拓扑最早开始传播：

- 每道工序恰好出现一次。
- 机器必须属于候选集合，duration 必须匹配。
- 图有环、进度停滞或工序缺失时返回失败。
- assignment 或 machine order 改变后必须重新解码。

## 4. 多样性不是列表长度

至少记录两个指纹：

```python
assignment_fp = tuple(machine_of[op] for op in canonical_ops)
order_fp = tuple(tuple(machine_sequences[m]) for m in machines)
```

只保留指纹不同的入口。若不同规则产生相同指纹，应减少构造预算，把时间留给改进阶段。

## 5. 选择与预算

- 始终保留当前 makespan 最好的合法入口。
- 额外入口按 assignment/order 距离和负载结构选择，而不是只按 makespan 排名。
- 构造阶段使用共享绝对 deadline，并预留验证、序列化和后续搜索时间。
- 没有足够时间时返回最好的合法入口，不启动新重启。

## 6. 常见伪实现

- 多次运行完全相同的确定性规则，却称为 multi-start。
- 随机选择机器但没有负载或完成时间约束。
- 改 assignment 后沿用旧机器序列或旧 start time。
- 为每个入口运行完整深搜索，耗尽最终预算。
- 只比较对象地址或列表身份，不比较结构指纹。

## 7. 验收证据

- 固定 seed 下结果可复现，不同 seed 只影响受控随机分支。
- 至少两个构造规则在测试实例上产生不同 assignment 或 order 指纹。
- 每个返回入口都通过独立解码和完整合法性自检。
- deadline 到达时仍能返回已有 incumbent。
