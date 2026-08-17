---
id: fjsp-reentrant-semantics-and-expansion
type: reference
title: 可重入 FJSP 尾部语义与展开
tags: [fjsp, reentrant_route, loop_expansion, parser, evaluator]
status: active
---

# 可重入 FJSP 语义与展开

当前基准采用刻意限定范围的可重入 FJSP 契约。标准 FJSP 主体后，每个工件恰好跟随一个连续回路三元组 `(loop_start, loop_end, repeat)`。工艺路线展开为 `pre + body * repeat + post`。展开后不存在额外资源或时间约束。

展开后的每次访问都是独立工序，具有连续的 0 基 `op_id`。它继承源工序的候选机器和时长，但不同轮次可以独立选择机器。因此 evaluator 仍针对展开后的工序集合执行标准的前驱、机器候选、时长和机器容量验证。

该区别在运行中非常重要。忽略尾部可能得到一个对未展开前缀看似合法、却遗漏大量重复工作的调度。安全解析器应验证每个回路边界和重复次数，消费全部尾部词元，在构造任何搜索状态前完成展开，并按展开后的身份检查输出覆盖。

给定的需求/IO 文档和 `reentrant_fjsp_manifest.json` 是该编码的权威来源。更广泛的可重入文献可能涉及任意循环路线、多重回路、批处理、返工概率、释放控制或并行工作中心；不得在此推断这些语义。
