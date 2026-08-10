# Re-entrant FJSP 输入输出协议

输入前缀为标准 FJSP。主体读完后必须恰好剩余 `3 * job_count` 个整数，每个 job 一组三元组：

```text
loop_start loop_end repeat
```

字段为 0-based 原始工序编号，且满足 `0 < loop_start <= loop_end < original_op_count - 1`、`repeat >= 2`。内部路线展开为：

```text
pre-loop + loop-body * repeat + post-loop
```

每个 pass 继承原工序的候选机器和加工时间，但可以独立选择机器。展开后重新分配连续 0-based `op_id`。任何缺失、越界或额外尾部 token 都必须拒绝。

输出继续使用 `standard_fjsp_schedule_v1`，并且必须覆盖展开后的全部 `(job_id, op_id)`。目标为最小化 makespan，固定 evaluator 重算合法性和目标值。
