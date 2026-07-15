"""可选代码槽插件的外置资料加载；默认闭环不调用。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness_agent.domains.pack import get_domain_pack


def load_edit_strategy_json_asset(
    *,
    problem_family: str,
    strategy_name: str,
    asset_key: str,
) -> dict[str, Any]:
    path = edit_strategy_asset_path(
        problem_family=problem_family,
        strategy_name=strategy_name,
        asset_key=asset_key,
    )
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def edit_strategy_asset_path(
    *,
    problem_family: str,
    strategy_name: str,
    asset_key: str,
) -> Path | None:
    pack = get_domain_pack(problem_family, fallback_to_standard=False)
    if pack is None:
        return None
    strategy = pack.edit_strategy(strategy_name)
    if strategy is None:
        return None
    return strategy.asset_path(asset_key)
