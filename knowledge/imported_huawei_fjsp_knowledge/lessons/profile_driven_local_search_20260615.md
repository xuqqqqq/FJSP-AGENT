# Profile-driven 局部搜索配置实验记录（2026-06-15）

## 实验目的

验证标准 FJSP agent 是否可以不只演化派工权重，还能让策略 profile 携带局部搜索规则与预算配置，并由固定 evaluator 自动选择更优组合。

## 机制变更

1. DeepSeek/template 生成的 `strategy_profile.json` 现在可以包含 `local_search_profiles`。
2. 每个 `local_search_profile` 显式描述：
   - `neighborhood_profile`：`random`、`critical-block`、`combined`、`hgtsa-lite`、`hybrid`。
   - `portfolio_size`、`restarts`、`iterations`、`neighbor_limit`、`time_limit_sec`。
3. `run-standard-agent` 在未显式传入 `--local-search-run-profiles` 时，会按候选 profile 内的 `local_search_profiles` 展开 contract。
4. 如果命令行提供 `--local-search-run-profiles`，则命令行配置优先，用于固定口径消融实验。

## 验证命令

```powershell
python -m harness_agent.cli run-standard-agent `
  --profile-mode template `
  --solver local-search `
  --doc README.md `
  --instance-dir "C:\Users\ASUS\Downloads\FJSP-Instance-main\FJSP-Instance-main\instance" `
  --pattern "fjsp.barnes.mt10*.txt" `
  --best-known-csv "F:\huawei_fjsp_llm\huawei_fjsp_llm\outputs\standard_fjsp_barnes_smoke\Best.csv" `
  --output-dir outputs\verify_profile_driven_local_search_barnes3 `
  --max-rounds 1 `
  --seeds 0 `
  --portfolio-size 64 `
  --strategy-candidates 1 `
  --max-workers 3 `
  --max-instances 3 `
  --timeout-seconds 90
```

## 结果

| 候选 | 局部搜索配置 | 平均 gap | 可行率 |
|---|---|---:|---:|
| `candidate_00_all__ls_template_combined_balanced_0` | `combined`，portfolio 192，2 restarts，100 iterations，220 neighbors，4 秒 | 8.349% | 1.000 |
| `candidate_00_all__ls_template_hybrid_probe_0` | `hybrid`，portfolio 256，3 restarts，160 iterations，300 neighbors，6 秒 | 7.608% | 1.000 |

3 个 Barnes `mt10*` 实例全部合法。Evaluator 选择 `template_hybrid_probe_0` 作为本轮最佳候选。

## 经验

1. 将局部搜索规则和预算暴露给 profile 后，agent 的可演化对象从“派工权重”扩展到“派工权重 + 邻域规则 + 搜索预算”。
2. `hybrid` 在本小样上优于 `combined`，说明 HGTSA 风格候选即使整体尚未稳定，也可以作为 evaluator-gated probe 在部分实例上贡献收益。
3. 该结果仍是小样验证，不能代表全 Barnes/DP 结论；后续需要在更多实例、多 seed、统一时间预算下复验。
4. 下一步应让 DeepSeek 根据上一轮候选表自动增减 `local_search_profiles`，例如保留有效的 `hybrid`，降低无效的 `hgtsa-lite` 纯 profile 权重。

## 后续补充：多初解局部搜索（2026-06-15）

新增 `initial_pool_size` 后，局部搜索可以从同一轮 portfolio 中选择多个不同的高质量构造式初解分别进入 tabu search，再取最优结果。该机制不读取历史解，也不使用上一轮解作为 warm start；所有初解仍由当前实例与当前策略 profile 生成。

### Barnes mt10 小样

命令输出目录：`outputs\verify_initial_pool_profile_barnes3`

| 候选 | 局部搜索配置 | 平均 gap | 可行率 |
|---|---|---:|---:|
| `template_combined_balanced_0` | `combined`，initial_pool_size 1 | 9.638% | 1.000 |
| `template_combined_elite_initials_0` | `combined`，initial_pool_size 2 | 6.592% | 1.000 |
| `template_hybrid_probe_0` | `hybrid`，initial_pool_size 2 | 8.163% | 1.000 |

Evaluator 选择 `template_combined_elite_initials_0`。相对上一版 profile-driven Barnes3 小样最佳 `7.608%`，平均 gap 进一步降到 `6.592%`。

### Brandimarte 小样

命令输出目录：`outputs\verify_initial_pool_profile_brandimarte2`

| 候选 | 局部搜索配置 | 平均 gap | 可行率 |
|---|---|---:|---:|
| `template_combined_balanced_0` | `combined`，initial_pool_size 1 | 7.019% | 1.000 |
| `template_combined_elite_initials_0` | `combined`，initial_pool_size 2 | 4.423% | 1.000 |
| `template_hybrid_probe_0` | `hybrid`，initial_pool_size 2 | 3.846% | 1.000 |

Brandimarte 小样上 evaluator 选择 `hybrid_probe`。这说明不同算例族的最优局部搜索 profile 不同，必须保留 evaluator-gated profile selection，而不是把某个 profile 写死为全局默认。
