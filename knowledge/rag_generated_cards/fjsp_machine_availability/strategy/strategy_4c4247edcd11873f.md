# LightRAG Generated Knowledge Card

- Problem family: fjsp_machine_availability
- Stage: strategy
- Source: LightRAG
- Working dir: /home/wbw20/WorkSpace/Programs/FJSP_LLM_Demo/demo3/FJSP-AGENT/knowledge/fjsp_kb
- Query mode: mix
- Top k: 10
- Cache key: 4c4247edcd11873f
- Created at: 2026-07-29T05:19:19.567128+00:00
- Tags: machine_availability, machine_calendar, maintenance, maintenance_window

## Query

在 Flexible Job-Shop Scheduling Problem with machine availability constraints、FJSP-NFA、maintenance windows 或 preventive maintenance 调度问题中，检索与以下主题相关的算法选择、建模边界和实现注意事项：机器存在预先给定的不可用维修区间，调度时工序不能跨越或落入不可用窗口。；将机器可用和不可用时间窗口作为解码、插入和局部移动评估的机器日历。；设备维修、预防维护或不可用区间对 FJSP 调度目标和可行性的影响。；半开维修窗口 [start, end) 的建模、合并、跳过和冲突检测。。重点关注如何从标准 FJSP 迁移到机器不可用区间约束。

## Retrieved Knowledge

Knowledge Graph Data (Entity):

```json
{"entity": "FJSP With Maintenance Activities", "type": "concept", "description": "在原始FJSP问题基础上扩展的，每台机器在计划期内考虑一次预防性维护活动的柔性作业车间调度问题。"}
{"entity": "Chan, Chung, Chan, Finke, and Tiwari (2006)", "type": "content", "description": "该论文考虑受机器维护约束的分布式柔性制造系统调度问题，维护时间与机器年龄相关，该问题也涵盖具有维护活动的柔性作业车间调度。"}
{"entity": "FJSSP-nfa实例", "type": "concept", "description": "柔性作业车间调度问题中带有非固定活动(维护任务)的实例，用于评估所提出的配方。"}
{"entity": "Flexible Job-Shop Scheduling Problem (FJSP)", "type": "concept", "description": "柔性作业车间调度问题，每道工序可在多台可选机器上加工，是生产调度与预防性维护集成研究的一个复杂环境。"}
{"entity": "FJSP标准实例", "type": "data", "description": "用于评估FJSP算法性能的基准测试数据集。"}
{"entity": "Flexible Job Shop Scheduling Problem", "type": "concept", "description": "一种制造调度问题，涉及在机器上分配作业，其中机器具有灵活性，本文考虑的是带可用性约束的变体。<SEP>柔性作业车间调度问题，在参考文献62和64中被灰狼优化和化学反应优化算法解决。"}
{"entity": "FJSSP-nfa", "type": "concept", "description": "一种灵活的作业车间调度问题，具有非固定可用性约束，包含n个作业和m台机器，每台机器需要调度维护任务，维护任务必须在给定时间窗口内完成，不允许作业抢占。<SEP>带有非固定可用性约束的柔性作业车间调度问题(FJSSP-nfa)，其中维护任务具有确定性的持续时间和必须被调度的时间区间，是本文及多篇文献的研究焦点。<SEP>具有不可用时间窗口的柔性作业车间调度问题，在经典FJSSP基础上添加了维护任务<SEP>一种柔性作业车间调度问题变体，其结果在出版物中以表格形式呈现。<SEP>柔性作业车间调度问题的一个变体，具体指无柔性替代的问题设置。<SEP>带可用性约束的柔性作业车间调度问题，本研究考虑的具体问题。"}
{"entity": "Meta-heuristic Methods", "type": "method", "description": "前期研究中用于求解带可用性约束的柔性作业车间调度问题的启发式算法。"}
{"entity": "FJSSP-nfa基准测试集", "type": "data", "description": "由经典FJSSP实例生成的、包含维护任务的新基准测试集，共包含2010个FJSSP-nfa实例"}
{"entity": "FBS Algorithm", "type": "method", "description": "一种基于集束搜索的启发式算法，通过MNew_NON过程生成节点，并使用M_EET作为评价函数，求解带有维护活动的柔性作业车间调度问题。"}
{"entity": "柔性作业车间调度问题(FJSP)", "type": "concept", "description": "一种扩展的作业车间调度问题，假设一个工序可以在多台机器上加工。"}
{"entity": "Flexible Job-shop Scheduling Problem", "type": "concept", "description": "一种复杂的生产调度问题，其中每个工件有多道工序，每道工序可在多台可选机器上加工，同时需考虑预防性维护任务的安排。<SEP>一种复杂的调度问题，其中作业包含多个工序，每个工序可以在多台可选的机器上加工，目标是优化某些性能指标。"}
{"entity": "最大工作负载", "type": "concept", "description": "调度优化目标之一，指所有机器中的最大工作负载，在多目标FJSSP-nfa优化中被考虑。"}
{"entity": "Chemical-Reaction Optimization", "type": "method", "description": "化学反应优化算法，在参考文献64中被用于解决带有维修活动的柔性作业车间调度问题。"}
{"entity": "https://scheduling.cc", "type": "location", "description": "提供FJSSP-nfa基准测试集实例的网站"}
{"entity": "New Benchmark", "type": "data", "description": "基于经典FJSSP基准问题生成的新实例集，包含超过2000个实例，用于测试求解方法。"}
{"entity": "Jing et al. (2017)", "type": "content", "description": "Jing et al. (2017)研究了基于概率的预防性维护FJSSP-nfa变体，即当机器故障概率达到特定阈值时执行预防性维护，并使用多目标遗传算法求解。"}
{"entity": "Gao, Gen, and Sun (2006)", "type": "content", "description": "该论文研究具有机器可用性约束的柔性作业车间调度问题，不可用时段灵活且需在调度过程中确定，并提出混合遗传算法。"}
{"entity": "Wang et al. (2021)", "type": "content", "description": "Wang et al. (2021)求解FJSSP-nfa时综合考虑了预防性维护、运输过程和能源消耗，采用多目标进化算法。"}
{"entity": "Mixed Integer Model", "type": "method", "description": "本文开发的用于高效求解带可用性约束的柔性作业车间调度问题的数学规划模型。"}
{"entity": "Flexible Job Shop Scheduling Problem (FJSSP)", "type": "concept", "description": "一种作业车间调度问题的泛化，允许工序在限定的机器集合上调度，首次由Brucker和Schlie在1990年提出。"}
{"entity": "Scheduling Sub-Problem", "type": "concept", "description": "柔性作业车间调度问题的子问题之一，负责在选定的机器上对工序进行排序，以优化目标值。"}
{"entity": "Distributed Flexible Manufacturing System (FMS)", "type": "concept", "description": "分布式柔性制造系统，涉及多个生产单元，维护时间与机器年龄相关。"}
{"entity": "Flexible Job-Shop Scheduling Problem", "type": "concept", "description": "柔性作业车间调度问题，是FJSSP-nfa的基础问题。"}
```

Knowledge Graph Data (Relationship):

```json
{"entity1": "FJSSP-nfa", "entity2": "Proposed Formulation", "description": "提出的数学规划模型用于求解FJSSP-nfa实例。"}
{"entity1": "Chan, Chung, Chan, Finke, and Tiwari (2006)", "entity2": "Flexible Job-Shop Scheduling Problem (FJSP)", "description": "该论文的问题涵盖具有维护活动的柔性作业车间调度。"}
{"entity1": "FJSSP-nfa", "entity2": "当前研究", "description": "当前研究针对FJSSP-nfa问题进行深入研究，提出混合整数规划解法，并评估维护任务对调度的影响。"}
{"entity1": "Flexible Job Shop Scheduling Problem", "entity2": "Meta-heuristic Methods", "description": "前期研究中的元启发式方法用于解决带可用性约束的柔性作业车间调度问题。"}
{"entity1": "FJSSP-nfa", "entity2": "FJSSP-nfa基准测试集", "description": "该基准测试集包含FJSSP-nfa问题的实例"}
{"entity1": "FBS Algorithm", "entity2": "Flexible Job-shop Scheduling Problem", "description": "FBS算法是针对带有维护活动的柔性作业车间调度问题设计的启发式方法。"}
{"entity1": "FJSSP-nfa", "entity2": "Table 5", "description": "Table 5呈现了FJSSP-nfa问题的求解结果。"}
{"entity1": "Chemical-Reaction Optimization", "entity2": "Flexible Job Shop Scheduling Problem", "description": "化学反应优化算法被用于解决带有维修活动的柔性作业车间调度问题。"}
{"entity1": "FJSSP-nfa", "entity2": "Gao et al. (2006)", "description": "Gao et al. (2006)首次引入了带有非固定可用性约束的柔性作业车间调度问题(FJSSP-nfa)。"}
{"entity1": "Flexible Job Shop Scheduling Problem", "entity2": "New Benchmark", "description": "新基准包含针对带可用性约束的柔性作业车间调度问题生成的实例。"}
{"entity1": "FJSSP-nfa", "entity2": "可用性约束", "description": "FJSSP-nfa问题明确考虑了机器的可用性约束。"}
{"entity1": "Flexible Job-Shop Scheduling Problem (FJSP)", "entity2": "Gao, Gen, and Sun (2006)", "description": "该论文研究了柔性作业车间调度问题。"}
{"entity1": "FJSSP-nfa", "entity2": "Tom Perroux", "description": "Tom Perroux等人在该研究中考虑了FJSSP-nfa问题。"}
{"entity1": "Flexible Job Shop Scheduling Problem", "entity2": "Mixed Integer Model", "description": "该混合整数模型旨在解决柔性作业车间调度问题。"}
{"entity1": "FJSSP-nfa", "entity2": "维护任务", "description": "FJSSP-nfa问题在经典FJSSP基础上添加了维护任务，导致机器出现不可用时间窗口"}
{"entity1": "Flexible Job Shop Scheduling Problem (FJSSP)", "entity2": "Scheduling Sub-Problem", "description": "柔性作业车间调度问题可分解为调度子问题，该子问题负责对所选机器上的工序进行排序。"}
{"entity1": "FJSSP-nfa", "entity2": "混合遗传算法", "description": "混合遗传算法被应用于求解FJSSP-nfa，在小型基准上进行了测试。"}
{"entity1": "Distributed Flexible Manufacturing System (FMS)", "entity2": "Flexible Job-Shop Scheduling Problem (FJSP)", "description": "分布式柔性制造系统调度问题涵盖具有维护活动的柔性作业车间调度。"}
{"entity1": "FJSSP-nfa", "entity2": "MIP模型", "description": "MIP模型被提出用于求解FJSSP-nfa问题。"}
{"entity1": "FJSSP-nfa", "entity2": "Flexible Job-Shop Scheduling Problem", "description": "FJSSP-nfa是柔性作业车间调度问题中带有非固定可用性约束的变体。"}
{"entity1": "FJSSP-nfa", "entity2": "多目标进化算法", "description": "多目标进化算法被应用于求解考虑多因素的FJSSP-nfa。"}
{"entity1": "FJSSP-nfa", "entity2": "IFAC PapersOnLine 56-2 (2023) 5388–5393", "description": "该出版物报告了FJSSP-nfa的求解结果。<SEP>该论文报告了FJSSP-nfa的计算结果。"}
{"entity1": "FJSSP-nfa", "entity2": "HurinkE", "description": "HurinkE是FJSSP-nfa问题的一组实例。<SEP>FJSSP-nfa问题包含HurinkE实例类。"}
{"entity1": "FJSSP-nfa", "entity2": "MIP", "description": "MIP模型用于求解FJSSP-nfa问题"}
{"entity1": "FJSSP-nfa", "entity2": "HurinkS", "description": "HurinkS是FJSSP-nfa问题的一组实例。<SEP>FJSSP-nfa问题包含HurinkS实例类。"}
{"entity1": "FJSSP-nfa", "entity2": "Li and Pan (2012)", "description": "Li and Pan (2012)针对FJSSP-nfa提出化学反应优化算法，考虑多预防性维护任务，以最小化完工时间、总工作负载和最大工作负载。"}
{"entity1": "FJSSP-nfa", "entity2": "Li et al. (2014)", "description": "Li et al. (2014)提出离散人工蜂群算法求解FJSSP-nfa，同时考虑多预防性维护和多目标优化。"}
{"entity1": "FJSSP-nfa", "entity2": "约束编程", "description": "约束编程被认为是解决FJSSP-nfa问题的一个有前景的方向。"}
{"entity1": "FJSSP-nfa", "entity2": "Mixed integer formulation", "description": "该混合整数规划模型是为FJSSP-nfa问题设计的求解方法。"}
{"entity1": "FJSSP-nfa", "entity2": "过滤波束搜索启发式算法", "description": "过滤波束搜索启发式算法被用于求解带受限维护资源的FJSSP-nfa。"}
{"entity1": "FJSSP-nfa", "entity2": "贪婪随机自适应搜索过程", "description": "贪婪随机自适应搜索过程被应用于FJSSP-nfa的求解。"}
{"entity1": "FJSSP-nfa", "entity2": "基于构造过程的启发式算法", "description": "基于构造过程的启发式算法被用于快速求解FJSSP-nfa。"}
{"entity1": "FJSSP-nfa", "entity2": "双重蚁群算法", "description": "双重蚁群算法被用于求解带固定间隔不可用性的FJSSP-nfa。"}
{"entity1": "FJSSP-nfa", "entity2": "多目标遗传算法", "description": "多目标遗传算法被用于求解基于概率预防性维护的FJSSP-nfa变体。"}
{"entity1": "FJSSP-nfa", "entity2": "Wang and Yu (2010)", "description": "Wang and Yu (2010)进一步探索了FJSSP-nfa，考虑了受限维护资源。"}
{"entity1": "FJSSP-nfa", "entity2": "Jing et al. (2017)", "description": "Jing et al. (2017)研究了FJSSP-nfa的基于概率预防性维护变体。"}
{"entity1": "FJSSP-nfa", "entity2": "首个大型基准测试", "description": "首个大型基准测试是针对FJSSP-nfa问题专门引入的测试数据集。"}
{"entity1": "FJSSP-nfa", "entity2": "Small Instances (less than 100 operations)", "description": "小规模实例是FJSSP-nfa问题的实例集合。"}
{"entity1": "FJSSP-nfa", "entity2": "Large Instances (more than 100 operations)", "description": "大规模实例是FJSSP-nfa问题的实例集合。"}
{"entity1": "FJSSP-nfa", "entity2": "Gao et al.",
