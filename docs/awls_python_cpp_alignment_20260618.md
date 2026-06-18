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

## 后续方向

1. 若目标是工程效果先对齐：优先把 C++ 后端作为标准 FJSP AWLS baseline backend 接入 agent/harness 的候选算法库。
2. 若目标是纯 Python 对齐：需要结构性加速，优先考虑候选动作评分的编译化、数组化或 C++/Cython/Numba 后端；仅靠微调 Python list/dict 循环很难追上。
3. 若目标是通用框架演示：保留 C++ 后端为“知识库/插件化强基线”，让自演进 Agent 在其上演化参数、初始化、重启策略、后处理，而不是重新从零生成弱局部搜索。
