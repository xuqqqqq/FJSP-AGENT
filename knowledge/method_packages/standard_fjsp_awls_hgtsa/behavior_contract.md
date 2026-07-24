# AWLS/HGTSA 行为契约

Coding Agent 与 semantic reviewer 应验证行为是否成立，而不是只看名字。

## Decoder

- 若 machine sequence 中某道工序的 job 前驱尚未完成，则该工序必须等待；按机器主序
  直接回放是无效的。
- 如果完整扫描一轮后仍有未调度工序，且本轮没有任何工序被成功调度，则 decode 必须失
  败，且不得替换 incumbent。
- 每个被接受的 decoded state，都必须在合法机器上以选定加工时长恰好包含每一道必需工
  序一次。

## 关键图与关键块

- 关键性必须从当前 decoded disjunctive graph 上的前向与后向时序传播推导出来。
- 关键机器块必须是由紧致 machine arc 连接的连续工序序列。仅仅挑选最忙机器上的工序，
  不能算关键块邻域。

## 邻域

- 同机 move 必须修改显式 machine order，并在比较前完成 decode。
- 异机 move 必须先把工序从旧 sequence 移除，再插入合法目标 sequence，然后对完整状
  态进行 decode。
- K-insertion 必须评估超过相邻交换的结构，并且只保留完整合法的候选。
- move 应用必须具备事务性。每个候选都应在 clone 或 snapshot 上执行，重建 links/times，
  并且仅在 decode 成功后提交。若出现异常、cycle、部分 decode 或 timeout，`current`
  与 `best` 都必须保持不变；先修改 `current` 再在出错后捕获异常是不合格的。
- 任何沿机器前驱/后继 link 的遍历，都必须使用 visited set 或 operation-count 上界，
  以防受损候选导致无界路径或无界 tabu key。

## 禁忌与特赦

- 接受 move 后存入的 tabu 属性，必须描述能把状态恢复到前一状态的逆 move。
- 对换机重分配而言，逆向属性使用旧机器与旧插入上下文，而不是新机器 sequence。
- tabu candidate 只有通过针对全局最优目标的显式 aspiration 规则，才可以被接受。
- 当前状态与全局最优状态必须是不同对象，或不可变 snapshot。

## 自适应搜索与多样化

- 权重或 `zi` 的更新必须能够实际影响可达 move 的打分或选择。
- 搜索停滞时必须触发有界多样化机制，例如加权扰动、重启或受控随机化。
- 多样化必须保留全局最优，并保持 seed 可复现性。

## 运行时

- 生成的 CLI 必须接受 `--time-limit-sec`；Core 传入的数值会略小于其进程超时，以便为
  序列化与进程退出预留空间。
- solver 必须在构造、候选生成、搜索、验证、序列化以及所有 portfolio lane 上共用同一
  个 deadline。
- deadline 检查必须出现在嵌套的 operation/machine/insertion-position 循环内部，而不
  只是放在 restart 或外层 tabu iteration 之间。单次 neighborhood scan 必须可中断。
- 候选物化必须使用显式 shortlist/window/cap 做边界控制。不要在检查 deadline 之前先
  构建无界的 all-pairs move 列表。
- Worker 侧验证仅限编译，以及一次固定 seed、最长 3 秒的短 smoke。不要重试失败的
  worker smoke，也不要用临时内联搜索循环替代它。多 seed 与正式 benchmark 运行属于
  Core 的职责。
