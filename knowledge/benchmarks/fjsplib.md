# FJSPLib 与标准基准家族

## 来源

- 基准页面：[FJSPLib](https://scheduleopt.github.io/benchmarks/fjsplib)
- 实例仓库：[SchedulingLab/fjsp-instances](https://github.com/SchedulingLab/fjsp-instances)
- 通用基准综述：[Job Shop Scheduling Benchmark: Environments and Instances](https://arxiv.org/pdf/2308.12794)

## 相关要点

FJSPLib 汇集了常见的 flexible job-shop 基准家族及其 best-known solutions。其中列出的家族包括 Brandimarte、Hurink、Dauzere、Barnes、Kacem、Fattahi 和 Behnke。

## 对 FJSP Harness Agent 的影响

在进入工业变体之前，标准 FJSP 测试应作为第一层公开验证。它提供：

- 清晰的 makespan 目标；
- 公开的实例家族；
- 已知的 upper/lower bound 参考；
- 与常见 baseline 进行比较的方式。

## 模块映射

- `examples/standard_fjsp_evaluator.py`：验证 makespan schedule。
- `Task Contract`：在可用时可以引用 best-known solution CSV。
- `Benchmark Runner`：应支持按 family 批量运行并输出 gap 报告。
- `Report`：应把标准 FJSP 的 gap 与工业自定义指标分开报告。
