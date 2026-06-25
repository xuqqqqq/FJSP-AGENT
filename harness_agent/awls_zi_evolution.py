from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from examples.standard_fjsp_awls_solver import validate_zi_formula

from .awls_benchmark import AwlsBenchmarkRequest, run_awls_benchmark, selected_instances
from .deepseek_client import DeepSeekClient


ZI_POLICY_CHOICES = ("cpp", "none", "sqrt", "aggressive", "critical", "formula")
SAME_MACHINE_EVAL_CHOICES = ("stable", "cpp-fast")


@dataclass(frozen=True)
class AwlsZiEvolutionRequest:
    """Run a DeepSeek-driven evolution loop over AWLS zi-weight settings.

    The loop is intentionally narrower than a free coding-worker loop: DeepSeek
    proposes structured AWLS zi candidates, the fixed evaluator runs them, and
    only measured benchmark evidence is fed into the next round.
    """

    instance_dir: Path
    pattern: str
    output_dir: Path
    best_known_csv: Path | None = None
    max_instances: int | None = None
    include_families: list[str] | None = None
    instance_names: list[str] | None = None
    sample_count: int | None = None
    sample_seed: int = 0
    rounds: int = 3
    candidates_per_round: int = 2
    deepseek_model: str = "deepseek-v4-pro"
    seeds: list[int] | None = None
    max_workers: int = 1
    restarts: int = 2
    cycles_per_restart: int = 1000
    iterations: int = 10000
    time_limit_sec: float = 10.0
    init_mode: str = "random"
    exact_select_top_k: int = 0
    beta: int = 500
    gamma: int = 40
    theta: int = 5
    portfolio_lanes: str = ""
    same_machine_eval: str = "stable"
    time_policy: str = "fixed"
    baseline_summary: Path | None = None


def run_awls_zi_evolution(request: AwlsZiEvolutionRequest) -> dict[str, Any]:
    output_dir = request.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    instances = selected_instances(
        AwlsBenchmarkRequest(
            instance_dir=request.instance_dir,
            pattern=request.pattern,
            output_dir=output_dir / "_selection_probe",
            best_known_csv=request.best_known_csv,
            max_instances=request.max_instances,
            include_families=request.include_families,
            instance_names=request.instance_names,
            sample_count=request.sample_count,
            sample_seed=request.sample_seed,
        )
    )
    instance_names = [path.name for path in instances]
    if not instance_names:
        raise ValueError("no instances selected for AWLS zi evolution")

    baseline_manifest = load_optional_manifest(request.baseline_summary)
    if baseline_manifest is None:
        baseline_manifest = run_candidate(
            request,
            instance_names,
            output_dir / "baseline_cpp",
            {
                "name": "baseline_cpp",
                "rationale": "Reference AWLS zi settings before DeepSeek evolution.",
                "beta": request.beta,
                "gamma": request.gamma,
                "theta": request.theta,
                "zi_policy": "cpp",
                "critical_block_exhaustive_pct": 0,
                "same_machine_eval": request.same_machine_eval,
                "portfolio_lanes": request.portfolio_lanes,
            },
        )

    client = DeepSeekClient.from_env(model=request.deepseek_model)
    history: list[dict[str, Any]] = []
    best = candidate_record("baseline_cpp", "baseline", baseline_manifest, {})
    rounds: list[dict[str, Any]] = []

    for round_index in range(max(1, request.rounds)):
        round_dir = output_dir / f"round_{round_index:02d}"
        round_dir.mkdir(parents=True, exist_ok=True)
        prompt = build_deepseek_prompt(request, instance_names, baseline_manifest, history, best, round_index)
        (round_dir / "deepseek_prompt.md").write_text(prompt, encoding="utf-8")
        raw = client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are an FJSP AWLS parameter-policy designer. Return compact valid JSON only. "
                        "Use only measured evaluator evidence provided by the user."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.35,
            max_tokens=4500,
            json_mode=True,
        )
        (round_dir / "deepseek_raw_response.json").write_text(raw, encoding="utf-8")
        profile = extract_json_object(raw)
        candidates = normalize_candidates(profile, request.candidates_per_round, round_index)
        (round_dir / "normalized_candidates.json").write_text(
            json.dumps(candidates, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        candidate_records: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate_dir = round_dir / "candidates" / candidate["name"]
            manifest = run_candidate(request, instance_names, candidate_dir, candidate)
            record = candidate_record(candidate["name"], f"round_{round_index:02d}", manifest, candidate)
            candidate_records.append(record)
            if is_better(record, best):
                best = record

        round_record = {
            "round_index": round_index,
            "candidates": candidate_records,
            "best_after_round": best,
        }
        rounds.append(round_record)
        history.append(round_record)
        write_manifest(output_dir, request, instance_names, baseline_manifest, rounds, best, status="running")

    return write_manifest(output_dir, request, instance_names, baseline_manifest, rounds, best, status="ok")


def run_candidate(
    request: AwlsZiEvolutionRequest,
    instance_names: list[str],
    output_dir: Path,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    return run_awls_benchmark(
        AwlsBenchmarkRequest(
            instance_dir=request.instance_dir,
            pattern=request.pattern,
            output_dir=output_dir,
            best_known_csv=request.best_known_csv,
            instance_names=instance_names,
            seeds=request.seeds or [0],
            max_workers=max(1, request.max_workers),
            restarts=max(1, int(candidate.get("restarts", request.restarts))),
            cycles_per_restart=max(1, request.cycles_per_restart),
            iterations=max(0, request.iterations),
            time_limit_sec=max(0.0, request.time_limit_sec),
            init_mode=request.init_mode,
            exact_select_top_k=max(0, request.exact_select_top_k),
            beta=max(1, int(candidate["beta"])),
            gamma=max(1, int(candidate["gamma"])),
            theta=max(0, int(candidate["theta"])),
            zi_policy=str(candidate["zi_policy"]),
            zi_formula=str(candidate.get("zi_formula") or ""),
            portfolio_lanes=str(candidate.get("portfolio_lanes") or request.portfolio_lanes or ""),
            critical_block_exhaustive_pct=max(0, min(25, int(candidate.get("critical_block_exhaustive_pct", 0)))),
            same_machine_eval=str(candidate.get("same_machine_eval") or request.same_machine_eval),
            time_policy=request.time_policy,
        )
    )


def build_deepseek_prompt(
    request: AwlsZiEvolutionRequest,
    instance_names: list[str],
    baseline_manifest: dict[str, Any],
    history: list[dict[str, Any]],
    best: dict[str, Any],
    round_index: int,
) -> str:
    baseline_summary = compact_summary(baseline_manifest)
    recent_history = compact_history(history[-3:])
    return f"""
We are evolving the adaptive zi-weight mechanism inside an AWLS solver for
standard FJSP benchmarks. You must propose structured candidates only; do not
write solver code and do not claim any unmeasured result.

Round: {round_index}
Selected benchmark instances:
{json.dumps(instance_names, ensure_ascii=False, indent=2)}

AWLS zi mechanism currently exposed to you:
- `zi = max(0, 1 - op_cooldown / rr) * op_weight` under `zi_policy=cpp`.
- `rr` is sampled from `(0, gamma]`.
- `op_weight` and `op_cooldown` are updated after each move.
- `beta`, `gamma`, and `theta` control the weight reset threshold, random
  perturbation scale, and cooldown decrease step.
- `zi_policy` choices are: {", ".join(ZI_POLICY_CHOICES)}.
  - cpp: original AWLS-style perturbation.
  - none: ablation that disables zi perturbation.
  - sqrt: weakens high operation weights with sqrt(weight).
  - aggressive: increases moved-operation weight and cooldown pressure faster.
  - critical: gives critical operations a larger zi pressure.
  - formula: use your proposed `zi_formula` arithmetic expression.
- When `zi_policy=formula`, `zi_formula` can use only these variables:
  `base`, `weight`, `cooldown`, `rr`, `gamma`, `cooling`, `sqrt_weight`,
  `log_weight`, `is_critical`, `forward`, `backward`, `duration`,
  `machine_load`, `position`.
- Allowed functions in `zi_formula`: `max`, `min`, `abs`, `sqrt`, `log1p`.
- Formula outputs are clipped to finite non-negative values. Prefer simple
  interpretable formulas such as `base * (1 + 0.3 * is_critical)` or
  `max(0, base + 0.05 * backward * is_critical)`.

Other allowed knobs:
- `same_machine_eval`: {", ".join(SAME_MACHINE_EVAL_CHOICES)}.
- `critical_block_exhaustive_pct`: integer 0..25.
- `portfolio_lanes`: optional AWLS lane string, e.g.
  `3:random:1,5:mixed:1,17:random:1,0:mixed:1,8:greedy:1`.
  Keep it empty unless evidence suggests a portfolio change.

Baseline evaluator evidence:
{json.dumps(baseline_summary, ensure_ascii=False, indent=2)}

Best candidate so far:
{json.dumps(best, ensure_ascii=False, indent=2)}

Recent measured history:
{json.dumps(recent_history, ensure_ascii=False, indent=2)}

Return JSON only with exactly this schema:
{{
  "rationale": "short explanation of the round-level search direction",
  "candidates": [
    {{
      "name": "short_unique_name",
      "rationale": "why this candidate should improve measured avg_gap_pct",
      "beta": 500,
      "gamma": 40,
      "theta": 5,
      "zi_policy": "cpp",
      "zi_formula": "",
      "critical_block_exhaustive_pct": 0,
      "same_machine_eval": "stable",
      "portfolio_lanes": ""
    }}
  ]
}}

Rules:
- Generate exactly {max(1, request.candidates_per_round)} diverse candidates.
- Optimize lower avg_gap_pct first, validity second. The evaluator will decide.
- Do not repeat an exact prior configuration unless your rationale explains why.
- Keep `beta` in 50..1500, `gamma` in 5..120, `theta` in 0..20.
- Use `zi_policy=formula` for at least one candidate when prior evidence shows
  the fixed policies are flat or worse; keep formulas short and diverse.
- Prefer interpretable changes to the zi mechanism. This is a controlled
  evolution experiment, not a free code rewrite.
""".strip()


def normalize_candidates(profile: dict[str, Any], count: int, round_index: int) -> list[dict[str, Any]]:
    raw_candidates = profile.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("DeepSeek response must contain a candidates list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_candidates):
        if not isinstance(raw, dict):
            continue
        name = safe_name(str(raw.get("name") or f"candidate_{index:02d}"))
        if name in seen:
            name = f"{name}_{index:02d}"
        seen.add(name)
        policy = str(raw.get("zi_policy") or "cpp")
        if policy not in ZI_POLICY_CHOICES:
            raise ValueError(f"DeepSeek proposed unsupported zi_policy: {policy}")
        formula = str(raw.get("zi_formula") or "")
        if policy == "formula":
            formula = validate_zi_formula(formula)
        else:
            formula = ""
        eval_mode = str(raw.get("same_machine_eval") or "stable")
        if eval_mode not in SAME_MACHINE_EVAL_CHOICES:
            raise ValueError(f"DeepSeek proposed unsupported same_machine_eval: {eval_mode}")
        normalized.append(
            {
                "name": f"r{round_index:02d}_{name}",
                "rationale": str(raw.get("rationale") or ""),
                "beta": clamp_int(raw.get("beta"), 500, 50, 1500),
                "gamma": clamp_int(raw.get("gamma"), 40, 5, 120),
                "theta": clamp_int(raw.get("theta"), 5, 0, 20),
                "zi_policy": policy,
                "zi_formula": formula,
                "critical_block_exhaustive_pct": clamp_int(raw.get("critical_block_exhaustive_pct"), 0, 0, 25),
                "same_machine_eval": eval_mode,
                "portfolio_lanes": str(raw.get("portfolio_lanes") or ""),
            }
        )
        if len(normalized) >= count:
            break
    if len(normalized) != count:
        raise ValueError(f"DeepSeek returned {len(normalized)} usable candidates; expected {count}")
    return normalized


def candidate_record(name: str, source: str, manifest: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    aggregate = manifest.get("aggregate") or {}
    return {
        "name": name,
        "source": source,
        "candidate": candidate,
        "summary": str(manifest.get("artifacts", {}).get("summary") or ""),
        "report": str(manifest.get("artifacts", {}).get("report") or ""),
        "valid_instance_count": aggregate.get("valid_instance_count"),
        "invalid_run_count": aggregate.get("invalid_run_count"),
        "avg_makespan": aggregate.get("avg_makespan"),
        "avg_gap_pct": aggregate.get("avg_gap_pct"),
        "median_gap_pct": aggregate.get("median_gap_pct"),
        "max_gap_pct": aggregate.get("max_gap_pct"),
        "best_reached_count": aggregate.get("best_reached_count"),
        "within_1pct_count": aggregate.get("within_1pct_count"),
        "gap_count": aggregate.get("gap_count"),
    }


def is_better(candidate: dict[str, Any], incumbent: dict[str, Any]) -> bool:
    candidate_gap = candidate.get("avg_gap_pct")
    incumbent_gap = incumbent.get("avg_gap_pct")
    candidate_invalid = candidate.get("invalid_run_count")
    incumbent_invalid = incumbent.get("invalid_run_count")
    if not isinstance(candidate_gap, (int, float)):
        return False
    if isinstance(candidate_invalid, int) and candidate_invalid > 0:
        return False
    if not isinstance(incumbent_gap, (int, float)):
        return True
    if isinstance(incumbent_invalid, int) and incumbent_invalid > 0:
        return True
    return float(candidate_gap) < float(incumbent_gap)


def write_manifest(
    output_dir: Path,
    request: AwlsZiEvolutionRequest,
    instance_names: list[str],
    baseline_manifest: dict[str, Any],
    rounds: list[dict[str, Any]],
    best: dict[str, Any],
    *,
    status: str,
) -> dict[str, Any]:
    manifest_path = output_dir / "zi_evolution_summary.json"
    report_path = output_dir / "zi_evolution_report.md"
    manifest = {
        "status": status,
        "request": request_to_json(request),
        "selected_instance_names": instance_names,
        "baseline": candidate_record("baseline_cpp", "baseline", baseline_manifest, {}),
        "rounds": rounds,
        "best": best,
        "artifacts": {
            "summary": str(manifest_path),
            "report": str(report_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_report(manifest), encoding="utf-8")
    return manifest


def render_report(manifest: dict[str, Any]) -> str:
    lines = [
        "# DeepSeek AWLS zi Evolution Report",
        "",
        f"- Status: `{manifest.get('status')}`",
        f"- Instances: `{len(manifest.get('selected_instance_names') or [])}`",
        f"- Best: `{manifest.get('best', {}).get('name')}`",
        f"- Best avg gap pct: `{manifest.get('best', {}).get('avg_gap_pct')}`",
        "",
        "## Candidates",
        "",
        "| Round | Candidate | Avg Makespan | Avg Gap % | Median Gap % | Max Gap % | Invalid Runs | zi Policy | Formula | beta | gamma | theta | Report |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]
    baseline = manifest.get("baseline") or {}
    lines.append(candidate_row("baseline", baseline))
    for round_record in manifest.get("rounds", []):
        round_label = f"round_{round_record.get('round_index', 0):02d}"
        for candidate in round_record.get("candidates", []):
            lines.append(candidate_row(round_label, candidate))
    lines.extend(
        [
            "",
            "## Selected Instances",
            "",
            "```json",
            json.dumps(manifest.get("selected_instance_names") or [], ensure_ascii=False, indent=2),
            "```",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def candidate_row(round_label: str, candidate: dict[str, Any]) -> str:
    config = candidate.get("candidate") or {}
    report = candidate.get("report") or ""
    report_cell = f"[report]({report})" if report else ""
    return (
        f"| {round_label} | {candidate.get('name')} | {format_cell(candidate.get('avg_makespan'))} | "
        f"{format_cell(candidate.get('avg_gap_pct'))} | "
        f"{format_cell(candidate.get('median_gap_pct'))} | {format_cell(candidate.get('max_gap_pct'))} | "
        f"{format_cell(candidate.get('invalid_run_count'))} | {config.get('zi_policy', 'cpp')} | "
        f"`{config.get('zi_formula', '') or ''}` | "
        f"{config.get('beta', '')} | {config.get('gamma', '')} | {config.get('theta', '')} | {report_cell} |"
    )


def compact_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    aggregate = manifest.get("aggregate") or {}
    return {
        "status": manifest.get("status"),
        "aggregate": {
            key: aggregate.get(key)
            for key in (
                "instance_count",
                "valid_instance_count",
                "invalid_run_count",
                "avg_makespan",
                "avg_gap_pct",
                "median_gap_pct",
                "max_gap_pct",
                "best_reached_count",
                "within_1pct_count",
                "within_2pct_count",
            )
        },
        "instances": [
            {
                "instance": item.get("instance"),
                "makespan": item.get("makespan"),
                "best_known_makespan": item.get("best_known_makespan"),
                "gap_pct": item.get("gap_pct"),
                "strategy": item.get("strategy"),
            }
            for item in manifest.get("instances", [])[:20]
        ],
    }


def compact_history(rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for round_record in rounds:
        compact.append(
            {
                "round_index": round_record.get("round_index"),
                "candidates": [
                    {
                        "name": candidate.get("name"),
                        "candidate": candidate.get("candidate"),
                        "avg_gap_pct": candidate.get("avg_gap_pct"),
                        "invalid_run_count": candidate.get("invalid_run_count"),
                    }
                    for candidate in round_record.get("candidates", [])
                ],
                "best_after_round": round_record.get("best_after_round"),
            }
        )
    return compact


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


def load_optional_manifest(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def request_to_json(request: AwlsZiEvolutionRequest) -> dict[str, Any]:
    payload = dict(request.__dict__)
    for key in ("instance_dir", "output_dir", "best_known_csv", "baseline_summary"):
        value = payload.get(key)
        payload[key] = str(value) if value is not None else None
    return payload


def clamp_int(value: Any, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


def safe_name(raw: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw.strip())[:64].strip("_")
    return text or "candidate"


def format_cell(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if value is None:
        return "N/A"
    return str(value)
