# 并行组批加工 FJSP IO

输入是 Fattahi 两字段头部 `job_count machine_count`，随后逐工件给出标准候选机主体。主体后严格追加：

```text
K
machine_id capacity       # 重复 K 行
F
family_id_0 ... family_id_{job_count-1}
```

机器和 family 均为 0-based。解析器必须完整消费尾部并拒绝缺失、多余、重复或越界字段。

输出沿用 `standard_fjsp_schedule_v1`。批处理机上的每条记录必须包含整数 `batch_id`；同一机器上相同 `batch_id` 表示同一批次。标准机器可以省略 `batch_id`。

```json
{"job_id": 0, "op_id": 0, "machine_id": 0, "batch_id": 7, "start": 0, "end": 45}
```

批内较短工序的 `end-start` 仍等于整个批次时长，而不是该工序自身加工时长。固定 Evaluator 根据候选加工时长重算批次最大值。
