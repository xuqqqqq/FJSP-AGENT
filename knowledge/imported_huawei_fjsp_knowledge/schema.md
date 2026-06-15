# 知识卡片 Schema

每张知识卡片采用 Markdown 正文 + YAML 风格头部字段。字段不要求严格 YAML 解析，但要方便关键词检索和后续脚本抽取。

## 通用字段

```text
---
id: 唯一编号
type: paper | codebase | operator | dataset | lesson
title: 标题
tags: [fjsp, tabu-search, critical-path]
source: URL 或本地路径
status: seed | verified | needs_fulltext | deprecated
---
```

字段说明：

| 字段 | 含义 |
|---|---|
| `id` | 稳定引用 ID，供实验日志记录 |
| `type` | 卡片类型 |
| `title` | 人可读标题 |
| `tags` | 检索关键词 |
| `source` | 来源链接、本地路径或实验输出 |
| `status` | 当前可信状态 |

## 论文卡片建议结构

```text
## 方法摘要
## 适用问题
## 核心算法片段
## 可迁移到本项目的点
## 风险与限制
## 后续动作
```

## 代码库卡片建议结构

```text
## 仓库定位
## 可复用模块
## 输入输出差异
## 接入难度
## 许可证/依赖风险
## 后续动作
```

## 算子卡片建议结构

```text
## 作用
## 输入
## 输出
## 约束安全性
## 伪代码
## 适用阶段
## 失败模式
```

## 经验卡片建议结构

```text
## 实验背景
## 现象
## 判断
## 对下一轮自演进的约束
```

