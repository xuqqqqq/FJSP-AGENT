# FJSP 自演进知识库 Demo

## 1. 目标

本目录是第三阶段“FJSP 垂直场景算法自演进框架”的本地知识库 demo。它不是论文全文仓库，而是一个可被 LLM/Agent 检索和复用的算法素材库。

核心用途：

1. 给自演进 agent 提供 FJSP 经典方法、邻域算子、代码库和数据集口径。
2. 避免 LLM 只凭常识生成弱启发式规则。
3. 将每轮实验失败和有效经验沉淀为可复用知识。
4. 支持后续将候选策略生成器从“纯参数搜索”升级为“检索增强的规则/算子组合”。

## 2. 目录结构

```text
knowledge/
  README.md
  schema.md
  papers/             # 论文和方法卡片
  codebases/          # 开源代码库卡片
  operators/          # 可复用算法算子卡片
  datasets/           # 数据集、best-known 和评估口径
  lessons/            # 本项目实验经验和失败模式
  todo_downloads.md   # 需要人工补充下载的论文/代码
```

## 3. 使用方式

关键词检索：

```powershell
python scripts\search_fjsp_knowledge.py --query "critical path tabu search Barnes gap"
```

限制搜索目录：

```powershell
python scripts\search_fjsp_knowledge.py --query "reinforcement learning graph neural network" --section papers
```

输出更多结果：

```powershell
python scripts\search_fjsp_knowledge.py --query "setup batch constraint repair" --top-k 10
```

## 4. 当前 demo 内容

第一版知识库包含以下类型卡片：

1. 标准 FJSP 数据集与 best-known 对照。
2. CP-SAT 建模基线。
3. GA + Tabu Search 混合方法。
4. 全局-局部邻域搜索 + Tabu 方法。
5. DRL/GNN 类方法。
6. 关键路径、机器序列插入、禁忌搜索、portfolio 策略选择等算子。
7. 本项目 Barnes smoke test 的经验沉淀。

## 5. 与 Skill 的关系

知识库负责存放内容，Skill 负责规定流程。推荐后续新增一个 `fjsp-strategy-evolve` skill，使 agent 每轮按如下流程运行：

```text
读取问题特性 -> 检索知识库 -> 选择算法片段 -> 生成候选策略 -> 运行校验器 -> 诊断失败 -> 更新 lessons
```

本 demo 暂时只建立知识库和检索脚本，不创建新的 Codex skill。

