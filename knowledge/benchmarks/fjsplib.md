# FJSPLib and Standard Benchmark Families

## Source

- Benchmark page: [FJSPLib](https://scheduleopt.github.io/benchmarks/fjsplib)
- Instance repository: [SchedulingLab/fjsp-instances](https://github.com/SchedulingLab/fjsp-instances)
- General benchmark survey: [Job Shop Scheduling Benchmark: Environments and Instances](https://arxiv.org/pdf/2308.12794)

## Relevant Idea

FJSPLib collects common flexible job-shop benchmark families and best-known
solutions.  Families listed there include Brandimarte, Hurink, Dauzere, Barnes,
Kacem, Fattahi, and Behnke.

## Impact on FJSP Harness Agent

Standard FJSP tests should be the first public validation layer before industrial
variants.  They provide:

- a clean makespan objective;
- public instance families;
- known upper/lower bound references;
- a way to compare against common baselines.

## Module Mapping

- `examples/standard_fjsp_evaluator.py`: validates makespan schedules.
- `Task Contract`: can reference best-known solution CSV when available.
- `Benchmark Runner`: should support family-level batch runs and gap reports.
- `Report`: should separate standard FJSP gap from industrial custom metrics.

