# AWLS-SDST 论文要点

把这里当作压缩后的提示证据，不要当作实现已完成的证明。

## AWLS / HGTSA 家族

- AWLS 风格的 FJSP 搜索通常使用析取图排程、关键路径/关键块邻域、tabu 记忆和自适应工序权重。
- N7/N8 同机移动会围绕关键块重定位工序。
- NK 或 RK/LK 换机插入会利用 head/tail 信息收缩目标位置，而不是扫描每个插入点。
- `zi` 通过工序权重和 cooldown 扰动近似移动评分，但只有在底层时间模型正确后才有意义。

## FJSP-SDST 文献模式

- sequence-dependent setup time 会占用同一台机器上相邻两道工序之间的机器容量。
- 只看 processing time 似乎有利的移动，在计入 setup 插入/移除成本后可能变差。
- setup-aware 局部搜索应同时用 setup 弧更新 head 与 tail timing。
- 对 HUdata 的 job-pair setup，setup 取决于同机前一个 job 与当前 job；对 Fattahi 的 operation-pair setup，setup 取决于前一工序与当前工序。

## NS4S / IJCAI 2025 背景

- NS4S 报告了在 SDST-HUdata 上的 FJSP-SDST 实验，共 20 个实例。
- 论文实验设置给出的 FJSP-SDST 截止时间是 30 秒。
- 其中的 UB/BKS 表只可作为评估参考，不能为了对齐论文表格去修改 evaluator 语义。
