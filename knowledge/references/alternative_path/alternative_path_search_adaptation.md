---
id: fjsp-alternative-path-search-adaptation
type: reference
title: 可选工艺路线FJSP的可实现搜索步骤
tags: [fjsp, alternative_path, route_choice, constructive_search, coupled_local_search, exact_hybrid, cp_sat]
status: active
---

# 可选工艺路线搜索适配

本卡是编码参考，不是后端求解器。按当前Assignment的方法族选读；在incumbent的实际
数据结构上实现，不复制实例答案或改变固定IO。语义见同目录
`alternative_path_semantics_and_validation.md`。

## 共同状态与一个完整解码器

`routes[j][r]`保存原始op_id序列，`chosen[j]`选路线，`pool[j][op]`保存机器与时长。
`next_pos[j]`只表示当前路线中的位置，获取工序用`routes[j][chosen[j]][next_pos[j]]`。
换路线后从空机器时间轴重新构造完整解；不能只替换旧schedule中的该job，因为其他job的
机器占用可能需移动。保留路线、优先/分配参数、schedule与目标值的独立best快照。

若已有合法列表构造器，优先扩展其参数作为解码器；不为表示形式本身重写它。
统一入口接收路线向量与可选派工/机器参数，返回完整schedule或失败。外层搜索只消费返回值，
不在多个邻域中重复实现时序逻辑。

## 构造：机器空隙和后续工作量

路线初筛可以比较 `W(j,r)=sum(min eligible processing time)`，再考虑必经机器负荷。
W短不等于makespan好：更长路线仍可能避开瓶颈或提供不同工序顺序。
静态评分用于确定起点或枚举顺序，不能替代完整调度评价。

每步从所有job的下一道ready工序中选择。对每台eligible机器，令`t=job_ready[j]`，
按start升序遍历占用区间`[a,b)`：若`t+p<=a`则找到空隙，否则`t=max(t,b)`继续。
机器方案可比较最早完工、加工时长和后续拥堵；选定后把区间有序插入时间轴。
跨ready工序的优先规则可互补使用：最早完工、剩余路线工作量较大优先、剩余工序数较大优先。
剩余工作量仅沿当前所选路线累计，不能把未选工序算进去。

先以现有规则生成完整合法best，再从以下候选中按预算探索，不要求同时实现全部：

- 现有路线向量加不同ready优先规则；
- 每次只换一个job路线，完整构造后比较；
- 在较好路线向量附近随机切换少量job，并扰动派工优先参数。

以路线向量+优先参数或实际机器顺序去重。每次完整构造后更新best，估计单次耗时再决定
后续入口数。两个候选只是激活证据，不能成为质量搜索的终止理由；直到deadline、
声明的工作上限或有限候选穷尽才停止。记录完整候选数、不同路线配置数和停止原因。

## 局部：单job路线移动先闭环

复用完整解码器的最小可工作循环：

```text
best = current = 已验证的现有解
while 尚有搜索预算:
    changed = false
    for j in 当前瓶颈相关或轮换的job顺序:
        for r in routes[j], r != current.chosen[j]:
            trial = current的独立路线/优先参数副本
            trial.chosen[j] = r
            decoded = 完整重解码(trial)
            若完整合法且makespan严格改善: current = decoded; 更新best; changed = true
            接受后后续移动从新的current出发
    若本阶段包含顺序/机器移动: 在同一解码入口上评估一个有界交换、重插或换机批次
    若无改进: 停止穷尽的邻域，或对current独立扰动重启；best不变
return best
```

仅降低静态路线工作量不算接受。只修改chosen、不重算schedule会产生陈旧覆盖；
在循环中原地改current再局部撤销容易污染机器状态，优先从小参数副本完整重建。
已有优先向量解码时，交换job/工序优先级本身就是顺序决策，不必新增不兼容的DAG表示。
若改用显式机器序列，换路线时删除旧路线独有工序、插入新增工序并拒绝前驱/机器弧成环。

## 精确：共享工序变量、条件路线前驱

适用于当前单一替代路线契约，不包括SDST、组批或日历扩展。
保留合法incumbent作为回退和可选hint；新增模型函数必须从CLI实际调用。
OR-Tools只使用运行环境已提供的依赖和获准capability probe。

可按下列关系映射到当前代码，变量名可不同：

```text
对每个job j:
    route[j,r] = BoolVar；AddExactlyOne(所有route[j,r])
对原始pool中的每道工序o:
    active[o] = BoolVar
    Add(active[o] == sum(route[j,r] for r中含o))
    start[o], end[o] = IntVar(0,H)
    对每个eligible机器m:
        use[o,m] = BoolVar
        interval = new_optional_interval_var(start[o], p[o,m], end[o], use[o,m], name)
        保存interval到该机器列表
    Add(sum(use[o,m]) == active[o])
对每条路线r:
    对相邻工序a,b: Add(start[b] >= end[a]).OnlyEnforceIf(route[j,r])
    Add(Cmax >= end[r最后一道工序]).OnlyEnforceIf(route[j,r])
对每台机器: AddNoOverlap(其IntervalVar列表)
Minimize(Cmax)；在剩余deadline内Solve
```

每job恰选一条路线，因此工序presence等于包含它的route变量之和；不要把共享工序
复制成多个同时必须执行的活动。创建全部工序变量后再加前驱，重排路线可能前向引用。
H采用安全串行上界或已验证incumbent的makespan；最长单个job时长不能作为全局上界。

提取时为每job找选中的r，只遍历该路线工序，再按use找机器，用`solver.Value`读取时间。
只在FEASIBLE/OPTIMAL状态提取；UNKNOWN、模型/接口异常都返回原best并报告实际状态。
`cp_sat_called`在真正调用Solve前设置，建模异常不能记为已求解。interval/one-hot/条件前驱
在实际创建时计数，不为诊断额外扫描不兼容的protobuf接口。

先闭合完整模型、提取、回退的一次真实调用；若模型规模/实测耗时要求局部修复，可固定部分
路线或时序并减少释放job。不在首次实现中同时搭建完整CP、LNS、Tabu和新的构造器。

## 集成顺序

一句可检验规则假设→小补丁保存→CLI接线→获准compile/smoke→读取输出检查真实计数。
短smoke的几秒是开发检查预算；正式搜索消费传入CLI预算，不能把smoke限额写死到solve。
若检查次数已用完，报告已知结果交给Core，不另造命令、重复检查或只写遥测代替实现。
