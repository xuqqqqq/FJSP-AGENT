from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .workers.deepseek_worker import render_strategy_markdown


def build_strategy_candidates(
    *,
    profile_path: Path,
    output_dir: Path,
    max_candidates: int,
    source: str,
) -> list[dict[str, str]]:
    """Create deterministic strategy-profile variants for one evolution round.

    DeepSeek or the template generator emits a profile containing several
    strategies.  Evaluating only the merged profile hides which idea helped.
    This helper creates candidate profiles that the harness can evaluate
    independently while preserving the original profile as candidate 0.
    """

    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    strategies = [item for item in profile.get("strategies", []) if isinstance(item, dict)]
    local_search_profiles = [
        item for item in profile.get("local_search_profiles", []) if isinstance(item, dict)
    ]
    candidate_dir = output_dir / "strategy_candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[dict[str, str]] = []

    def add_candidate(candidate_id: str, candidate_profile: dict[str, Any]) -> None:
        if len(candidates) >= max(1, max_candidates):
            return
        safe_id = safe_name(candidate_id)
        path = candidate_dir / f"{safe_id}.json"
        strategy_path = candidate_dir / f"{safe_id}.md"
        path.write_text(json.dumps(candidate_profile, ensure_ascii=False, indent=2), encoding="utf-8")
        strategy_path.write_text(render_strategy_markdown(candidate_profile, source=f"{source}:{safe_id}"), encoding="utf-8")
        candidates.append(
            {
                "candidate_id": safe_id,
                "profile_path": str(path),
                "strategy_path": str(strategy_path),
            }
        )

    add_candidate(
        "candidate_00_all",
        {
            "rationale": profile.get("rationale", ""),
            "strategies": strategies,
            "local_search_profiles": local_search_profiles,
        },
    )

    for index, strategy in enumerate(strategies):
        add_candidate(
            f"candidate_{index + 1:02d}_{strategy.get('name', 'single')}",
            {
                "rationale": f"Single-strategy ablation from {source}: {strategy.get('name', index)}",
                "strategies": [strategy],
                "local_search_profiles": local_search_profiles,
            },
        )

    if strategies:
        add_candidate(
            "candidate_chain_bias",
            {
                "rationale": "Mutated candidate that emphasizes long remaining chains and early finish.",
                "strategies": [mutate_weights(strategy, {"remaining_work": 1.25, "remaining_ops": 1.2, "early_finish": 1.1}) for strategy in strategies],
                "local_search_profiles": local_search_profiles,
            },
        )
        add_candidate(
            "candidate_machine_bias",
            {
                "rationale": "Mutated candidate that emphasizes machine readiness, load, and flexibility.",
                "strategies": [mutate_weights(strategy, {"machine_load": 1.3, "machine_ready": 1.2, "flexibility": 1.15}) for strategy in strategies],
                "local_search_profiles": local_search_profiles,
            },
        )

    return candidates


def mutate_weights(strategy: dict[str, Any], factors: dict[str, float]) -> dict[str, Any]:
    mutated = dict(strategy)
    weights = dict(mutated.get("weights", {}))
    for name, factor in factors.items():
        if name in weights:
            weights[name] = max(-12.0, min(12.0, float(weights[name]) * factor))
        else:
            weights[name] = min(12.0, 1.5 * factor)
    mutated["name"] = f"{strategy.get('name', 'strategy')}_mut"
    mutated["weights"] = weights
    return mutated


def safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe[:80] or "candidate"
