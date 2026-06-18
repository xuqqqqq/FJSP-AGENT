# AWLS Python/C++ 效果对齐记录（2026-06-18）

## 目标

本轮目标是先判断 `examples/standard_fjsp_awls_solver.py` 的纯 Python AWLS 与 C++ GREEDY_INIT AWLS 在标准 FJSP 上的差距来源，并建立可复现的效果对齐基线。

优先验证实例为 Brandimarte `Mk10`。论文/复现目标为 C++ GREEDY_INIT AWLS 在 90 秒预算下达到 `195` 左右。

## 关键结论

1. C++ GREEDY_INIT AWLS 在 Mk10 上已复现论文级效果：20 个 seed、90 秒预算下 best=`195`、avg≈`196.85`。
2. 纯 Python AWLS 的邻域与评分主逻辑已基本对齐 C++ 主分支，但单位时间搜索深度显著不足。
3. Mk10 seed=2 下，纯 Python 600 秒只达到 `197`，而 C++ 后端 90 秒达到 `195`。
4. 因此当前差距主要来自 Python 热循环速度，而不是初始化解质量或明显邻域翻译错误。
5. 已新增 C++ 后端桥接脚本，用 Python harness 统一读取、调用、重建标准 JSON，并使用同一 Python evaluator 校验，从工程上建立效果对齐基线。

## 复现实验

### C++ 论文口径复现

可执行文件：

`F:\huawei_fjsp_llm\fjsp_harness_agent\outputs\cpp_awls_builds_20260618\AWLS_greedy_msvc.exe`

Mk10 20 seed、90 秒结果：

- best：`195`
- avg：`196.85`
- seed=2 约 `70.866s` 到达 `195`

### 纯 Python 长时验证

命令核心参数：

```powershell
python examples\standard_fjsp_awls_solver.py `
  --input "C:\Users\ASUS\Downloads\FJSP-Instance-main\FJSP-Instance-main\instance\Mk\Mk10.fjs" `
  --output outputs\python_long_depth_mk10_20260618\seed2_600s_solution.json `
  --seed 2 `
  --time-limit-sec 600 `
  --restarts 1 `
  --cycles-per-restart 1000000 `
  --iterations 10000 `
  --paper-profile
```

结果：

- makespan：`197`
- runtime：`600.003s`
- cycles_done：`22`
- moves：`214709`
- 校验：完整合法

该结果说明：纯 Python 即使给到 600 秒，也没有追到 C++ 90 秒 seed=2 的 `195`。

### Python 调用 C++ 后端验证

新增脚本：

`examples\standard_fjsp_awls_cpp_backend.py`

命令：

```powershell
python examples\standard_fjsp_awls_cpp_backend.py `
  --input "C:\Users\ASUS\Downloads\FJSP-Instance-main\FJSP-Instance-main\instance\Mk\Mk10.fjs" `
  --output outputs\python_long_depth_mk10_20260618\cpp_backend_seed2_90s_solution.json `
  --seed 2 `
  --time-limit-sec 90 `
  --best-known 195
```

结果：

- makespan：`195`
- scheduled_operations：`240`
- runtime：`84.589s`
- Python evaluator：`valid=true, error_count=0`

这说明 C++ 机器序列输出可以被 Python 可靠重建为标准 JSON 解，并通过同一验证器。

## 热点分析

Mk10 纯 Python 2000 步 cProfile 结果显示：

- `find_move`：主要耗时入口，约 11 秒级累计耗时。
- `evaluate_and_push`：候选动作评分调用次数约 145 万次。
- `change_machine_intersection`：换机候选 RK/LK/intersection 计算是主要子热点。
- `apply_move` / `update_time`：每步实际应用和拓扑重算也有明显开销。

单个 10000 步 TS cycle 对比：

- C++：约 2 秒。
- Python：约 24 到 27 秒。

因此 Python 与 C++ 的核心差距是解释器级候选枚举/评分成本，而不是“某个 seed 初始解太差”。

## 已做代码调整

纯 Python AWLS：

- 增加 `--paper-profile`，集中配置 GREEDY_INIT 论文复现口径。
- 增加 `--initial-state` 和 `cpp-exact` 选项，用于诊断 C++ 初始权重/随机数消耗差异。
- 使用 `on_machine_pos` 替代部分 `sequence.index()`，降低少量重复查找。
- `Move` 改为 slots dataclass，降低候选对象开销。
- 在 `exact_select_top_k=0` 时跳过未使用的 `ranked_moves` 记录，减少候选评分热循环中的列表写入。
- 将换机候选的 RK/LK/intersection 计算改为窗口边界计算。经 Mk10 300 步、22438 个候选交集抽检，与旧列表构造算法完全一致。
- 将 fallback/exhaustive 关键块从“机器扫描连续关键操作”改为优先使用 C++ `update_all_critical_block` 风格的“枚举所有关键路径并提取关键块”，边界上更贴近 C++ 邻域定义。
- 将 GREEDY_INIT 中候选阈值改为 `int(best_completion * ratio)` 截断，补齐 C++ `static_cast<int>` 口径。
- 将 `find_move` 中的大参数候选评估函数拆成本地 `consider_same` / `consider_change`，并把 same-machine 的 `Move` 对象构造推迟到合法性过滤之后，减少热循环对象和函数调用开销。

C++ 后端桥：

- 读取标准 FJSP 实例。
- 调用已验证 C++ GREEDY_INIT AWLS 可执行文件。
- 解析 C++ 输出的机器序列。
- 用作业前序和机器前序拓扑重建开始/结束时间。
- 输出标准 `standard_fjsp_schedule_v1` JSON。
- 调用 Python 标准验证器校验合法性。

## 跨实例 sanity check

轻量验证结果：

| 实例 | 时间 | 结果 | 合法性 |
| --- | ---: | ---: | --- |
| Mk06 | 5s | 59 | 合法 |
| Mk10 | 10s | 199 | 合法 |
| Barnes mt10c1 | 5s | 928 | 合法 |
| Mk01 | 5/10/30s 多 seed | C++ 后端异常退出 | 无机器序列输出 |

Mk01 的失败来自 C++ 可执行文件自身无输出异常退出；Python 桥接没有进入解析阶段。后续如果要求覆盖 Mk01，需要修 C++ 后端边界或回落到纯 Python 求解器。

## 后续纯 Python 加速复测

窗口化换机交集之后，Mk10 seed=2 的 2000 步 cProfile 结果：

- 原始基线：约 `13.58s`
- 跳过 `ranked_moves` 后：约 `12.48s`
- 窗口化换机交集后：约 `10.72s`

该优化没有缩小邻域，仍枚举 C++ 主分支中的同机移动和换机移动。

Mk10 5 seed、每 seed 90 秒、`--paper-profile` 串行复测：

| seed | makespan | moves | cycles |
| ---: | ---: | ---: | ---: |
| 0 | 198 | 38955 | 4 |
| 1 | 198 | 40003 | 5 |
| 2 | 197 | 39539 | 4 |
| 3 | 199 | 39316 | 4 |
| 4 | 198 | 39264 | 4 |

统计：

- best：`197`
- avg：`198.0`

结论：纯 Python 搜索深度已有实质提升，但 90 秒质量仍未达到 C++ GREEDY_INIT 的 `195` / `196.85` 水平。下一阶段若继续追纯 Python 对齐，需要进一步处理 `same_machine_evaluate_cpp_fast`、`change_machine_evaluate`、`candidate_tabu_sequence` 和 `update_time` 等热循环，或引入原生扩展/JIT 将候选评分编译化。

## 候选评估轻量化复测

进一步将 `find_move` 内候选评估轻量化后，Mk10 seed=2、3000 步短跑保持同一轨迹：

- 修改前同口径短跑：makespan=`209`，runtime≈`7.0s`
- 修改后同口径短跑：makespan=`209`，runtime≈`5.2s`

Mk10 seed=2、90 秒 `--paper-profile`：

- makespan：`197`
- moves：`49470`
- cycles_done：`5`
- 校验：`valid=true, error_count=0`

对比窗口化优化后的上一版 seed=2 90 秒 `39539` 步，本轮达到 `49470` 步，90 秒内搜索步数提升约 `25%`，但最优 makespan 暂未突破 `197`。

Mk10 20 seed、每 seed 90 秒、`--paper-profile` 并行复测：

| seed | makespan | moves |
| ---: | ---: | ---: |
| 0 | 198 | 44448 |
| 1 | 198 | 45344 |
| 2 | 197 | 49470 |
| 3 | 199 | 45148 |
| 4 | 198 | 44041 |
| 5 | 199 | 42820 |
| 6 | 199 | 46219 |
| 7 | 198 | 44167 |
| 8 | 198 | 43471 |
| 9 | 197 | 47622 |
| 10 | 197 | 43127 |
| 11 | 197 | 43973 |
| 12 | 198 | 43151 |
| 13 | 198 | 43585 |
| 14 | 198 | 42571 |
| 15 | 198 | 43573 |
| 16 | 198 | 44167 |
| 17 | 198 | 43566 |
| 18 | 197 | 44215 |
| 19 | 200 | 43873 |

统计：

- best：`197`
- avg：`198.0`
- worst：`200`

与 C++ GREEDY_INIT AWLS 20 seed、90 秒的 best=`195`、avg≈`196.85` 相比，纯 Python 当前平均仍差约 `1.15`，最优仍差 `2`。这说明候选枚举加速是必要但不充分的；剩余差距更可能来自随机轨迹、权重扰动初始状态、评分近似细节或更深层的局部搜索实现差异。

### C++ tenure 口径复核与后续采纳

C++ `TabuSearch` 中 tabu tenure 上界为：

- 若 `job_num <= 2 * machine_num`，`L_max = int(L * 1.4)`
- 否则 `L_max = int(L * 1.5)`

Mk10 上对应 `L=11, L_max=15`。Python 旧口径为 `ceil(L * 1.5)`，即 `L_max=17`。对齐 C++ tenure 后，Mk10 seed=2 的 3000 步短跑从 `209` 改善到 `207`，但 90 秒 seed=2 从 `197` 变为 `198`；进一步测试 seed 0..9 的 90 秒结果为：

| seed | makespan |
| ---: | ---: |
| 0 | 198 |
| 1 | 197 |
| 2 | 198 |
| 3 | 197 |
| 4 | 198 |
| 5 | 198 |
| 6 | 199 |
| 7 | 200 |
| 8 | 200 |
| 9 | 200 |

统计：best=`197`，avg=`198.5`。该短时结果曾差于当时默认 20 seed avg=`198.0`，说明在 RNG 与候选评分轨迹尚未完全一致前，逐项照搬 C++ 参数不一定立刻带来效果对齐。

后续将 C++ tenure 与 C++ 停止检查节奏一起复测后，Mk10 seed=2 的 300 秒结果达到 `196`，600 秒仍保持 `196`。因此当前代码已采用 C++ tenure 公式，并用 `cpp_tabu_tenure_bounds()` 单元测试覆盖两个分支。

### strict paper profile 诊断

C++ `Operation` 默认状态为 `w=INT_MAX, t=0`，且 same-machine / change-machine 候选评分会在每次候选评估中抽取 `rr`，即使当前扰动最终为 0。Python 因工程效果保留了默认 `--paper-profile`：

- `initial_state=reset`
- `zi_policy=cpp`
- `time_check_interval=1000`
- `same_machine_eval=cpp-fast`

该 profile 在 Mk10 seed=2、300 秒达到 `196`，是当前纯 Python 最好的单轨迹证据。

为区分“工程效果 profile”和“更严格 C++ 状态诊断 profile”，新增 `--strict-paper-profile`：

- 继承 `--paper-profile` 的 greedy / tenure / stop-check / cpp-fast 设置。
- 改为 `initial_state=cpp`，即初始 `w=INT_MAX, t=0`。
- 改为 `zi_policy=cpp-exact`，即候选评分即使 `w=0` 也消耗随机数。

Mk10 seed=2 复测结果：

| profile | 120s | 300s | 结论 |
| --- | ---: | ---: | --- |
| paper-profile 等价手动参数 | 198 | 196 | 当前 Python 工程效果更好 |
| strict-paper-profile 等价手动参数 | 197 | 197 | 更贴近 C++ 状态，但没有转化为更好结果 |

因此当前默认不切换到 strict；strict 主要用于继续定位 C++/Python 随机轨迹和权重扰动差异。

## 后续方向

1. 若目标是工程效果先对齐：优先把 C++ 后端作为标准 FJSP AWLS baseline backend 接入 agent/harness 的候选算法库。
2. 若目标是纯 Python 对齐：需要结构性加速，优先考虑候选动作评分的编译化、数组化或 C++/Cython/Numba 后端；仅靠微调 Python list/dict 循环很难追上。
3. 若目标是通用框架演示：保留 C++ 后端为“知识库/插件化强基线”，让自演进 Agent 在其上演化参数、初始化、重启策略、后处理，而不是重新从零生成弱局部搜索。
