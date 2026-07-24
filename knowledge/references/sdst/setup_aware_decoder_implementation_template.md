# 含 Setup 语义的解码模板

来源依据：`knowledge/references/sdst/awls_sdst_adaptation_implementation.md` 与活动 setup IO 契约。先确定 setup 索引、dummy predecessor、anticipatory/non-anticipatory 语义，再选择下述分支。

```python
def place_after_machine_predecessor(problem, op, machine, machine_pred, job_ready, machine_ready):
    setup = problem.setup_time(machine, machine_pred, op)
    if problem.setup_mode == "non_anticipatory":
        setup_start = max(machine_ready, job_ready)
        process_start = setup_start + setup
    elif problem.setup_mode == "anticipatory":
        setup_start = machine_ready
        process_start = max(job_ready, setup_start + setup)
    else:
        raise UnsupportedSetupContract(problem.setup_mode)
    finish = process_start + problem.duration[op, machine]
    return setup_start, process_start, finish
```

实际 DAG/传播必须把 job ready、machine predecessor、setup interval 和 processing interval 按契约连接。机器顺序、插入、交换、换机、交叉或重启后，前驱改变的 setup 必须重算；不能沿用标准 FJSP delta 或旧 start/end。

关键路径、slack、block 和 move score 都基于 setup-aware 完整时间图。activation telemetry 至少区分 setup lookup、非零 setup contribution、setup-aware move evaluation 和完整重解码次数。
