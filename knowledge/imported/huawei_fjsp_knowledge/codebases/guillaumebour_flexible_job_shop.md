---
id: codebase-guillaumebour-flexible-job-shop
type: codebase
title: guillaumebour/flexible-job-shop
tags: [fjsp, genetic-algorithm, python, brandimarte]
source: https://github.com/guillaumebour/flexible-job-shop
status: seed
---

## 仓库定位

该仓库是 FJSP 遗传算法 Python 实现，并在 README 中引用 Li & Gao 的混合遗传算法 + 禁忌搜索方法。适合作为编码方式、种群生成和 GA 主循环的参考。

## 可复用模块

1. Brandimarte `.fjs` 数据读取。
2. 工序/机器编码。
3. GA 种群、交叉、变异、适应度评估。

## 输入输出差异

本项目当前标准算例来自 `qimingme/FJSP-Instance` 统一格式，字段与常见 `.fjs` 格式接近但不完全等价。接入前必须写转换器或适配读取函数。

## 接入难度

中等。可先不直接复制代码，而是提取：

1. 个体表示。
2. 解码逻辑。
3. 遗传操作。
4. 参数范围。

## 风险与限制

1. 许可证和依赖需进一步确认。
2. 该仓库未必直接支持 Barnes 全系列。
3. 不能把代码作为黑箱依赖交付，需转化为本项目可控实现。

## 后续动作

1. 克隆仓库到本地 `external/` 或单独缓存目录。
2. 跑通 Mk02 示例。
3. 对比其编码和我们当前派工解码器的差异。

