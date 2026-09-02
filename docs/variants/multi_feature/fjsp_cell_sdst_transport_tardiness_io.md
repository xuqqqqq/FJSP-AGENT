# FJCS-SDFSTs-ITTs IO 合同

规范化实例后缀为 `.fjcs.json`，格式字段必须是 `fjsp_cell_sdst_transport_tardiness_instance_v1`。`active_features` 必须完整包含 cell transport、family SDST、reentrant route、due date 和 total tardiness。

实例使用 0-based 内部编号。`machine_cell_ids` 为每个物理机器副本给出 cell；`family_setup_times[a][b]` 表示 family `a` 后接 `b` 的准备时间；`cell_transport_times[a][b]` 表示 cell `a` 到 `b` 的运输时间。每个 job 保留公开数据中的 `source_job_id`，并包含 `family_id`、`due_date` 和按路线顺序排列的候选机/时长对。`jobs[].operations` 本身就是已经完整展开的回流路线，数组下标是独立 `op_id`；格式中没有第二套 `reentrant_route` 或循环展开字段。

输出沿用 `standard_fjsp_schedule_v1`，必须声明 `makespan`、`total_tardiness` 和完整 `schedule`。Core evaluator 独立重算候选资格、加工时长、工件前置、运输、机器互斥、family setup、makespan 与 total tardiness。
