"""将领域适配器输出转换为 Context Packet 可消费的能力描述。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from harness_agent.domains.standard_fjsp import StandardFjspContextProvider
from harness_agent.core.models import TaskContract


class DomainContextProvider(Protocol):
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


@dataclass(frozen=True)
class GenericContextProvider:
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


_PROVIDERS: dict[str, DomainContextProvider] = {
    "standard_fjsp": StandardFjspContextProvider(),
    "fjsp": StandardFjspContextProvider(),
}
_GENERIC_PROVIDER = GenericContextProvider()


def get_domain_context_provider(problem_family: str) -> DomainContextProvider:
    normalized = str(problem_family or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _PROVIDERS.get(normalized, _GENERIC_PROVIDER)


def register_domain_context_provider(problem_family: str, provider: DomainContextProvider) -> None:
    normalized = str(problem_family or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        raise ValueError("problem_family is required")
    _PROVIDERS[normalized] = provider
