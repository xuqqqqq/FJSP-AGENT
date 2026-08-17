# FJSPLib 与标准基准家族

## 来源

- 基准页面：[FJSPLib](https://scheduleopt.github.io/benchmarks/fjsplib)
- 实例仓库：[SchedulingLab/fjsp-instances](https://github.com/SchedulingLab/fjsp-instances)
- 通用基准综述：[Job Shop Scheduling Benchmark: Environments and Instances](https://arxiv.org/pdf/2308.12794)

## 相关要点

FJSPLib 汇集了常见的柔性作业车间基准家族及其当前最佳已知解。其中列出的家族包括 Brandimarte、Hurink、Dauzere、Barnes、Kacem、Fattahi 和 Behnke。

## 对 FJSP Harness Agent 的影响

在进入工业变体之前，标准 FJSP 测试应作为第一层公开验证。它提供：

- 清晰的 makespan 目标；
- 公开的实例家族；
- 已知的上下界参考；
- 与常见基线方法进行比较的方式。

## 模块映射

- `examples/standard_fjsp_evaluator.py`：验证 makespan 调度。
- `Task Contract`：在可用时可以引用最佳已知解 CSV。
- `Benchmark Runner`：应支持按家族批量运行并输出 gap 报告。
- `Report`：应把标准 FJSP 的 gap 与工业自定义指标分开报告。
