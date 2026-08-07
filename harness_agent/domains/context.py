"""将领域适配器输出转换为 Context Packet 可消费的能力描述。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from harness_agent.domains.standard_fjsp import (
    DistributedFjspContextProvider,
    PriorityFjspContextProvider,
    StandardFjspContextProvider,
)
from harness_agent.core.models import TaskContract


class DomainContextProvider(Protocol):
    """问题族到 Context Packet 的适配接口。

    上游 Harness 只关心“实例长什么样、当前激活了哪些特征”，不关心底层是
    FJSP、SDST 还是别的族，因此通过这个协议收敛领域差异。
    """

    def inspect_instances(self, contract: TaskContract, *, project_root: Path | None) -> dict[str, Any]:
        ...

    def active_features(
        self,
        *,
        contract: TaskContract,
        instance_diagnostics: dict[str, Any],
        contract_review_evidence: dict[str, Any],
    ) -> list[str]:
        ...

    def solution_contract(self) -> dict[str, Any]:
        """Return the immutable solver output contract for this problem family."""
        ...


@dataclass(frozen=True)
class GenericContextProvider:
    """默认兜底适配器。

    当某个问题族没有专门 provider 时，系统仍能生成 Context Packet，只是不会
    给出实例特征和变体判定，worker 需更多依赖确认后的契约与 evaluator。
    """

    def inspect_instances(self, contract: TaskContract, *, project_root: Path | None) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "summary": {"instance_count": len(contract.instances), "profiled_count": 0},
            "direction_hints": [
                "No domain context provider is registered; rely on the confirmed IO and evaluator contract."
            ],
            "instances": [],
        }

    def active_features(
        self,
        *,
        contract: TaskContract,
        instance_diagnostics: dict[str, Any],
        contract_review_evidence: dict[str, Any],
    ) -> list[str]:
        return []

    def solution_contract(self) -> dict[str, Any]:
        return {}


_PROVIDERS: dict[str, DomainContextProvider] = {
    "standard_fjsp": StandardFjspContextProvider(),
    "fjsp": StandardFjspContextProvider(),
    "fjsp_sdst": StandardFjspContextProvider(),
    "sdst": StandardFjspContextProvider(),
    "fjsp_machine_availability": StandardFjspContextProvider(),
    "fjsp_nfa": StandardFjspContextProvider(),
    "nfa_fjsp": StandardFjspContextProvider(),
    "machine_availability_fjsp": StandardFjspContextProvider(),
    "fjsp_distributed_transfer": DistributedFjspContextProvider(),
    "distributed_fjsp": DistributedFjspContextProvider(),
    "dfjspt": DistributedFjspContextProvider(),
    "distributed_fjsp_with_transfers": DistributedFjspContextProvider(),
    "fjsp_job_priority": PriorityFjspContextProvider(),
    "fjsp_priority": PriorityFjspContextProvider(),
    "priority_fjsp": PriorityFjspContextProvider(),
    "fjspjp": PriorityFjspContextProvider(),
    "job_priority_fjsp": PriorityFjspContextProvider(),
}
_GENERIC_PROVIDER = GenericContextProvider()


def get_domain_context_provider(problem_family: str) -> DomainContextProvider:
    """按问题族解析上下文 provider。

    这里做的只是名称规范化和注册表查找，不在此处引入任何算法分派逻辑。
    """

    normalized = str(problem_family or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _PROVIDERS.get(normalized, _GENERIC_PROVIDER)


def register_domain_context_provider(problem_family: str, provider: DomainContextProvider) -> None:
    """注册新的问题族上下文适配器。"""

    normalized = str(problem_family or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        raise ValueError("problem_family is required")
    _PROVIDERS[normalized] = provider
