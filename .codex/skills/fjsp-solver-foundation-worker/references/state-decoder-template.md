# 状态与完整 DAG 解码模板

来源依据：`knowledge/references/standard_fjsp/standard_fjsp_agent_generated_reference_skeleton.md` 与 `knowledge/method_packages/standard_fjsp_awls_hgtsa/reference_solver.py`。这是接口模板，不是可直接替换活动 solver 的完整文件。

```python
Op = tuple[int, int]

@dataclass
class SearchState:
    machine_of: dict[Op, int]
    machine_sequences: dict[int, list[Op]]

    def clone(self) -> "SearchState":
        return SearchState(dict(self.machine_of), {m: list(seq) for m, seq in self.machine_sequences.items()})
```

完整解码必须同时建立 job arcs 与每台机器相邻 arcs，再做拓扑最早开始传播：

```python
def decode(problem, state):
    if set(state.machine_of) != set(problem.operations):
        return None
    if any(state.machine_of[op] not in problem.eligible[op] for op in problem.operations):
        return None

    succ = {op: [] for op in problem.operations}
    indegree = {op: 0 for op in problem.operations}
    for u, v in problem.job_arcs:
        add_arc(succ, indegree, u, v)
    for seq in state.machine_sequences.values():
        for u, v in zip(seq, seq[1:]):
            add_arc(succ, indegree, u, v)

    ready = stable_zero_indegree_queue(indegree)
    finish = {}
    while ready:
        op = ready.pop()
        start = max((finish[p] for p in predecessors(op)), default=0)
        finish[op] = start + problem.duration[op, state.machine_of[op]]
        release_successors(op, succ, indegree, ready)
    return None if len(finish) != len(problem.operations) else build_schedule(state, finish)
```

实际实现应在建图时保存 predecessor，避免示例中的 `predecessors(op)` 反向扫描。任何 assignment 或 machine order 改变后都重新解码；旧 start/end 只可作为展示或 hint，不能作为新状态事实。

更新必须事务化：对 clone 应用 move，解码成功后才替换 current；只有严格更优的完整合法解才能替换独立 incumbent。deadline 使用单调绝对时间，并为最终解码、验证和序列化留余量。
