from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..deepseek_client import DeepSeekClient, DeepSeekUnavailable
from ..worker import CodingWorker, ExperimentSpec, WorkerCapabilities, WorkerResult


FEATURES = [
    "early_finish",
    "early_start",
    "short_processing",
    "long_processing",
    "min_option",
    "remaining_work",
    "remaining_after",
    "remaining_ops",
    "machine_ready",
    "job_ready",
    "machine_load",
    "flexibility",
    "machine_slack",
    "job_slack",
]


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


class DeepSeekWorker(CodingWorker):
    def __init__(self, model: str = "deepseek-v4-pro") -> None:
        self.model = model
        self.available = bool(os.environ.get("DEEPSEEK_API_KEY"))

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="deepseek" if self.available else "deepseek_unavailable",
            supports_code_generation=self.available,
            supports_repair=self.available,
            supports_structured_output=True,
        )

    def run_experiment(self, spec: ExperimentSpec) -> WorkerResult:
        return WorkerResult(
            status="not_implemented",
            changed_files=[],
            summary="DeepSeekWorker is used through strategy-profile generation in the standard agent runner.",
        )

    def generate_strategy_profile(
        self,
        *,
        docs: str,
        previous_report: str,
        output_dir: Path,
        round_index: int,
        max_tokens: int = 2500,
    ) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        client = DeepSeekClient.from_env(model=self.model)
        prompt = self._profile_prompt(docs, previous_report, round_index)
        content = client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are an FJSP heuristic designer. Return valid JSON only. "
                        "Do not claim results you have not evaluated."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.35,
            max_tokens=max_tokens,
            json_mode=True,
        )
        (output_dir / "deepseek_raw_response.json").write_text(content, encoding="utf-8")
        profile = extract_json_object(content)
        normalized = normalize_strategy_profile(profile)
        profile_path = output_dir / "strategy_profile.json"
        strategy_path = output_dir / "strategy.md"
        profile_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        strategy_path.write_text(render_strategy_markdown(normalized, source="DeepSeek"), encoding="utf-8")
        return profile_path, strategy_path

    def _profile_prompt(self, docs: str, previous_report: str, round_index: int) -> str:
        return f"""
We need evolve a standard FJSP heuristic under a fixed evaluator.

Round: {round_index}

Available dispatch features:
{", ".join(FEATURES)}

Return JSON with this schema:
{{
  "rationale": "short natural-language strategy idea",
  "strategies": [
    {{
      "name": "unique_short_name",
      "noise": 0.0,
      "weights": {{"early_finish": 5.0, "remaining_work": 2.0}}
    }}
  ]
}}

Rules:
- Generate 4 to 8 diverse strategies.
- Use only the listed feature names.
- Weights should normally be between -8 and 12.
- Prefer valid, fast constructive heuristics; no warm starts from old solutions.
- If previous reports show high gap, propose genuinely different scoring mixtures.

Requirement and knowledge excerpts:
{docs[:14000]}

Previous report excerpt:
{previous_report[-5000:]}
""".strip()


def normalize_strategy_profile(profile: dict[str, Any]) -> dict[str, Any]:
    strategies: list[dict[str, Any]] = []
    for index, item in enumerate(profile.get("strategies", [])):
        if not isinstance(item, dict):
            continue
        raw_weights = item.get("weights", {})
        if not isinstance(raw_weights, dict):
            continue
        weights: dict[str, float] = {}
        for key, value in raw_weights.items():
            if key not in FEATURES:
                continue
            try:
                weights[str(key)] = max(-12.0, min(12.0, float(value)))
            except (TypeError, ValueError):
                continue
        if not weights:
            continue
        strategies.append(
            {
                "name": str(item.get("name", f"deepseek_{index:03d}"))[:64],
                "noise": max(0.0, min(0.12, float(item.get("noise", 0.0) or 0.0))),
                "weights": weights,
            }
        )
    return {
        "rationale": str(profile.get("rationale", ""))[:4000],
        "strategies": strategies,
    }


def render_strategy_markdown(profile: dict[str, Any], source: str) -> str:
    lines = [f"# Strategy Profile ({source})", "", profile.get("rationale", ""), "", "## Strategies", ""]
    for strategy in profile.get("strategies", []):
        lines.append(f"### {strategy['name']}")
        lines.append("")
        lines.append(f"- noise: `{strategy.get('noise', 0.0)}`")
        lines.append(f"- weights: `{json.dumps(strategy.get('weights', {}), ensure_ascii=False)}`")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def write_template_strategy_profile(output_dir: Path, round_index: int) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    profile = {
        "rationale": (
            "Template profile used when DeepSeek is unavailable. It emphasizes a diverse mix of "
            "early-finish, remaining-work, bottleneck-load, and flexibility-aware dispatch rules."
        ),
        "strategies": [
            {
                "name": f"template_balanced_{round_index}",
                "noise": 0.01,
                "weights": {
                    "early_finish": 5.0,
                    "remaining_work": 3.5,
                    "short_processing": 1.5,
                    "machine_load": 2.5,
                    "flexibility": 1.0,
                },
            },
            {
                "name": f"template_bottleneck_{round_index}",
                "noise": 0.02,
                "weights": {
                    "machine_load": 6.0,
                    "machine_ready": 2.0,
                    "remaining_after": 3.0,
                    "early_finish": 3.0,
                },
            },
            {
                "name": f"template_long_chain_{round_index}",
                "noise": 0.015,
                "weights": {
                    "remaining_work": 7.0,
                    "remaining_ops": 4.0,
                    "early_finish": 2.0,
                    "min_option": 1.0,
                },
            },
        ],
    }
    profile_path = output_dir / "strategy_profile.json"
    strategy_path = output_dir / "strategy.md"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    strategy_path.write_text(render_strategy_markdown(profile, source="template"), encoding="utf-8")
    return profile_path, strategy_path


def generate_profile_auto(
    *,
    docs: str,
    previous_report: str,
    output_dir: Path,
    round_index: int,
    mode: str,
    model: str,
) -> tuple[Path, Path, str]:
    if mode not in {"auto", "deepseek", "template"}:
        raise ValueError(f"unknown profile generation mode: {mode}")
    if mode in {"auto", "deepseek"}:
        try:
            worker = DeepSeekWorker(model=model)
            profile_path, strategy_path = worker.generate_strategy_profile(
                docs=docs,
                previous_report=previous_report,
                output_dir=output_dir,
                round_index=round_index,
            )
            return profile_path, strategy_path, "deepseek"
        except DeepSeekUnavailable:
            if mode == "deepseek":
                raise
        except Exception as exc:  # noqa: BLE001 - record model failure and fall back only in auto mode.
            (output_dir / "deepseek_error.txt").write_text(str(exc), encoding="utf-8")
            if mode == "deepseek":
                raise
    profile_path, strategy_path = write_template_strategy_profile(output_dir, round_index)
    return profile_path, strategy_path, "template"
