# AWLS 邻域与近似评估逻辑核对记录

本文记录对参考 AWLS/HGTSA C++ 实现与当前 Python 标准 FJSP 求解器的核对结果。结论先行：当前 Python 求解器已经吸收了关键块邻域、跨机 k-insertion 和多 profile 评估思路，但并不是参考实现的严格逐行移植；它依赖精确 decode 与 evaluator 保证合法性，候选排序中的近似评估仍偏工程化、偏保守。直接把参考实现中的片段 R/Q 近似评估或序列 tabu 机械套入当前 Python 搜索循环，已在 Barnes 三个小基准上被验证为不稳定。

## 1. 参考实现中的关键机制

参考代码位置：

- `C:\Users\ASUS\Downloads\许强强-华科-计算机 (1)\许强强-华科-计算机\src\Schedule.cpp`
- `C:\Users\ASUS\Downloads\许强强-华科-计算机 (1)\许强强-华科-计算机\src\TabuSearch.cpp`
- `C:\Users\ASUS\Downloads\许强强-华科-计算机 (1)\许强强-华科-计算机\src\TabuList.h`

### 1.1 head/tail 时间信息

`Schedule::update_time()` 按析取图拓扑序计算每个工序的：

- `forward_path_length`：该工序最早开始时间，可理解为 head 或 R 值。
- `end_time`：该工序完成时间。
- `backward_path_length`：从该工序之后到完工的最长后继尾长，可理解为 tail 或 Q 值。

这组 R/Q 信息是后续近似评估的基础。它不是重新完整排程，而是在当前图结构上做一次前向/后向最长路计算。

### 1.2 邻域动作

`TabuSearch::find_move()` 主要生成两类动作：

- 同机关键块移动：围绕 critical block 生成 FRONT/BACK 移动，包括块外移动和 N7/N8 风格的块内移动。
- 跨机换路 k-insertion：对关键工序尝试候选机器，利用 RK/LK 集合确定可插入区间，再生成 CHANGE_MACHINE_FRONT / CHANGE_MACHINE_BACK 动作。

其中 RK/LK 的含义为：

- RK：目标机器上 end time 大于被移动工序 job predecessor 完成时间的工序集合。
- LK：目标机器上 tail + processing time 大于被移动工序 job successor tail 的工序集合。
- 交叠区间用于减少无效插入位置，降低邻域规模。

### 1.3 近似评估

参考实现不是对每个候选都完整 decode，而是使用局部近似：

- `same_machine_evaluate()`：只对同机移动影响到的一段操作序列重新传播 R/Q，并取 `max(R + p + Q)` 作为估计 makespan。
- `change_machine_evaluate()`：根据 RK/LK 交叠关系，用被移动工序的 job predecessor、job successor tail 和目标机器相邻工序的 tail/head 组合估算。
- `UpdateWeight_per_op()`：当搜索没有改善时，对被移动工序和关键/非关键工序更新权重和冷却时间，近似评估中会加入 `zi` 扰动项。

因此参考实现的近似评估是“局部最长路估计 + 自适应权重扰动”，不是简单机器负载或整机重扫评分。

### 1.4 tabu 对象

参考实现不是只禁一个“反向 move id”，而是把受影响的一段操作序列加入 tabu list。候选动作如果会重新形成这段序列，则被判为 tabu；若候选能打破历史最优，则走 aspiration 放行。

## 2. 当前 Python 实现的实际逻辑

当前代码位置：

- `examples/standard_fjsp_local_search_solver.py`

当前 Python 求解器的主线是：

- 构造阶段通过多派工规则 portfolio 生成初始解。
- `decode_state()` 将作业顺序弧和机器顺序弧组成析取图，用拓扑调度精确计算开始/结束时间。
- `critical_machine_blocks_all()` 和 `critical_machine_blocks()` 提取关键机器块。
- `generate_hgtsa_lite_neighbors()` 生成 N8/k-insertion 风格候选，并用 `proxy_insert_score()` 排序。
- `tabu_search()` 对候选执行完整 decode 和 evaluator 校验，只有合法候选才可能被接受。

这意味着当前 Python 版本的合法性基础强于近似评估：即使 proxy 排序不准，也不会直接输出不合法解；但 proxy 会影响哪些候选进入精确 decode，因此会影响搜索质量和稳定性。

## 3. 已确认的不等价点

### 3.1 当前 proxy 不是严格 AWLS 近似评估

当前 `proxy_insert_score()` 主要使用机器序列重扫、作业前驱完成时间、tail 和轻量负载/局部偏移惩罚来排序候选。它不是参考实现中的受影响片段 R/Q 重算，也没有引入 `zi` 自适应权重扰动。

影响判断：

- 不影响合法性，因为接受前会完整 decode 和 evaluator 校验。
- 可能影响质量，因为候选排名可能与真实 makespan 改善不一致。

### 3.2 当前 tabu 比参考实现弱

当前 `Move.tabu_key` / `reverse_tabu_key` 基于 move 类型、工序和机器生成，主要阻止直接反向动作。参考实现禁的是受影响操作序列，因此对循环的抑制更强。

影响判断：

- 当前 tabu 更宽松，可能允许局部循环。
- 但机械增强为序列 tabu 后，在当前 Python 候选生成规模和精确 decode 选择机制下不一定改善。

### 3.3 RK/LK 插入位置是工程化近似

当前 `awls_insert_positions()` 已吸收 RK/LK 思想，但为了避免过窄，还加入了 start/ready pivot 和首尾位置作为 fallback。这比参考实现更宽、更保守，不是严格的连续交叠区间版本。

影响判断：

- 覆盖面更大，有助于防止漏掉有效动作。
- 邻域更大时依赖 proxy 排序，proxy 不准会放大候选筛选误差。

### 3.4 critical block 提取方式更宽

当前 `critical_machine_blocks_all()` 按机器序列扫描连续关键工序，并检查前一关键工序 end 是否等于后一关键工序 start。参考实现会从关键起点枚举关键路径并提取路径上的同机块。

影响判断：

- 当前方式通常能捕获同机关键块，但不是严格的“所有关键路径枚举”。
- 它更像安全扩展邻域，不是证明意义上的完整 critical path block 枚举。

## 4. 本次实验证伪的移植方式

测试口径：

- 算例：Barnes 三个标准 FJSP 实例。
- seeds：3、7、11。
- 参数：`--portfolio-size 64 --restarts 2 --initial-pool-size 1 --iterations 90 --neighbor-limit 200 --time-limit-sec 3 --neighborhood-profile awls-hybrid`
- 校验：`examples/standard_fjsp_evaluator.py`
- best-known：`outputs\deepseek_three_case_10r\Best.csv`

### 4.1 基线 awls-hybrid

| 算例 | 平均 gap | 最优 gap | 最优 makespan |
| --- | ---: | ---: | ---: |
| mt10c1 | 7.5072 | 3.7716 | 963 |
| mt10cc | 6.9231 | 5.8242 | 963 |
| mt10x | 11.9826 | 6.3181 | 976 |

### 4.2 片段 R/Q 评分 + 序列 tabu

输出目录：`outputs\awls_logic_check\benchmark_seqtabu`

| 算例 | 平均 gap | 最优 gap | 最优 makespan | 结论 |
| --- | ---: | ---: | ---: | --- |
| mt10c1 | 10.1652 | 3.7716 | 963 | 变差 |
| mt10cc | 7.6190 | 5.8242 | 963 | 变差 |
| mt10x | 10.0218 | 6.3181 | 976 | seed7 改善，但整体不稳定 |

### 4.3 只使用片段 R/Q 评分

输出目录：`outputs\awls_logic_check\benchmark_scoreonly`

| 算例 | 平均 gap | 最优 gap | 最优 makespan | 结论 |
| --- | ---: | ---: | ---: | --- |
| mt10c1 | 10.5244 | 3.7716 | 963 | 变差 |
| mt10cc | 7.1429 | 5.8242 | 963 | 变差 |
| mt10x | 10.4575 | 6.3181 | 976 | 比基线均值略好，但不稳 |

### 4.4 旧 proxy + 序列 tabu

输出目录：`outputs\awls_logic_check\benchmark_seqtabu_oldscore`

| 算例 | 平均 gap | 最优 gap | 最优 makespan | 结论 |
| --- | ---: | ---: | ---: | --- |
| mt10c1 | 10.8836 | 3.7716 | 963 | 明显变差 |
| mt10cc | 6.9231 | 5.8242 | 963 | 与基线持平 |
| mt10x | 10.5301 | 6.3181 | 976 | seed7 改善，但整体不足以采纳 |

## 5. 结论

1. 当前 Python 实现的邻域生成方向基本合理，但不能称为参考 AWLS 的严格实现。
2. 当前近似评估确实偏粗，属于“候选预排序 proxy”，不是参考实现中的局部 R/Q 近似评估。
3. 直接替换为片段 R/Q 主评分会降低当前三实例平均质量，说明参考实现的近似评估依赖其图更新、move 表达、权重扰动和候选生成方式，不能孤立移植。
4. 直接加入序列 tabu 也不稳定，尤其会损伤 mt10c1；当前 Python 搜索更适合先保留弱 tabu 与精确 decode，再通过 profile 化实验逐步引入更强 tabu。
5. 本次未把变差的实验逻辑并入主线，只保留核对记录和实验结论。

## 6. 后续建议

后续如果要真正实现 AWLS 风格 profile，应按以下顺序推进：

1. 新增独立 profile，例如 `awls-rq-experimental`，不要覆盖现有 `awls-hybrid`。
2. 先实现“近似评分诊断器”：对同一批候选同时记录 proxy 分数和完整 decode makespan，计算 top-k 命中率或排序相关性，确认近似评估是否真的能筛出好候选。
3. 再引入片段 R/Q 评分，并采用加权融合而不是直接替换旧 proxy。
4. 序列 tabu 需要与 move 语义绑定：同机 FRONT/BACK、跨机 CHANGE_MACHINE_FRONT/BACK 的禁忌片段应分别定义，且需要独立调 tenure。
5. 自适应权重 `zi` 应在有足够候选诊断数据后加入，否则容易把排序扰动变成随机噪声。
6. 每个新增 profile 必须保留 evaluator 验证、Best.csv gap 统计和跨 seed 对比，只有稳定改善后才进入默认候选集。
