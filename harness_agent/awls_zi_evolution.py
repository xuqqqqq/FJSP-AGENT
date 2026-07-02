from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from examples.standard_fjsp_awls_solver import parse_portfolio_lanes, validate_zi_formula

from .awls_benchmark import AwlsBenchmarkRequest, run_awls_benchmark, selected_instances
from .deepseek_client import DeepSeekClient


ZI_POLICY_CHOICES = ("cpp", "none", "sqrt", "aggressive", "critical", "formula")
SAME_MACHINE_EVAL_CHOICES = ("stable", "cpp-fast")
CRITICAL_BLOCK_EXHAUSTIVE_PCT_MAX = 100
CANDIDATE_REPAIR_ATTEMPTS = 1
FAILED_PORTFOLIO_LANE_STRINGS = (
    "0:mixed:1:6,6:mixed:1:6,7:greedy:1:6",
    "2:mixed:1,3:random:1",
    "1:mixed:1:10,4:mixed:1:10",
    "6:mixed:1:6,7:greedy:1:6,3:random:1:6",
    "2:random:1:6,5:greedy:1:6,8:mixed:1:6",
    "1:greedy:1:6,3:random:1:6,7:mixed:1:6",
    "1:random:1:6,7:greedy:1:6,9:mixed:1:6",
)
KNOWN_DIRECT_PROBE_FLAT_OR_WORSE_CONFIGS = {
    ("critical", 400, 40, 5, 50, "stable"),
    ("critical", 400, 40, 5, 60, "stable"),
    ("critical", 400, 40, 5, 75, "stable"),
    ("critical", 400, 40, 5, 90, "stable"),
    ("aggressive", 400, 40, 5, 50, "stable"),
    ("aggressive", 400, 40, 5, 75, "stable"),
}
SDST_SETUP_ZI_SYMBOLS = (
    "setup_prev",
    "setup_next",
    "setup_adjacent",
    "setup_prev_ratio",
    "setup_next_ratio",
    "setup_adjacent_ratio",
    "setup_predecessor_critical",
    "setup_successor_critical",
)
SDST_MEMORY_PATHS = (
    Path(__file__).resolve().parents[1] / "knowledge" / "papers" / "awls_sdst_neighborhood_selection_notes.md",
    Path(__file__).resolve().parents[1] / "knowledge" / "papers" / "awls_sdst_move_evaluation_notes.md",
    Path(__file__).resolve().parents[1] / "knowledge" / "papers" / "awls_sdst_initialization_notes.md",
    Path(__file__).resolve().parents[1] / "knowledge" / "papers" / "awls_sdst_same_machine_notes.md",
    Path(__file__).resolve().parents[1] / "knowledge" / "papers" / "awls_sdst_portfolio_search_control_notes.md",
    Path(__file__).resolve().parents[1] / "knowledge" / "papers" / "awls_sdst_zi_feature_notes.md",
)
SDST_MEMORY_MAX_CHARS_PER_CARD = 6000


class CandidateNormalizationError(ValueError):
    """DeepSeek returned syntactically valid JSON that violates search guards."""


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
    zi_policy: str = "cpp"
    zi_formula: str = ""
    critical_block_exhaustive_pct: int = 0
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
                "zi_policy": request.zi_policy,
                "zi_formula": request.zi_formula,
                "critical_block_exhaustive_pct": request.critical_block_exhaustive_pct,
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
            build_deepseek_messages(prompt),
            temperature=0.35,
            max_tokens=4500,
            json_mode=True,
        )
        prior_signatures = collect_candidate_signatures(baseline_manifest, history)
        validation_errors: list[str] = []
        current_prompt = prompt
        candidates: list[dict[str, Any]] | None = None
        for attempt in range(CANDIDATE_REPAIR_ATTEMPTS + 1):
            raw_path = round_dir / ("deepseek_raw_response.json" if attempt == 0 else f"deepseek_repair_response_{attempt:02d}.json")
            raw_path.write_text(raw, encoding="utf-8")
            profile = extract_json_object(raw)
            try:
                candidates = normalize_candidates(
                    profile,
                    request.candidates_per_round,
                    round_index,
                    prior_signatures=prior_signatures,
                    require_portfolio_candidate=request.candidates_per_round > 1,
                )
                break
            except CandidateNormalizationError as exc:
                validation_errors.append(str(exc))
                if attempt >= CANDIDATE_REPAIR_ATTEMPTS:
                    raise
                current_prompt = build_candidate_repair_prompt(current_prompt, exc, prior_signatures)
                (round_dir / f"deepseek_repair_prompt_{attempt + 1:02d}.md").write_text(current_prompt, encoding="utf-8")
                raw = client.chat(
                    build_deepseek_messages(current_prompt),
                    temperature=0.25,
                    max_tokens=4500,
                    json_mode=True,
                )
        if validation_errors:
            (round_dir / "candidate_validation_errors.json").write_text(
                json.dumps(validation_errors, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if candidates is None:
            raise CandidateNormalizationError("DeepSeek candidate normalization did not produce candidates")
        (round_dir / "normalized_candidates.json").write_text(
            json.dumps(candidates, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        candidate_records: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate_dir = round_dir / "candidates" / candidate["name"]
            try:
                manifest = run_candidate(request, instance_names, candidate_dir, candidate)
            except Exception as exc:  # noqa: BLE001 - candidate failures must not abort the round.
                manifest = write_candidate_failure_manifest(request, instance_names, candidate_dir, candidate, exc)
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
            critical_block_exhaustive_pct=max(
                0,
                min(
                    CRITICAL_BLOCK_EXHAUSTIVE_PCT_MAX,
                    int(candidate.get("critical_block_exhaustive_pct", request.critical_block_exhaustive_pct)),
                ),
            ),
            same_machine_eval=str(candidate.get("same_machine_eval") or request.same_machine_eval),
            time_policy=request.time_policy,
        )
    )


def write_candidate_failure_manifest(
    request: AwlsZiEvolutionRequest,
    instance_names: list[str],
    output_dir: Path,
    candidate: dict[str, Any],
    exc: Exception,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"
    seeds = request.seeds or [0]
    error = f"{type(exc).__name__}: {exc}"
    manifest = {
        "status": "candidate_failed",
        "request": {
            **request_to_json(request),
            "candidate": candidate,
        },
        "selected_instance_names": instance_names,
        "errors": [error],
        "aggregate": {
            "instance_count": len(instance_names),
            "seed_count": len(seeds),
            "seeds": seeds,
            "run_count": len(instance_names) * len(seeds),
            "valid_run_count": 0,
            "invalid_run_count": max(1, len(instance_names) * len(seeds)),
            "valid_instance_count": 0,
            "invalid_instance_count": len(instance_names),
            "avg_makespan": None,
            "avg_gap_pct": None,
            "median_gap_pct": None,
            "max_gap_pct": None,
            "best_reached_count": 0,
            "within_1pct_count": 0,
            "within_2pct_count": 0,
            "gap_count": 0,
        },
        "instances": [
            {
                "instance": name,
                "status": "failed",
                "valid": False,
                "error_count": 1,
                "errors": [error],
                "makespan": None,
                "gap_pct": None,
            }
            for name in instance_names
        ],
        "runs": [
            {
                "instance": name,
                "seed": seed,
                "status": "failed",
                "valid": False,
                "error_count": 1,
                "errors": [error],
                "makespan": None,
                "gap_pct": None,
            }
            for name in instance_names
            for seed in seeds
        ],
        "artifacts": {
            "summary": str(manifest_path),
            "report": str(report_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_candidate_failure_report(manifest), encoding="utf-8")
    return manifest


def render_candidate_failure_report(manifest: dict[str, Any]) -> str:
    lines = [
        "# AWLS zi Candidate Failure",
        "",
        f"- Status: `{manifest.get('status')}`",
        f"- Instances: `{len(manifest.get('selected_instance_names') or [])}`",
        "",
        "## Errors",
        "",
    ]
    for error in manifest.get("errors", []):
        lines.append(f"- `{error}`")
    return "\n".join(lines).strip() + "\n"


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
    sdst_memory = load_sdst_neighborhood_memory()
    return f"""
We are evolving the adaptive zi-weight and bounded AWLS search controls inside
an AWLS solver for standard FJSP / FJSP-SDST benchmarks. You must propose
structured candidates only; do not write solver code and do not claim any
unmeasured result.

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
  `machine_load`, `position`, `setup_prev`, `setup_next`, `setup_adjacent`,
  `setup_prev_ratio`, `setup_next_ratio`, `setup_adjacent_ratio`,
  `setup_is_sdst`, `setup_predecessor_critical`, and
  `setup_successor_critical`.
- Allowed functions in `zi_formula`: `max`, `min`, `abs`, `sqrt`, `log1p`.
- Formula outputs are clipped to finite non-negative values. Prefer simple
  interpretable formulas such as `base * (1 + 0.3 * is_critical)` or
  `max(0, base + 0.05 * backward * is_critical)`.

Other allowed knobs:
- `same_machine_eval`: {", ".join(SAME_MACHINE_EVAL_CHOICES)}.
- `critical_block_exhaustive_pct`: integer 0..100.  This controls how often
  AWLS exhaustively scans critical-block candidates before falling back to the
  faster stochastic sampler; measured SDST-HUdata `oddla20` evidence found
  values above 25 can be materially better.
- `portfolio_lanes`: optional AWLS lane string, e.g.
  `3:random:1,5:mixed:1,17:random:1,0:mixed:1,8:greedy:1`.
  Use it when measured evidence shows seed or initialization variance.  For
  SDST-HUdata this can be a first-class search lever: a lane such as
  `2:mixed:1:8` means run AWLS with lane seed 2, mixed initialization, one
  restart, and an 8-second lane budget.
- The platform rejects exact duplicate configurations, known failed portfolio
  lane strings, all-formula-only multi-candidate rounds, and multi-candidate
  rounds with no bounded `portfolio_lanes` candidate.

Local SDST-HUdata measured memory and cautions:
{sdst_memory}

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
- Keep `portfolio_lanes` bounded: normally 2 to 6 lanes, each formatted
  `seed:init:restarts[:seconds]`, with init in random/greedy/mixed.
- If more than one candidate is requested, at least one candidate should use a
  non-empty `portfolio_lanes` string unless the recent measured history proves
  such lanes are harmful under this exact incumbent.  Treat seed/init/lane
  choice as a first-class hypothesis, not as noise.
- Do not retry known failed portfolio strings:
  `{", ".join(FAILED_PORTFOLIO_LANE_STRINGS)}`.
- Do not retry no-portfolio direct pct probes under
  `beta400/gamma40/theta5 + stable`: `critical` pct `50/60/75/90` and
  `aggressive` pct `50/75` have tied or worsened the current `1010`
  incumbent on `oddla20`.
- Avoid spending a full round only on `same_machine_eval=cpp-fast` or another
  small critical/cooldown multiplier formula after measured memory says those
  ideas tied or worsened the `1010` incumbent.
- Use `zi_policy=formula` for at least one candidate when prior evidence shows
  the fixed policies are flat or worse; keep formulas short and diverse.
- If using `zi_policy=formula` on SDST-HUdata, prefer at least one materially
  setup-aware expression using `setup_prev`, `setup_next`, or
  `setup_adjacent_ratio` rather than another pure critical multiplier.
- Recent setup-ratio formulas that only multiply `base` by
  `is_critical * setup_*_ratio` tied or worsened.  A new formula should change
  the gate structure, include another AWLS pressure term, or use a different
  mechanism rather than only adjusting that coefficient.
- Prefer interpretable changes to the zi mechanism. This is a controlled
  evolution experiment, not a free code rewrite.
""".strip()


def build_deepseek_messages(prompt: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are an FJSP AWLS parameter-policy designer. Return compact valid JSON only. "
                "Use only measured evaluator evidence provided by the user."
            ),
        },
        {"role": "user", "content": prompt},
    ]


def build_candidate_repair_prompt(
    prompt: str,
    error: CandidateNormalizationError,
    prior_signatures: set[str],
) -> str:
    signatures = "\n".join(f"- {item}" for item in sorted(prior_signatures)) or "- (none)"
    return f"""
{prompt}

Your previous JSON was rejected by the platform candidate gate.

Validation error:
{error}

Already measured / forbidden exact configuration signatures:
{signatures}

Return corrected JSON only. Keep the same schema and candidate count, but make
the candidates materially distinct. At least one candidate must use a bounded,
non-empty portfolio_lanes string that is not listed as a failed portfolio.
""".strip()


def load_sdst_neighborhood_memory() -> str:
    sections: list[str] = []
    for path in SDST_MEMORY_PATHS:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if len(text) > SDST_MEMORY_MAX_CHARS_PER_CARD:
            text = text[:SDST_MEMORY_MAX_CHARS_PER_CARD].rstrip() + "\n\n...(truncated)"
        sections.append(f"## Memory Card: {path.name}\n\n{text}")
    if not sections:
        return "(No project-local SDST memory cards found.)"
    return "\n\n---\n\n".join(sections)


def normalize_candidates(
    profile: dict[str, Any],
    count: int,
    round_index: int,
    *,
    prior_signatures: set[str] | None = None,
    require_portfolio_candidate: bool = False,
    forbidden_portfolios: set[str] | None = None,
) -> list[dict[str, Any]]:
    raw_candidates = profile.get("candidates")
    if not isinstance(raw_candidates, list):
        raise CandidateNormalizationError("DeepSeek response must contain a candidates list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_signatures: set[str] = set()
    prior = set(prior_signatures or set())
    forbidden = set(forbidden_portfolios or normalized_failed_portfolios())
    rejection_reasons: list[str] = []
    for index, raw in enumerate(raw_candidates):
        if not isinstance(raw, dict):
            continue
        name = safe_name(str(raw.get("name") or f"candidate_{index:02d}"))
        if name in seen:
            name = f"{name}_{index:02d}"
        seen.add(name)
        policy = str(raw.get("zi_policy") or "cpp")
        if policy not in ZI_POLICY_CHOICES:
            raise CandidateNormalizationError(f"DeepSeek proposed unsupported zi_policy: {policy}")
        formula = str(raw.get("zi_formula") or "")
        if policy == "formula":
            try:
                formula = validate_zi_formula(formula)
            except ValueError as exc:
                raise CandidateNormalizationError(f"{name}: invalid zi_formula: {exc}") from exc
        else:
            formula = ""
        eval_mode = str(raw.get("same_machine_eval") or "stable")
        if eval_mode not in SAME_MACHINE_EVAL_CHOICES:
            raise CandidateNormalizationError(f"DeepSeek proposed unsupported same_machine_eval: {eval_mode}")
        try:
            portfolio_lanes = normalize_portfolio_lanes(str(raw.get("portfolio_lanes") or ""))
        except ValueError as exc:
            raise CandidateNormalizationError(f"{name}: invalid portfolio_lanes: {exc}") from exc
        candidate = {
            "name": f"r{round_index:02d}_{name}",
            "rationale": str(raw.get("rationale") or ""),
            "beta": clamp_int(raw.get("beta"), 500, 50, 1500),
            "gamma": clamp_int(raw.get("gamma"), 40, 5, 120),
            "theta": clamp_int(raw.get("theta"), 5, 0, 20),
            "zi_policy": policy,
            "zi_formula": formula,
            "critical_block_exhaustive_pct": clamp_int(
                raw.get("critical_block_exhaustive_pct"),
                0,
                0,
                CRITICAL_BLOCK_EXHAUSTIVE_PCT_MAX,
            ),
            "same_machine_eval": eval_mode,
            "portfolio_lanes": portfolio_lanes,
        }
        signature = candidate_signature(candidate)
        if signature in prior:
            rejection_reasons.append(f"{name}: repeats prior measured configuration {signature}")
            continue
        if signature in seen_signatures:
            rejection_reasons.append(f"{name}: duplicates another candidate in this round {signature}")
            continue
        if portfolio_lanes and portfolio_lanes in forbidden:
            rejection_reasons.append(f"{name}: repeats failed portfolio_lanes {portfolio_lanes}")
            continue
        if is_known_direct_probe_flat_or_worse(candidate):
            rejection_reasons.append(f"{name}: repeats known flat/worse direct pct probe")
            continue
        if (
            policy == "sqrt"
            and not portfolio_lanes
            and eval_mode == "stable"
            and int(candidate.get("critical_block_exhaustive_pct") or 0) == 75
        ):
            rejection_reasons.append(f"{name}: repeats failed sqrt stable pct75 configuration")
            continue
        normalized.append(candidate)
        seen_signatures.add(signature)
        if len(normalized) >= count:
            break
    if len(normalized) != count:
        suffix = "; ".join(rejection_reasons[:5])
        detail = f" ({suffix})" if suffix else ""
        raise CandidateNormalizationError(f"DeepSeek returned {len(normalized)} usable candidates; expected {count}{detail}")
    if require_portfolio_candidate and not any(candidate.get("portfolio_lanes") for candidate in normalized):
        raise CandidateNormalizationError("multi-candidate SDST rounds require at least one non-empty portfolio_lanes candidate")
    if count > 1 and all(candidate.get("zi_policy") == "formula" and not candidate.get("portfolio_lanes") for candidate in normalized):
        raise CandidateNormalizationError("multi-candidate rounds may not be all formula-only without portfolio_lanes")
    if count > 1 and any(candidate.get("zi_policy") == "formula" for candidate in normalized):
        setup_formula_count = sum(
            1
            for candidate in normalized
            if candidate.get("zi_policy") == "formula" and formula_uses_sdst_setup_features(str(candidate.get("zi_formula") or ""))
        )
        if setup_formula_count == 0:
            raise CandidateNormalizationError("SDST formula rounds require at least one zi_formula using setup_* features")
    return normalized


def formula_uses_sdst_setup_features(formula: str) -> bool:
    return any(symbol in formula for symbol in SDST_SETUP_ZI_SYMBOLS)


def is_known_direct_probe_flat_or_worse(candidate: dict[str, Any]) -> bool:
    if candidate.get("portfolio_lanes"):
        return False
    if candidate.get("zi_policy") == "formula":
        return False
    key = (
        str(candidate.get("zi_policy") or ""),
        int(candidate.get("beta") or 0),
        int(candidate.get("gamma") or 0),
        int(candidate.get("theta") or 0),
        int(candidate.get("critical_block_exhaustive_pct") or 0),
        str(candidate.get("same_machine_eval") or ""),
    )
    return key in KNOWN_DIRECT_PROBE_FLAT_OR_WORSE_CONFIGS


def candidate_signature(candidate: dict[str, Any]) -> str:
    policy = str(candidate.get("zi_policy") or "cpp")
    formula = str(candidate.get("zi_formula") or "") if policy == "formula" else ""
    return ";".join(
        [
            f"beta={candidate.get('beta', '')}",
            f"gamma={candidate.get('gamma', '')}",
            f"theta={candidate.get('theta', '')}",
            f"zi_policy={policy}",
            f"zi_formula={formula}",
            f"critical_block_exhaustive_pct={candidate.get('critical_block_exhaustive_pct', '')}",
            f"same_machine_eval={candidate.get('same_machine_eval', '')}",
            f"portfolio_lanes={candidate.get('portfolio_lanes', '') or ''}",
        ]
    )


def collect_candidate_signatures(baseline_manifest: dict[str, Any], history: list[dict[str, Any]]) -> set[str]:
    signatures: set[str] = set()
    baseline_config = candidate_config_from_benchmark_manifest(baseline_manifest)
    if baseline_config:
        signatures.add(candidate_signature(baseline_config))
    for round_record in history:
        for record in round_record.get("candidates", []):
            if not isinstance(record, dict):
                continue
            config = record.get("candidate")
            if isinstance(config, dict) and config:
                signatures.add(candidate_signature(config))
        best = round_record.get("best_after_round")
        if isinstance(best, dict):
            config = best.get("candidate")
            if isinstance(config, dict) and config:
                signatures.add(candidate_signature(config))
    return signatures


def candidate_record(name: str, source: str, manifest: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    aggregate = manifest.get("aggregate") or {}
    config = candidate or candidate_config_from_benchmark_manifest(manifest)
    return {
        "name": name,
        "source": source,
        "status": manifest.get("status"),
        "errors": manifest_errors(manifest),
        "candidate": config,
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


def manifest_errors(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for error in manifest.get("errors") or []:
        if isinstance(error, str) and error not in errors:
            errors.append(error)
    for section in ("instances", "runs"):
        for item in manifest.get(section, []):
            for error in item.get("errors") or []:
                if isinstance(error, str) and error not in errors:
                    errors.append(error)
    return errors[:5]


def is_better(candidate: dict[str, Any], incumbent: dict[str, Any]) -> bool:
    candidate_gap = candidate.get("avg_gap_pct")
    incumbent_gap = incumbent.get("avg_gap_pct")
    candidate_invalid = candidate.get("invalid_run_count")
    incumbent_invalid = incumbent.get("invalid_run_count")
    if isinstance(candidate_invalid, int) and candidate_invalid > 0:
        return False
    if isinstance(incumbent_invalid, int) and incumbent_invalid > 0:
        return True
    if isinstance(candidate_gap, (int, float)):
        if not isinstance(incumbent_gap, (int, float)):
            return True
        return float(candidate_gap) < float(incumbent_gap)
    if not isinstance(incumbent_gap, (int, float)):
        candidate_makespan = candidate.get("avg_makespan")
        incumbent_makespan = incumbent.get("avg_makespan")
        if isinstance(candidate_makespan, (int, float)) and isinstance(incumbent_makespan, (int, float)):
            return float(candidate_makespan) < float(incumbent_makespan)
    return False


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
    baseline = manifest.get("baseline") or {}
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
        "| Round | Candidate | Status | Avg Makespan | ΔMakespan | Avg Gap % | ΔGap % | Median Gap % | Max Gap % | Invalid Runs | Error | zi Policy | Formula | beta | gamma | theta | Critical Exhaustive % | Same-Machine Eval | Portfolio | Report |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    lines.append(candidate_row("baseline", baseline, baseline))
    for round_record in manifest.get("rounds", []):
        round_label = f"round_{round_record.get('round_index', 0):02d}"
        for candidate in round_record.get("candidates", []):
            lines.append(candidate_row(round_label, candidate, baseline))
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


def candidate_row(round_label: str, candidate: dict[str, Any], baseline: dict[str, Any] | None = None) -> str:
    config = candidate.get("candidate") or {}
    report = candidate.get("report") or ""
    report_cell = f"[report]({report})" if report else ""
    delta_makespan = numeric_delta(candidate.get("avg_makespan"), (baseline or {}).get("avg_makespan"))
    delta_gap = numeric_delta(candidate.get("avg_gap_pct"), (baseline or {}).get("avg_gap_pct"))
    errors = candidate.get("errors") or []
    error_cell = inline_code_cell(str(errors[0])) if errors else ""
    return (
        f"| {round_label} | {candidate.get('name')} | {candidate.get('status', '')} | "
        f"{format_cell(candidate.get('avg_makespan'))} | "
        f"{format_cell(delta_makespan)} | {format_cell(candidate.get('avg_gap_pct'))} | {format_cell(delta_gap)} | "
        f"{format_cell(candidate.get('median_gap_pct'))} | {format_cell(candidate.get('max_gap_pct'))} | "
        f"{format_cell(candidate.get('invalid_run_count'))} | {error_cell} | {config.get('zi_policy', 'cpp')} | "
        f"`{config.get('zi_formula', '') or ''}` | "
        f"{config.get('beta', '')} | {config.get('gamma', '')} | {config.get('theta', '')} | "
        f"{format_cell(config.get('critical_block_exhaustive_pct'))} | "
        f"{config.get('same_machine_eval', '') or ''} | "
        f"`{config.get('portfolio_lanes', '') or ''}` | {report_cell} |"
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
        "runs": [
            {
                "instance": item.get("instance"),
                "seed": item.get("seed"),
                "makespan": item.get("makespan"),
                "gap_pct": item.get("gap_pct"),
                "strategy": item.get("strategy"),
            }
            for item in sorted(
                manifest.get("runs", []),
                key=lambda item: (
                    float(item.get("makespan")) if isinstance(item.get("makespan"), (int, float)) else float("inf"),
                    int(item.get("seed", 0) or 0),
                ),
            )[:10]
        ],
        "request": candidate_config_from_benchmark_manifest(manifest),
    }


def candidate_config_from_benchmark_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    request = manifest.get("request") or {}
    if not isinstance(request, dict):
        return {}
    keys = (
        "init_mode",
        "restarts",
        "cycles_per_restart",
        "iterations",
        "time_limit_sec",
        "exact_select_top_k",
        "beta",
        "gamma",
        "theta",
        "zi_policy",
        "zi_formula",
        "critical_block_exhaustive_pct",
        "same_machine_eval",
        "portfolio_lanes",
        "time_policy",
    )
    return {key: request.get(key) for key in keys if request.get(key) not in (None, "")}


def compact_history(rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for round_record in rounds:
        compact.append(
            {
                "round_index": round_record.get("round_index"),
                "candidates": [
                    {
                        "name": candidate.get("name"),
                        "status": candidate.get("status"),
                        "errors": candidate.get("errors"),
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


def normalize_portfolio_lanes(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    lanes = parse_portfolio_lanes(text)
    if len(lanes) > 8:
        raise ValueError("DeepSeek proposed too many portfolio lanes; maximum is 8")
    for lane in lanes:
        if lane.restarts > 4:
            raise ValueError("DeepSeek proposed a portfolio lane with restarts > 4")
        if lane.time_limit_sec is not None and lane.time_limit_sec > 300:
            raise ValueError("DeepSeek proposed a portfolio lane budget > 300 seconds")
    return ",".join(
        f"{lane.seed}:{lane.init_mode}:{lane.restarts}"
        + (f":{format_float_for_lane(lane.time_limit_sec)}" if lane.time_limit_sec is not None else "")
        for lane in lanes
    )


def normalized_failed_portfolios() -> set[str]:
    failed: set[str] = set()
    for raw in FAILED_PORTFOLIO_LANE_STRINGS:
        try:
            failed.add(normalize_portfolio_lanes(raw))
        except ValueError:
            failed.add(raw)
    return failed


def format_float_for_lane(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def numeric_delta(value: Any, baseline: Any) -> float | None:
    if isinstance(value, (int, float)) and isinstance(baseline, (int, float)):
        return float(value) - float(baseline)
    return None


def safe_name(raw: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw.strip())[:64].strip("_")
    return text or "candidate"


def format_cell(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if value is None:
        return "N/A"
    return str(value)


def inline_code_cell(value: str, limit: int = 160) -> str:
    text = value.replace("|", "\\|").replace("`", "'")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return f"`{text}`"
