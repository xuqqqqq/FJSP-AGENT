# 标准 FJSP AWLS/HGTSA 方法包

## 目的

该方法包是一个与具体实例无关的实现参考，用于根据当前激活的需求与 IO 契约生成标准
FJSP 求解器。它提供的是方法知识，不是预先求好的排程，也不是后端编排代码。

## 适用性与检索阶段

该方法包属于第二阶段实现候选，而不是第一阶段默认方案。只有在 Main Agent 已判断当前
标准 FJSP 方向主要由 assignment/sequence 耦合改进、关键路径局部搜索、禁忌搜索或
自适应多样化主导时，它才应进入可见范围。

该方法包是建议性的方法知识，不是规定性的实现方案。当实例、当前 incumbent 与预算支
持一条连贯的 AWLS/HGTSA 路线时，Main Agent 可以选择整个包；也可以只取其中个别资
产，作为更小规模或混合方向的参考。建议分阶段推进，并不意味着禁止直接选择完整方法。

当合法 incumbent 或完整 baseline 状态能够表示为 assignment 加显式 machine
sequences，且可用预算足以支持重复的 decode-and-evaluate move 时，应优先考虑本包。
不要仅因为它是知识库里最详细的资产就选择它。

当当前任务只是构造一个最小合法 baseline、所选方向是 CP-SAT/精确模型，或当前激活的
变种已经使标准 decoder 与 move 语义失效时，不应优先选择本包。

Coding Agent 应基于本包进行推理，并针对当前 solver CLI、状态表示、实例特征和输出
schema 独立地适配、组合、简化或重写其中思路。参考来源只是可选学习材料，不是必须照
抄的答案。如果 Agent 宣称采用了完整方法包，那么最终行为必须保留其连贯、可执行的搜
索语义；这种保真要求并不等于复制源码。它也不得复制基准特定的分数、排程、机器顺序或
目标值。

## 必需结构

1. 从当前输入中解析每个作业、每道工序、每台候选机器及其加工时长。
2. 构建一个合法的构造状态，包含工序 assignment 与显式 machine sequences。
3. 使用作业 precedence 与机器 precedence 传播来 decode 状态。部分状态或死锁状态都
   属于 infeasible，不能替换 incumbent。
4. 从解码后的 schedule 中提取精确关键路径与紧致关键机器块。
5. 生成有界的同机关键块 move，以及异机重分配/插入 move。
6. 对逆向 move 使用 tabu 属性，对全局最优启用 aspiration，并维护相互独立的当前状态
   与全局最优状态。
7. 只有在 move 被接受后，才更新工序权重或等价搜索压力；搜索停滞时要保留显式多样化
   机制。
8. 每个候选都必须先 decode 并验证，再比较目标值。
9. 超时或 decode 失败时，必须保留当前最好的完整合法 schedule。

## 资产

- `reference_solver.py`：完整 Python 方法参考。生成的 solver 可以学习、适配、组合、
  简化或替换其中结构和函数，但仍必须遵守当前激活的 IO 与 evaluator 契约。既不要求，
  也不建议直接移植源码。
- `behavior_contract.md`：用于区分“真实方法实现”和“只有函数名或注释”的行为检查。
- `standard_fjsp_algorithm_semantic_review_contract.md`：独立 reviewer 在 promotion
  阶段使用的方法语义契约。

## 适配边界

参考实现可能会为其历史 CLI 使用仓库内的 parser/solution helper。独立的
agent-generated solver 必须用当前 solver contract 允许的代码替换这些 import。算法
结构属于本方法包；parser/evaluator 的权威来源仍然是当前激活的 IO 文档与 Core
evaluator。
