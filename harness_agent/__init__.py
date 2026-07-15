"""FJSP 及其变种问题的算法生成与演进平台。

生产代码按职责分层：`context` 组织任务事实，`agents` 负责规划与审查，
`workers` 适配 Coding Agent，`orchestration` 管理闭环，`core` 固定判卷，
`domains` 提供问题族解析能力，`web`/`cli` 只是两种入口。
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
