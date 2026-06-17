from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..deepseek_client import DeepSeekClient, DeepSeekUnavailable, is_deepseek_configured
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

LOCAL_SEARCH_NEIGHBORHOODS = ["random", "critical-block", "combined", "hgtsa-lite", "hybrid", "awls-hybrid"]
RULE_OPERATOR_TYPES = [
    "dispatch_rule",
    "local_search_operator",
    "path_selection",
    "repair_rule",
    "parameter_policy",
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
        self.available = is_deepseek_configured()

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="deepseek" if self.available else "deepseek_unavailable",
            supports_code_generation=self.available,
            supports_repair=self.available,
            supports_structured_output=True,
        )

    def run_experiment(self, spec: ExperimentSpec) -> WorkerResult:
        output_dir = Path(spec.output_dir) if spec.output_dir else Path(spec.worktree_path) / ".algoforge_worker" / spec.experiment_id
        output_dir.mkdir(parents=True, exist_ok=True)
        if not self.available:
            return WorkerResult(
                status="unavailable",
                changed_files=[],
                summary="DeepSeek API is not configured.",
                artifacts={"output_dir": str(output_dir)},
            )

        context = json.loads(Path(spec.context_packet_path).read_text(encoding="utf-8-sig"))
        client = DeepSeekClient.from_env(model=self.model)
        prompt = self._code_edit_prompt(context=context, max_steps=spec.max_steps)
        content = client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a guarded coding agent. Return compact valid JSON only. "
                        "Do not claim benchmark success. Do not request forbidden file edits."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=7000,
            json_mode=True,
        )
        raw_path = output_dir / "deepseek_code_edit_raw.json"
        raw_path.write_text(content, encoding="utf-8")
        try:
            raw_proposal = extract_json_object(content)
        except json.JSONDecodeError as exc:
            repaired = self._repair_code_edit_json(client, content, str(exc), max_tokens=7000)
            (output_dir / "deepseek_code_edit_repair_response.json").write_text(repaired, encoding="utf-8")
            raw_proposal = extract_json_object(repaired)
        proposal = self._normalize_code_edit_proposal(raw_proposal, context)
        proposal_path = output_dir / "proposal.json"
        proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path = output_dir / "proposal.md"
        markdown_path.write_text(render_code_edit_markdown(proposal), encoding="utf-8")

        changed_files: list[str] = []
        if spec.apply_changes:
            changed_files = apply_code_edit_proposal(
                proposal=proposal,
                worktree_path=Path(spec.worktree_path),
                context=context,
            )
            applied_path = output_dir / "applied_files.json"
            applied_path.write_text(json.dumps(changed_files, ensure_ascii=False, indent=2), encoding="utf-8")

        return WorkerResult(
            status="applied" if changed_files else "proposal_created",
            changed_files=changed_files,
            summary=str(proposal.get("summary") or proposal.get("strategy_intent") or "DeepSeek code-edit proposal created."),
            raw_log_path=str(raw_path),
            artifacts={
                "output_dir": str(output_dir),
                "proposal": str(proposal_path),
                "proposal_markdown": str(markdown_path),
            },
        )

    def generate_strategy_profile(
        self,
        *,
        docs: str,
        previous_report: str,
        output_dir: Path,
        round_index: int,
        max_tokens: int = 5000,
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
        try:
            profile = extract_json_object(content)
        except json.JSONDecodeError as exc:
            repaired = self._repair_profile_json(client, content, str(exc), max_tokens=max_tokens)
            (output_dir / "deepseek_repair_response.json").write_text(repaired, encoding="utf-8")
            profile = extract_json_object(repaired)
        normalized = normalize_strategy_profile(profile)
        profile_path = output_dir / "strategy_profile.json"
        strategy_path = output_dir / "strategy.md"
        profile_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        strategy_path.write_text(render_strategy_markdown(normalized, source="DeepSeek"), encoding="utf-8")
        return profile_path, strategy_path

    def generate_reflection(
        self,
        *,
        docs: str,
        report: str,
        hypothesis: dict[str, Any],
        output_dir: Path,
        round_index: int,
        max_tokens: int = 3500,
    ) -> str:
        output_dir.mkdir(parents=True, exist_ok=True)
        client = DeepSeekClient.from_env(model=self.model)
        content = client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are an FJSP algorithm-evolution analyst. "
                        "Use only the evaluator evidence provided. Do not invent results."
                    ),
                },
                {
                    "role": "user",
                    "content": self._reflection_prompt(docs, report, hypothesis, round_index),
                },
            ],
            temperature=0.25,
            max_tokens=max_tokens,
            json_mode=False,
        )
        reflection = content.strip()
        path = output_dir / "deepseek_reflection.md"
        path.write_text(reflection + "\n", encoding="utf-8")
        return reflection + "\n"

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
  ],
  "local_search_profiles": [
    {{
      "name": "combined_balanced",
      "neighborhood_profile": "combined",
      "portfolio_size": 192,
      "restarts": 2,
      "initial_pool_size": 1,
      "iterations": 100,
      "neighbor_limit": 220,
      "time_limit_sec": 4.0,
      "rationale": "why this operator/budget mix should help"
    }}
  ]
}}

Rules:
- Generate exactly 4 to 6 diverse strategies.
- Generate 1 to 3 diverse local_search_profiles.
- Use only the listed feature names.
- Use only these local-search neighborhoods: {", ".join(LOCAL_SEARCH_NEIGHBORHOODS)}.
- Weights should normally be between -8 and 12.
- Prefer valid, fast constructive heuristics; no warm starts from old solutions.
- Local-search profiles are operator/budget hypotheses, not claims. Prefer
  `combined` for stable quality, use `hybrid`, `hgtsa-lite`, or
  `awls-hybrid` only when the previous measured evidence suggests
  N8/k-insertion-style moves may help.
- Return compact valid JSON only; no Markdown, comments, trailing commas, or
  partial objects.
- Feature values already encode scheduling preference direction. For example,
  `early_finish`, `early_start`, `short_processing`, `min_option`,
  `machine_ready`, `machine_load`, `flexibility`, `machine_slack`, and
  `job_slack` are signed so a positive weight usually favors earlier, shorter,
  less loaded, or less slack choices. Do not flip these signs unless previous
  measured evidence justifies it.
- Treat "Structured Hypothesis Feedback" in the previous report as the latest
  measured evidence.
- When `avg_gap_pct` is present, lower `avg_gap_pct` is the main benchmark
  quality target.
- If the previous hypothesis did not improve, propose genuinely different
  scoring mixtures rather than small numeric jitter around the same rule.

Requirement and knowledge excerpts:
{docs[:14000]}

Previous report excerpt:
{previous_report[-5000:]}
""".strip()

    def _reflection_prompt(
        self,
        docs: str,
        report: str,
        hypothesis: dict[str, Any],
        round_index: int,
    ) -> str:
        return f"""
We evaluated one round of a standard FJSP algorithm-evolution agent.

Round: {round_index}

Write a concise Markdown reflection for the next round. Include:
1. what the evaluator actually proved;
2. which dispatch/local-search candidates look promising or harmful;
3. what concrete rule/parameter/operator changes the next strategy profile
   should try;
4. what should not be retried unless new evidence appears.

Rules:
- Do not claim a candidate is good unless the evaluator metrics support it.
- Lower gap/makespan is better. The harness stores comparable scores as
  negative gap/makespan, so a less negative score is better.
- Keep the reflection actionable for the next profile-generation prompt.
- Do not propose reusing solution files or manually tuned warm starts.
- Keep it under 1200 words.

Requirement and knowledge excerpt:
{docs[:8000]}

Structured hypothesis and candidate evidence:
{json.dumps(hypothesis, ensure_ascii=False, indent=2)[:10000]}

Selected harness report excerpt:
{report[:5000]}
""".strip()

    def _code_edit_prompt(self, *, context: dict[str, Any], max_steps: int) -> str:
        compact_context = json.dumps(context, ensure_ascii=False, indent=2)
        return f"""
You are inside an AlgoForge coding-worker loop. The harness/evaluator is the
source of truth; your job is to propose a small code change that can be audited
and then evaluated by Core.

Return JSON only with this schema:
{{
  "summary": "one paragraph summary",
  "strategy_intent": "natural-language strategy before editing code",
  "rule_operator_hypotheses": [
    {{
      "name": "unique_rule_or_operator_name",
      "type": "dispatch_rule",
      "novelty": "how this differs from prior rolled-back or baseline behavior",
      "expected_effect": "which evaluator metric should improve and why",
      "evidence_used": ["contract_review_evidence.role_prioritized_sections", "loop_feedback.previous_rounds"],
      "target_files": ["examples/standard_fjsp_local_search_solver.py"],
      "ablation_plan": "how Core can isolate this rule/operator effect in a later run"
    }}
  ],
  "changes": [
    {{
      "path": "relative/path.py",
      "action": "create_or_replace",
      "content": "full file content",
      "rationale": "why this change helps"
    }}
  ],
  "context_usage": {{
    "used_project_intake": true,
    "referenced_files": ["examples/standard_fjsp_solver.py"],
    "notes": "how the repository map shaped the edit"
  }},
  "quick_test_plan": "command or explanation",
  "risk_notes": ["risk 1"]
}}

Rules:
- Maximum internal reasoning/edit steps requested by Core: {max_steps}.
- Only propose edits under edit_policy.allowed_paths.
- Never propose edits under edit_policy.forbidden_paths or .git/outputs.
- Prefer one small, complete file over many partial edits.
- Preserve existing parser, validator, evaluator, and benchmark semantics unless
  the task contract explicitly asks to implement those surfaces.  For standard
  FJSP runs, prefer importing the existing parser/evaluator helpers instead of
  reimplementing machine-index or duration parsing.
- If project_intake is present, use it to identify entry files, core solver
  files, evaluator/validator files, and test commands before choosing edits.
- In context_usage, explicitly list the project_intake files or commands that
  shaped the proposal.  If project_intake was not useful, explain why.
- State 1 to 3 materially different rule_operator_hypotheses before changes.
  These are rule/operator lineage records, not success claims.  Use types only
  from: {", ".join(RULE_OPERATOR_TYPES)}.
- If previous_pipeline_memory.operator_guidance is present, use its must_do,
  preserve, mutate, and avoid lists when forming rule_operator_hypotheses and
  novelty statements.
- If loop_feedback or previous_pipeline_memory reports rolled-back or duplicate
  proposals, novelty must explain what is materially different this time.
- If contract_review_evidence.role_prioritized_sections is present, cite it in
  evidence_used when the rule/operator comes from objectives, constraints, IO,
  acceptance, or algorithm-guidance sections.
- If the task contract requires human confirmation, say so in risk_notes and
  avoid claiming formal success.
- Do not include Markdown fences or commentary outside JSON.
- Do not include placeholders like TODO-only implementations unless the context
  explicitly requests scaffolding.
- If no safe edit is possible, return an empty "changes" list with an explicit
  risk note.

Context packet:
{compact_context[:26000]}
""".strip()

    def _repair_code_edit_json(self, client: DeepSeekClient, raw: str, error: str, max_tokens: int) -> str:
        return client.chat(
            [
                {
                    "role": "system",
                    "content": "Repair malformed JSON. Return compact valid JSON only, with no Markdown.",
                },
                {
                    "role": "user",
                    "content": (
                        "The following AlgoForge code-edit proposal was invalid JSON. "
                        "Repair only the JSON structure. Preserve the proposed strategy and code content as much as possible, "
                        "but if full file content is truncated or impossible to repair, return an empty changes list and explain the risk. "
                        "Use exactly these top-level keys: summary, strategy_intent, rule_operator_hypotheses, changes, context_usage, quick_test_plan, risk_notes. "
                        "Each change must use path, action, content, rationale. Return JSON only.\n\n"
                        f"JSON error: {error}\n\n"
                        f"Invalid response:\n{raw[:9000]}"
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=max_tokens,
            json_mode=True,
        )

    def _normalize_code_edit_proposal(self, proposal: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        normalized_changes: list[dict[str, str]] = []
        rejected_changes: list[dict[str, str]] = []
        for item in proposal.get("changes", []):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", "")).strip()
            action = str(item.get("action", "create_or_replace")).strip()
            content = item.get("content")
            if action != "create_or_replace" or not isinstance(content, str):
                rejected_changes.append({"path": path, "reason": "unsupported action or missing content"})
                continue
            allowed, reason = is_path_allowed(path, context)
            if not allowed:
                rejected_changes.append({"path": path, "reason": reason})
                continue
            normalized_changes.append(
                {
                    "path": normalize_relative_path(path),
                    "action": "create_or_replace",
                    "content": content,
                    "rationale": str(item.get("rationale", ""))[:2000],
                }
            )
        normalized = {
            "summary": str(proposal.get("summary", ""))[:4000],
            "strategy_intent": str(proposal.get("strategy_intent", ""))[:4000],
            "rule_operator_hypotheses": normalize_rule_operator_hypotheses(
                proposal.get("rule_operator_hypotheses")
            ),
            "changes": normalized_changes,
            "rejected_changes": rejected_changes,
            "context_usage": normalize_context_usage(proposal.get("context_usage")),
            "quick_test_plan": str(proposal.get("quick_test_plan", ""))[:2000],
            "risk_notes": [str(item)[:1000] for item in proposal.get("risk_notes", []) if isinstance(item, str)],
        }
        normalized["proposal_audit"] = build_proposal_audit(normalized, context)
        return normalized

    def _repair_profile_json(self, client: DeepSeekClient, raw: str, error: str, max_tokens: int) -> str:
        return client.chat(
            [
                {
                    "role": "system",
                    "content": "Repair malformed JSON. Return valid JSON only, with no Markdown.",
                },
                {
                    "role": "user",
                    "content": (
                        "The following FJSP strategy profile was invalid JSON. "
                        "Repair it to exactly this schema: "
                        '{"rationale":"short text","strategies":[{"name":"name","noise":0.0,"weights":{"early_finish":5.0}}],'
                        '"local_search_profiles":[{"name":"combined_balanced","neighborhood_profile":"combined","portfolio_size":192,'
                        '"restarts":2,"initial_pool_size":1,"iterations":100,"neighbor_limit":220,'
                        '"time_limit_sec":4.0,"rationale":"short text"}]}. '
                        "Use only the already present strategy ideas if possible.\n\n"
                        f"JSON error: {error}\n\n"
                        f"Invalid response:\n{raw[:6000]}"
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=max_tokens,
            json_mode=True,
        )


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
        "local_search_profiles": normalize_local_search_profiles(profile),
    }


def normalize_local_search_profiles(profile: dict[str, Any]) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    raw_profiles = profile.get("local_search_profiles", [])
    if not isinstance(raw_profiles, list):
        return profiles
    for index, item in enumerate(raw_profiles):
        if not isinstance(item, dict):
            continue
        neighborhood = str(item.get("neighborhood_profile", item.get("neighborhood", ""))).strip()
        if neighborhood not in LOCAL_SEARCH_NEIGHBORHOODS:
            continue
        try:
            portfolio_size = int(item.get("portfolio_size", 192))
            restarts = int(item.get("restarts", 2))
            initial_pool_size = int(item.get("initial_pool_size", item.get("initials", 1)))
            iterations = int(item.get("iterations", 100))
            neighbor_limit = int(item.get("neighbor_limit", 220))
            time_limit_sec = float(item.get("time_limit_sec", 4.0))
        except (TypeError, ValueError):
            continue
        profiles.append(
            {
                "name": safe_profile_name(str(item.get("name", f"{neighborhood}_{index:02d}"))),
                "neighborhood_profile": neighborhood,
                "portfolio_size": max(32, min(512, portfolio_size)),
                "restarts": max(1, min(6, restarts)),
                "initial_pool_size": max(1, min(4, initial_pool_size)),
                "iterations": max(10, min(320, iterations)),
                "neighbor_limit": max(20, min(520, neighbor_limit)),
                "time_limit_sec": max(0.5, min(15.0, time_limit_sec)),
                "rationale": str(item.get("rationale", ""))[:800],
            }
        )
    return profiles[:3]


def normalize_context_usage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "used_project_intake": False,
            "referenced_files": [],
            "notes": "",
        }
    referenced_files = []
    for item in value.get("referenced_files", []):
        if isinstance(item, str) and item.strip():
            referenced_files.append(normalize_relative_path(item))
    return {
        "used_project_intake": bool(value.get("used_project_intake")),
        "referenced_files": sorted(set(referenced_files))[:40],
        "notes": str(value.get("notes", ""))[:2000],
    }


def normalize_rule_operator_hypotheses(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    hypotheses: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("name", f"hypothesis_{index:02d}")).strip()
        name = safe_profile_name(raw_name) or f"hypothesis_{index:02d}"
        if name in seen_names:
            name = f"{name}_{index:02d}"
        seen_names.add(name)
        hypothesis_type = str(item.get("type", "")).strip()
        if hypothesis_type not in RULE_OPERATOR_TYPES:
            hypothesis_type = "dispatch_rule"
        target_files = []
        for target in item.get("target_files", []):
            if isinstance(target, str) and target.strip():
                target_files.append(normalize_relative_path(target))
        evidence_used = []
        for evidence in item.get("evidence_used", []):
            if isinstance(evidence, str) and evidence.strip():
                evidence_used.append(evidence.strip()[:240])
        hypotheses.append(
            {
                "name": name[:80],
                "type": hypothesis_type,
                "novelty": str(item.get("novelty", ""))[:1000],
                "expected_effect": str(item.get("expected_effect", ""))[:1000],
                "evidence_used": sorted(set(evidence_used))[:12],
                "target_files": sorted(set(path for path in target_files if path))[:20],
                "ablation_plan": str(item.get("ablation_plan", ""))[:1000],
            }
        )
        if len(hypotheses) >= 6:
            break
    return hypotheses


def build_proposal_audit(proposal: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    project_intake = context.get("project_intake") or {}
    intake_summary = project_intake.get("summary") or {}
    accepted_paths = [normalize_relative_path(str(item.get("path", ""))) for item in proposal.get("changes", [])]
    accepted_paths = [item for item in accepted_paths if item]
    rejected_paths = [normalize_relative_path(str(item.get("path", ""))) for item in proposal.get("rejected_changes", [])]
    rejected_paths = [item for item in rejected_paths if item]

    intake_sets = {
        "entry_files": normalized_path_set(intake_summary.get("entry_files") or []),
        "core_algorithm_files": normalized_path_set(intake_summary.get("core_algorithm_files") or []),
        "benchmark_files": normalized_path_set(intake_summary.get("benchmark_files") or []),
        "validator_files": normalized_path_set(intake_summary.get("validator_files") or []),
        "dependency_files": normalized_path_set(intake_summary.get("dependency_files") or []),
    }
    proposal_text = proposal_search_text(proposal)
    referenced_paths = sorted(
        path
        for path in all_intake_paths(intake_sets)
        if path and (path.lower() in proposal_text or any(_path_is_under(changed, path) for changed in accepted_paths))
    )
    declared_usage = proposal.get("context_usage") or {}
    declared_references = normalized_path_set(declared_usage.get("referenced_files") or [])
    changed_core = sorted(path for path in accepted_paths if path_matches_any(path, intake_sets["core_algorithm_files"]))
    changed_validators = sorted(path for path in accepted_paths if path_matches_any(path, intake_sets["validator_files"]))
    changed_benchmarks = sorted(path for path in accepted_paths if path_matches_any(path, intake_sets["benchmark_files"]))
    touched_intake_paths = sorted(path for path in accepted_paths if path_matches_any(path, all_intake_paths(intake_sets)))
    risk_codes = [str(item.get("code")) for item in intake_summary.get("risk_flags") or [] if item.get("code")]
    test_commands = [
        str(item.get("command"))
        for item in intake_summary.get("test_commands") or []
        if item.get("command")
    ]
    quick_test_plan = str(proposal.get("quick_test_plan", ""))
    referenced_test_commands = [command for command in test_commands if command and command in quick_test_plan]
    hypotheses = proposal.get("rule_operator_hypotheses") or []
    if not isinstance(hypotheses, list):
        hypotheses = []
    hypothesis_target_files = sorted(
        {
            normalize_relative_path(str(target))
            for item in hypotheses
            if isinstance(item, dict)
            for target in item.get("target_files", [])
            if isinstance(target, str) and target.strip()
        }
    )
    hypothesis_types = sorted(
        {
            str(item.get("type"))
            for item in hypotheses
            if isinstance(item, dict) and item.get("type")
        }
    )

    warnings = []
    if project_intake and not (declared_usage.get("used_project_intake") or referenced_paths or touched_intake_paths):
        warnings.append("project_intake_present_but_not_referenced")
    if accepted_paths and not hypotheses:
        warnings.append("missing_rule_operator_hypotheses")
    if changed_validators:
        warnings.append("proposal_touches_validator_candidates")
    if changed_benchmarks:
        warnings.append("proposal_touches_benchmark_candidates")
    if accepted_paths and not referenced_test_commands:
        warnings.append("quick_test_plan_does_not_reference_intake_test_command")

    return {
        "project_intake_present": bool(project_intake),
        "project_intake_status": project_intake.get("status"),
        "declared_project_intake_used": bool(declared_usage.get("used_project_intake")),
        "declared_referenced_files": sorted(declared_references),
        "detected_referenced_intake_files": referenced_paths[:80],
        "accepted_change_count": len(accepted_paths),
        "rejected_change_count": len(rejected_paths),
        "accepted_change_paths": accepted_paths,
        "changed_core_algorithm_files": changed_core,
        "changed_validator_files": changed_validators,
        "changed_benchmark_files": changed_benchmarks,
        "changed_files_seen_in_intake": touched_intake_paths,
        "referenced_test_commands": referenced_test_commands,
        "project_intake_risk_codes": risk_codes,
        "operator_lineage": {
            "hypothesis_count": len(hypotheses),
            "hypothesis_types": hypothesis_types,
            "hypothesis_target_files": hypothesis_target_files[:40],
            "target_files_overlap_changes": sorted(
                path for path in accepted_paths if path_matches_any(path, set(hypothesis_target_files))
            ),
        },
        "warnings": warnings,
    }


def normalized_path_set(values: list[Any]) -> set[str]:
    result: set[str] = set()
    for value in values:
        if isinstance(value, str):
            normalized = normalize_relative_path(value)
            if normalized:
                result.add(normalized)
    return result


def all_intake_paths(intake_sets: dict[str, set[str]]) -> set[str]:
    result: set[str] = set()
    for paths in intake_sets.values():
        result.update(paths)
    return result


def proposal_search_text(proposal: dict[str, Any]) -> str:
    parts = [
        str(proposal.get("summary", "")),
        str(proposal.get("strategy_intent", "")),
        str(proposal.get("quick_test_plan", "")),
        str((proposal.get("context_usage") or {}).get("notes", "")),
    ]
    for item in proposal.get("rule_operator_hypotheses", []):
        if not isinstance(item, dict):
            continue
        parts.extend(
            [
                str(item.get("name", "")),
                str(item.get("type", "")),
                str(item.get("novelty", "")),
                str(item.get("expected_effect", "")),
                str(item.get("ablation_plan", "")),
                " ".join(str(value) for value in item.get("target_files", []) if isinstance(value, str)),
                " ".join(str(value) for value in item.get("evidence_used", []) if isinstance(value, str)),
            ]
        )
    for item in proposal.get("changes", []):
        parts.append(str(item.get("path", "")))
        parts.append(str(item.get("rationale", "")))
    for path in (proposal.get("context_usage") or {}).get("referenced_files") or []:
        parts.append(str(path))
    return "\n".join(parts).replace("\\", "/").lower()


def path_matches_any(path: str, roots: set[str]) -> bool:
    return any(_path_is_under(path, root) for root in roots if root)


def safe_profile_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe[:64] or "local_search_profile"


def normalize_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip().lstrip("/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    return "/".join(parts)


def is_path_allowed(path_value: str, context: dict[str, Any]) -> tuple[bool, str]:
    normalized = normalize_relative_path(path_value)
    if not normalized:
        return False, "empty path"
    candidate = Path(normalized)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        return False, "absolute paths and parent traversal are not allowed"
    edit_policy = context.get("edit_policy", {})
    allowed_paths = [normalize_relative_path(str(item)) for item in edit_policy.get("allowed_paths", [])]
    forbidden_paths = [normalize_relative_path(str(item)) for item in edit_policy.get("forbidden_paths", [])]
    forbidden_paths.extend([".git", "outputs"])
    if any(_path_is_under(normalized, forbidden) for forbidden in forbidden_paths if forbidden):
        return False, "path is under a forbidden directory"
    if not allowed_paths or "." in allowed_paths:
        return True, ""
    if any(_path_is_under(normalized, allowed) for allowed in allowed_paths if allowed):
        return True, ""
    return False, "path is outside allowed paths"


def apply_code_edit_proposal(
    *,
    proposal: dict[str, Any],
    worktree_path: Path,
    context: dict[str, Any],
) -> list[str]:
    worktree_root = worktree_path.resolve()
    changed_files: list[str] = []
    for change in proposal.get("changes", []):
        path_value = str(change.get("path", ""))
        allowed, reason = is_path_allowed(path_value, context)
        if not allowed:
            raise ValueError(f"refusing to apply rejected path {path_value!r}: {reason}")
        relative_path = normalize_relative_path(path_value)
        target = (worktree_root / relative_path).resolve()
        if not _resolved_is_under(target, worktree_root):
            raise ValueError(f"refusing to write outside worktree: {relative_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(change.get("content", "")), encoding="utf-8")
        changed_files.append(relative_path)
    return changed_files


def render_code_edit_markdown(proposal: dict[str, Any]) -> str:
    lines = [
        "# Coding Worker Proposal",
        "",
        "## Summary",
        "",
        proposal.get("summary", "") or "No summary provided.",
        "",
        "## Strategy Intent",
        "",
        proposal.get("strategy_intent", "") or "No strategy intent provided.",
        "",
        "## Changes",
        "",
    ]
    changes = proposal.get("changes", [])
    if not changes:
        lines.append("No accepted changes were proposed.")
    for change in changes:
        lines.extend(
            [
                f"### `{change.get('path')}`",
                "",
                f"- action: `{change.get('action')}`",
                f"- rationale: {change.get('rationale', '')}",
                "",
            ]
        )
    hypotheses = proposal.get("rule_operator_hypotheses") or []
    lines.extend(["", "## Rule / Operator Hypotheses", ""])
    if not hypotheses:
        lines.append("No rule/operator hypotheses were provided.")
    for hypothesis in hypotheses:
        lines.extend(
            [
                f"### {hypothesis.get('name')}",
                "",
                f"- type: `{hypothesis.get('type')}`",
                f"- novelty: {hypothesis.get('novelty') or 'N/A'}",
                f"- expected_effect: {hypothesis.get('expected_effect') or 'N/A'}",
                f"- evidence_used: `{json.dumps(hypothesis.get('evidence_used') or [], ensure_ascii=False)}`",
                f"- target_files: `{json.dumps(hypothesis.get('target_files') or [], ensure_ascii=False)}`",
                f"- ablation_plan: {hypothesis.get('ablation_plan') or 'N/A'}",
                "",
            ]
        )
    context_usage = proposal.get("context_usage") or {}
    lines.extend(
        [
            "",
            "## Context Usage",
            "",
            f"- used_project_intake: `{context_usage.get('used_project_intake')}`",
            f"- referenced_files: `{json.dumps(context_usage.get('referenced_files') or [], ensure_ascii=False)}`",
            f"- notes: {context_usage.get('notes') or 'N/A'}",
        ]
    )
    audit = proposal.get("proposal_audit") or {}
    if audit:
        lines.extend(
            [
                "",
                "## Proposal Audit",
                "",
                f"- project_intake_present: `{audit.get('project_intake_present')}`",
                f"- project_intake_status: `{audit.get('project_intake_status')}`",
                f"- declared_project_intake_used: `{audit.get('declared_project_intake_used')}`",
                f"- detected_referenced_intake_files: `{json.dumps(audit.get('detected_referenced_intake_files') or [], ensure_ascii=False)}`",
                f"- changed_core_algorithm_files: `{json.dumps(audit.get('changed_core_algorithm_files') or [], ensure_ascii=False)}`",
                f"- changed_validator_files: `{json.dumps(audit.get('changed_validator_files') or [], ensure_ascii=False)}`",
                f"- referenced_test_commands: `{json.dumps(audit.get('referenced_test_commands') or [], ensure_ascii=False)}`",
                f"- warnings: `{json.dumps(audit.get('warnings') or [], ensure_ascii=False)}`",
            ]
        )
    rejected = proposal.get("rejected_changes", [])
    if rejected:
        lines.extend(["", "## Rejected Changes", ""])
        for item in rejected:
            lines.append(f"- `{item.get('path')}`: {item.get('reason')}")
    risk_notes = proposal.get("risk_notes", [])
    if risk_notes:
        lines.extend(["", "## Risk Notes", ""])
        for note in risk_notes:
            lines.append(f"- {note}")
    lines.extend(["", "## Quick Test Plan", "", proposal.get("quick_test_plan", "") or "No quick test plan provided."])
    return "\n".join(lines).strip() + "\n"


def _path_is_under(path_value: str, root_value: str) -> bool:
    path = normalize_relative_path(path_value)
    root = normalize_relative_path(root_value)
    return path == root or path.startswith(root.rstrip("/") + "/")


def _resolved_is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def render_strategy_markdown(profile: dict[str, Any], source: str) -> str:
    lines = [f"# Strategy Profile ({source})", "", profile.get("rationale", ""), "", "## Strategies", ""]
    for strategy in profile.get("strategies", []):
        lines.append(f"### {strategy['name']}")
        lines.append("")
        lines.append(f"- noise: `{strategy.get('noise', 0.0)}`")
        lines.append(f"- weights: `{json.dumps(strategy.get('weights', {}), ensure_ascii=False)}`")
        lines.append("")
    local_profiles = profile.get("local_search_profiles", [])
    if local_profiles:
        lines.extend(["## Local Search Profiles", ""])
        for local_profile in local_profiles:
            lines.append(f"### {local_profile['name']}")
            lines.append("")
            lines.append(f"- neighborhood: `{local_profile.get('neighborhood_profile')}`")
            lines.append(f"- portfolio_size: `{local_profile.get('portfolio_size')}`")
            lines.append(f"- restarts: `{local_profile.get('restarts')}`")
            lines.append(f"- initial_pool_size: `{local_profile.get('initial_pool_size', 1)}`")
            lines.append(f"- iterations: `{local_profile.get('iterations')}`")
            lines.append(f"- neighbor_limit: `{local_profile.get('neighbor_limit')}`")
            lines.append(f"- time_limit_sec: `{local_profile.get('time_limit_sec')}`")
            lines.append(f"- rationale: {local_profile.get('rationale', '')}")
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
        "local_search_profiles": [
            {
                "name": f"template_combined_balanced_{round_index}",
                "neighborhood_profile": "combined",
                "portfolio_size": 192,
                "restarts": 2,
                "initial_pool_size": 1,
                "iterations": 100,
                "neighbor_limit": 220,
                "time_limit_sec": 4.0,
                "rationale": "Stable default that protects the current strongest combined neighborhood.",
            },
            {
                "name": f"template_combined_elite_initials_{round_index}",
                "neighborhood_profile": "combined",
                "portfolio_size": 224,
                "restarts": 2,
                "initial_pool_size": 2,
                "iterations": 100,
                "neighbor_limit": 240,
                "time_limit_sec": 5.0,
                "rationale": "Tests whether multiple elite constructive starts improve the combined neighborhood.",
            },
            {
                "name": f"template_hybrid_probe_{round_index}",
                "neighborhood_profile": "hybrid",
                "portfolio_size": 256,
                "restarts": 3,
                "initial_pool_size": 2,
                "iterations": 160,
                "neighbor_limit": 300,
                "time_limit_sec": 6.0,
                "rationale": "Evaluator-gated probe for HGTSA-style N8/k-insertion moves without replacing combined.",
            },
            {
                "name": f"template_awls_probe_{round_index}",
                "neighborhood_profile": "awls-hybrid",
                "portfolio_size": 224,
                "restarts": 2,
                "initial_pool_size": 2,
                "iterations": 140,
                "neighbor_limit": 260,
                "time_limit_sec": 5.0,
                "rationale": "AWLS-biased candidate mix that prioritizes RK/LK k-insertion while preserving fallback coverage.",
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


def generate_reflection_auto(
    *,
    docs: str,
    report: str,
    hypothesis: dict[str, Any],
    local_reflection: str,
    output_dir: Path,
    round_index: int,
    mode: str,
    model: str,
) -> tuple[str, str]:
    """Generate evaluator-grounded reflection for the next strategy round.

    The profile generator proposes dispatch and local-search hypotheses.  This
    reflection generator is the complementary agent step: it reads the fixed
    evaluator output and writes the natural-language diagnosis that conditions
    the next round.  DeepSeek mode is intentionally strict so failed API access
    cannot masquerade as model-driven reasoning.
    """

    if mode not in {"auto", "deepseek", "template"}:
        raise ValueError(f"unknown profile generation mode: {mode}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if mode in {"auto", "deepseek"}:
        try:
            worker = DeepSeekWorker(model=model)
            reflection = worker.generate_reflection(
                docs=docs,
                report=report,
                hypothesis=hypothesis,
                output_dir=output_dir,
                round_index=round_index,
            )
            return reflection, "deepseek"
        except DeepSeekUnavailable:
            if mode == "deepseek":
                raise
        except Exception as exc:  # noqa: BLE001 - auto mode may continue with local reflection.
            (output_dir / "deepseek_reflection_error.txt").write_text(str(exc), encoding="utf-8")
            if mode == "deepseek":
                raise
    return local_reflection, "local"
