---
id: codebase-doagnn
type: codebase
title: thxiwilldoit/DOAGNN
tags: [fjsp, reinforcement-learning, graph-neural-network, pytorch]
source: https://github.com/thxiwilldoit/DOAGNN
status: seed
---

## 仓库定位

DOAGNN 是 WWW 2025 FJSP 强化学习论文的开源代码入口。它适合作为“由策略网络直接选择动作”的参考实现，而不是短期内替代现有启发式求解器。

## 可复用模块

1. 图状态表示。
2. 训练数据组织。
3. 策略网络结构。
4. 强化学习训练/评估流程。

## 输入输出差异

需要检查其数据格式是否兼容 FJSP-Instance。如果不兼容，需要实现标准格式转换器。

## 接入难度

较高。需要 PyTorch 训练环境、足够训练实例和较长实验周期。

## 后续动作

1. 下载代码并记录依赖。
2. 先只阅读环境和数据格式，不急于训练。
3. 判断是否能将我们的动作合法性过滤器作为动作掩码接入。

