---
id: standard-fjsp-algorithm-semantic-review-contract
type: operator-contract
title: 标准 FJSP 算法语义审查合同
tags: [fjsp, semantic-review, tabu-search, critical-path, critical-block, n8, k-insertion]
status: curated
---

# 标准 FJSP 算法语义审查契约

本契约用于审查生成代码是否真正实现了其宣称的搜索方法，而不只是存在对应的函数名。它
与具体实例无关，且不得包含基准特定的目标值或排程。

## 当前状态与全局最优

凡是允许接受非改善移动的元启发式，都必须维护两个不同状态：

```python
current_assignment, current_sequences, current_schedule = initial_state
best_assignment, best_sequences, best_schedule = clone_state(initial_state)
best_value = makespan(best_schedule)

current_assignment, current_sequences, current_schedule = accepted_trial
if trial_value < best_value:
    best_assignment, best_sequences = clone_state(current_assignment, current_sequences)
    best_schedule = list(current_schedule)
    best_value = trial_value

return best_assignment, best_sequences, best_schedule
```

搜索必须返回全局最优状态，而不是最后访问到的当前状态。即使外层 evaluator 最终会回
滚整个 solver candidate，用相同或更差的候选覆盖名为 `best_*` 的变量，依然属于违反
incumbent 保留原则。

## 逆向 Move 的禁忌属性

接受一个移动后，tabu memory 必须记录能够撤销该移动的逆向属性。只记录正向目的地，
通常无法阻止立刻回退。

示例：

```text
change machine A -> B: store reverse attribute (operation, A)
swap ... a,b ... -> ... b,a ...: store reverse attribute (machine, b, a)
insert old_pos -> new_pos: store the position or local arc attribute that restores old_pos
```

下一次迭代必须使用与存储时完全相同的属性表示来判断候选移动是否 tabu。应增加一条
行为测试：先接受一个移动，再生成它的逆移动，并证明在 tenure 到期前该逆移动一直
处于 tabu 状态。

## 特赦准则

tabu candidate 只有在完整 decode 后严格优于全局最优目标时，才可以通过特赦被接受。特
赦比较的是 `best_value`，而不只是当前状态。若一个号称 tabu loop 的实现只接受严格改
善移动，那它仍然只是爬山搜索，无法依靠 tabu memory 跨越局部最优。

## 精确关键路径

应基于 job-precedence 弧和相邻 machine-sequence 弧构建当前有效的析取 DAG。最早开始时
间要按拓扑序计算，tail 长度或 latest start 要按完整的逆拓扑序计算。对任意工序数量而
言，固定少量轮 relaxation 并不能构成精确关键路径算法。

只有总 slack 为零的工序才是关键工序。机器关键块必须是由紧致 machine arc 连接的关键
工序极大连续序列；中间存在 idle time 的相邻工序，不得被合并为同一个关键块。

必需测试：

1. 一个合成 DAG，其中关键链长度超过两条交替的 job arc 与 machine arc。
2. 一个 schedule，其中非关键工序与关键工序相邻出现。
3. 一条 machine sequence，其中两个 zero-slack 工序之间存在 idle time；它们不得被判
   为同一个紧致关键块。

## N7、N8 与 K-Insertion 的保真性

不要把任意交换或不受约束的同机插入，当作已实现具名 N7/N8 的证据。实现必须明确说明：
究竟由哪些关键块端点或可行性边界来定义该邻域。

对异机插入而言，在完整 decode 之前，就应根据 job 前驱/后继时序与目标机器序列结构，
推导出一个有界的可行目标区间。完整 decode 仍然是最终权威，但若某实现声称自己是结构化
邻域，实际却只在无关随机位置上采样，语义审查应将其标记出来。

## 运行时与阶段贡献

候选应用必须具备事务性。搜索过程可以先在 `current` 上计算近似移动评分，但真正应用
被选中的移动时，必须作用于 clone 或可恢复 snapshot，重建所有 links/times，并且只有
在完整 decode 成功后才提交。如果在修改了 `current` 之后又捕获 cycle/decode 异常，却没
有回滚，这属于阻断级语义错误，因为后续遍历与 tabu state 将不再描述一个合法排程。

solver 必须接收 evaluator 提供的 time limit，并使用单一绝对 deadline。deadline 检查证
据必须出现在嵌套候选循环内部，例如 operation、eligible machine、target position 等层
级，而不能只包在外层 tabu iteration 周围。机器 link 遍历必须有 visited 或 operation
count 上界，已物化的候选列表则必须有显式 shortlist/window/cap。

每个搜索阶段都应暴露足够的计数器或计时信息，以便报告：

```text
input makespan
evaluated move count
feasible move count
accepted move count
best makespan after stage
elapsed time
```

利用这些事实识别“源码里存在、运行时却没有获得预算”的死阶段。完整重解码是安全基线，
但在宣称具备可扩展性之前，大型邻域还应补充候选边界、廉价过滤或增量评价。

## 多 Seed 稳健性

单个被 promotion 的 seed 只能证明一次有 evaluator 背书的改进，不能证明方法稳定。可复
用经验应描述方法本身及其已验证不变量。对于随机搜索，只有在获得重复或多 seed 证据后，
才能把该方法从 candidate lesson 提升为 validated knowledge。

## 语义审查决策

只有当某条 finding 同时引用了具体 candidate 源码行与精确的知识契约条款时，才应使用
`repair_required`。若缺少源码证据或契约证据，应发出 warning，而不是阻断 promotion。

## 已审阅的可复用失败模式

以下源码模式都需要显式行为验证：

1. tabu loop 检查的是正向 move signature，并在接受后存储同一个 signature。这并不能
   证明逆向移动会被禁止。
2. 一个允许非改善移动的搜索只更新单一状态元组，并在返回时没有独立 clone 的全局最
   优状态元组。
3. 关键性传播使用了与图规模无关的固定少量轮 relaxation。
4. 一个 latest-finishing 或 near-makespan 窗口被称为精确关键块，却没有 zero-slack
   与 tight-arc 证据。
5. 某个移动修改了 machine sequence，捕获失败的 update/decode 后继续执行，却没有恢
   复前一状态。
6. solver 只在外层迭代之间检查时间，而单次 neighborhood 枚举就可能耗尽全部预算。

这些仅是方法级失败模式。可复用记忆应保留不变量与所需测试，绝不能保留实例分数、工序
顺序或已求解排程。
