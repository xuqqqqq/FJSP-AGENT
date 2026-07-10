from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

from ..deepseek_client import DeepSeekClient, DeepSeekUnavailable, is_deepseek_configured
from ..solver_quality_contract import build_agent_generated_solver_quality_contract
from ..slot_contract import extract_marked_block, replace_marked_block, validate_slot_manifest_gate
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

LOCAL_SEARCH_NEIGHBORHOODS = ["random", "critical-block", "combined", "hgtsa-lite", "hybrid", "awls-hybrid", "setup-guided"]
RULE_OPERATOR_TYPES = [
    "dispatch_rule",
    "local_search_operator",
    "path_selection",
    "repair_rule",
    "parameter_policy",
]

PRIORITY_CONTEXT_DEFAULT_MAX_CHARS = 48000
PRIORITY_CONTEXT_MIN_CHARS = 12000
PRIORITY_CONTEXT_MAX_CHARS = 60000
PRIORITY_INCUMBENT_FILE_MAX_CHARS = 16000
PRIORITY_KNOWLEDGE_CARD_LIMIT = 3
PRIORITY_KNOWLEDGE_CARD_MAX_CHARS = 3600


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    decoder = json.JSONDecoder()
    try:
        parsed, _ = decoder.raw_decode(stripped)
        if isinstance(parsed, dict):
            return parsed
        raise json.JSONDecodeError("top-level JSON value must be an object", stripped, 0)
    except json.JSONDecodeError:
        for match in re.finditer(r"\{", stripped):
            try:
                parsed, _ = decoder.raw_decode(stripped[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
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
            max_tokens=12000,
            json_mode=True,
        )
        raw_path = output_dir / "deepseek_code_edit_raw.json"
        raw_path.write_text(content, encoding="utf-8")
        try:
            raw_proposal = extract_json_object(content)
        except json.JSONDecodeError as exc:
            repaired = self._repair_code_edit_json(client, content, str(exc), max_tokens=12000)
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
            proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
            markdown_path.write_text(render_code_edit_markdown(proposal), encoding="utf-8")
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
        priority_context = priority_worker_context(context)
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
  "solver_contract_self_check": {{
    "active_features": ["alternative_machines", "operation_precedence", "machine_capacity"],
    "capabilities": [
      {{
        "name": "stable_operation_identity",
        "status": "implemented",
        "evidence": "op_info uses (job_id, op_id) keys; schedule output preserves job_id/op_id"
      }}
    ],
    "representation": "which operation identity, assignment, and machine sequence structures the code uses",
    "decoder": "which function rebuilds a complete schedule and how it rejects infeasible candidates",
    "variant_handling": ["sequence_dependent_setup is applied on same-machine arcs inside decode_schedule"],
    "runtime_bounds": "where restarts/iterations/windows/deadlines are capped",
    "incumbent_preservation": "how failed candidates keep the incumbent schedule",
    "remaining_gaps": []
  }},
  "changes": [
    {{
      "path": "relative/path.py",
      "action": "replace_slot_block",
      "slot_id": "local_search_neighborhood_actions",
      "content": "replacement code between marker_start and marker_end only",
      "rationale": "why this code-slot replacement helps"
    }},
    {{
      "path": "relative/path.py",
      "action": "create_or_replace",
      "content": "full file content",
      "rationale": "why this change helps"
    }},
    {{
      "path": "relative/path.py",
      "action": "text_replace",
      "old": "exact existing text snippet",
      "new": "replacement text snippet",
      "rationale": "why this local patch helps"
    }},
    {{
      "path": "relative/path.py",
      "action": "insert_before",
      "anchor": "exact existing anchor text",
      "content": "text inserted immediately before anchor",
      "rationale": "why this insertion helps"
    }},
    {{
      "path": "relative/path.py",
      "action": "insert_after",
      "anchor": "exact existing anchor text",
      "content": "text inserted immediately after anchor",
      "rationale": "why this insertion helps"
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
- Prefer `text_replace` or `insert_after` for existing solver files. Use
  `create_or_replace` only for a new small helper file or when the full file
  content is short and complete. Do not rewrite a large solver file just to add
  a small operator.
- Use `insert_before` instead of `insert_after` when adding a new top-level
  helper before `def main(...)` or another function definition. Never insert a
  top-level helper immediately after a `def ...:` line, because that leaves the
  original function with no body and causes syntax errors.
- For helper code longer than about 40 lines, prefer a new small helper file
  plus a compact import/call patch over one huge inline JSON string. This keeps
  proposals parseable and reduces indentation mistakes.
- When iteration_edit_contract.mode is `incremental_after_baseline`, do not use
  `create_or_replace` on an existing solver entrypoint. Preserve the promoted
  incumbent skeleton and use a small `text_replace`, `insert_before`,
  `insert_after`, or confirmed `replace_slot_block` mutation. Baseline-generation is the only phase where
  creating the initial solver entrypoint is expected. Exception: if Priority
  context says the incumbent requires legality repair, you may use
  `create_or_replace` on the solver entrypoint to restore a runnable complete
  solver; do not rely on fragile `text_replace` anchors when the entrypoint is
  already invalid.
- If slot_manifest is present and the edit is inside a user-confirmed slot,
  prefer `replace_slot_block` with the slot_id.  Do not echo the whole
  original_content in an `old` field; Core will locate marker_start/marker_end
  and replace only the code between them.
- When changing a confirmed slot, return exactly one `replace_slot_block`
  action for that slot instead of `text_replace`.
- For `replace_slot_block`, `content` must contain only the replacement code
  inside the slot markers.  Do not include marker_start/marker_end lines, do
  not include the whole file, and keep the surrounding function IO contract.
- Keep `replace_slot_block.content` compact: target 20 to 80 lines of slot code.
  Prefer a surgical local addition or ranking change inside the existing
  original_content over rewriting the whole slot.  Avoid long comments and
  decorative section banners.
- If only one slot has user_confirmed=true, its exact target file is the slot's
  target_file from slot_manifest; do not invent a new solver filename.
- Preserve existing parser, validator, evaluator, and benchmark semantics unless
  the task contract explicitly asks to implement those surfaces.  For standard
  FJSP runs, prefer importing the existing parser/evaluator helpers instead of
  reimplementing machine-index or duration parsing.
- For agent-generated FJSP/FJSP-variant solvers, derive active features from
  the requirement document, IO contract, evaluator protocol, and
  instance_diagnostics before choosing an algorithm.  Do not assume the variant
  is SDST; only implement setup, no-wait, lag, calendar, batching, transport,
  release-date, due-date, or multi-objective logic when those features are
  present in the active context.
- If Priority context contains `agent_generated_solver_quality_contract.enabled
  = true`, the code must satisfy its required_code_capabilities before
  optimizing makespan: standalone --input/--output/--seed CLI, active IO
  parser, declared JSON schedule schema, stable operation identity,
  operation-level ready-list construction, complete schedule coverage, machine
  eligibility, processing duration equality, precedence, machine non-overlap,
  bounded runtime, and incumbent preservation when a candidate cannot be fully
  decoded.
- Active IO parser means the solver loops over the parsed jobs, operations,
  candidate machines, processing times, and active variant data from the input
  file.  A parser that calls read_text/split/json.load and then hardcodes
  `op_info = {{(0, 0): ...}}`, a fixed `machine_sequences`, or a fixed schedule
  is not an active parser and will be rejected.
- When agent_generated_solver_quality_contract.enabled is true and you edit an
  agent-generated solver, fill `solver_contract_self_check` before the changes:
  list the active_features you detected from IO/requirements/diagnostics, mark
  each required and variant_required capability as implemented/missing/not_applicable,
  and cite concrete function names, variables, or guards as evidence. Evidence
  must name symbols that appear verbatim in the proposed code, such as
  `parse_instance`, `op_info`, `decode_schedule`, `expected_ops`, `deadline`, or
  `best_schedule`. Do not mark a capability implemented unless the proposed code
  contains the cited evidence.
- If evaluator_protocol.solver_command_template runs an agent-generated solver
  under `examples/agent_generated*.py`, treat generated solver/helper files as
  standalone example scripts. Do not import `harness_agent.*` from those files;
  keep small setup/decoder utilities self-contained or reuse helpers already
  present in the incumbent generated solver. Core will reject backend-package
  imports in that runtime.
- During agent-generated baseline creation, first build a runnable standalone
  solver from the IO contract: parser, one stable operation-key representation
  (prefer `(job_id, op_id)`), operation-level ready-list constructor,
  assignment/machine sequence or equivalent schedule representation, complete
  decode/build path, JSON output schema, and self-checks for every active
  constraint.  A fixed job-by-job greedy that does not compare ready operations
  and eligible machines is not a sufficient generated baseline.
- During agent-generated improvement rounds, preserve the promoted incumbent
  parser and valid skeleton.  If the incumbent lacks a required contract
  capability, repair that missing capability first; otherwise mutate exactly
  one bounded rule/operator around the incumbent instead of writing a new
  unrelated solver.
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
- If Priority context contains loop_feedback.current_round_repair, this is an
  in-round repair attempt after Core rejected the previous proposal. First fix
  the listed JA/evaluator issues; do not repeat rejected anchors, unsupported
  actions, protected-fact regressions, no-op proposals, or syntax errors.
- Read Priority context before the full Context packet.  If
  priority_knowledge_cards are present, cite `knowledge_cards` in
  evidence_used when they shape the proposal, and either follow any
  preserve/recover/avoid guidance or explain in risk_notes why loop_feedback
  overrides it.
- If loop_feedback or previous_pipeline_memory reports rolled-back or duplicate
  proposals, novelty must explain what is materially different this time.
- If contract_review_evidence.role_prioritized_sections is present, cite it in
  evidence_used when the rule/operator comes from objectives, constraints, IO,
  acceptance, or algorithm-guidance sections.
- For local_search_operator, neighborhood, decoder, destroy-repair, or
  post-processing proposals, preserve feasibility before objective comparison:
  every decoded candidate must cover the full required operation/job set before
  makespan or score is computed; deadlocked/partial/empty schedules must be
  skipped or treated as infeasible, never as makespan 0; only replace the
  incumbent schedule after verifying identical operation coverage.
- If active features include sequence-dependent setup, setup time is a machine
  sequencing effect: candidate start times must include setup between adjacent
  operations on the same machine, and any sequence/neighborhood move must be
  full-decoded under setup before it can replace the incumbent.  If the active
  context does not include setup, do not add SDST-specific assumptions.
- If the task contract requires human confirmation, say so in risk_notes and
  avoid claiming formal success.
- Do not include Markdown fences or commentary outside JSON.
- Do not include placeholders like TODO-only implementations unless the context
  explicitly requests scaffolding.
- If no safe edit is possible, return an empty "changes" list with an explicit
  risk note.

Priority context for this round:
{priority_context}

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
                        "Use exactly these top-level keys: summary, strategy_intent, rule_operator_hypotheses, solver_contract_self_check, changes, context_usage, quick_test_plan, risk_notes. "
                        "Each change must use one supported action: "
                        "replace_slot_block(path, slot_id, content), create_or_replace(path, content), text_replace(path, old, new), "
                        "insert_before(path, anchor, content), or insert_after(path, anchor, content). Return JSON only.\n\n"
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
            action = str(item.get("action", "create_or_replace")).strip()
            if action == "replace_slot_block":
                slot_id = str(item.get("slot_id", "")).strip()
                slot, slot_error = confirmed_context_slot(context, slot_id)
                if slot is None:
                    rejected_changes.append({"path": str(item.get("path", "")).strip(), "reason": slot_error})
                    continue
                path = str(slot.get("target_file") or item.get("path") or "").strip()
            else:
                path = str(item.get("path", "")).strip()
            allowed, reason = is_path_allowed(path, context)
            if not allowed:
                rejected_changes.append({"path": path, "reason": reason})
                continue
            normalized_path = normalize_relative_path(path)
            if action == "replace_slot_block":
                slot_id = str(item.get("slot_id", "")).strip()
                slot, slot_error = confirmed_context_slot(context, slot_id)
                if slot is None:
                    rejected_changes.append({"path": path, "reason": slot_error})
                    continue
                slot_path = normalize_relative_path(str(slot.get("target_file", "")))
                normalized_path = slot_path
                content = item.get("content", item.get("replacement"))
                if not isinstance(content, str) or not content.strip():
                    rejected_changes.append({"path": path, "reason": "replace_slot_block requires non-empty string content"})
                    continue
                normalized_changes.append(
                    {
                        "path": normalized_path,
                        "action": "replace_slot_block",
                        "slot_id": slot_id,
                        "content": normalize_slot_replacement_content(content, slot),
                        "rationale": str(item.get("rationale", ""))[:2000],
                    }
                )
            elif action == "create_or_replace":
                content = item.get("content")
                if not isinstance(content, str):
                    rejected_changes.append({"path": path, "reason": "create_or_replace requires string content"})
                    continue
                if create_or_replace_forbidden(normalized_path, context):
                    rejected_changes.append(
                        {
                            "path": normalized_path,
                            "reason": (
                                "iteration_edit_contract forbids create_or_replace on an existing solver file; "
                                "preserve the incumbent and use text_replace/insert_before/insert_after/replace_slot_block"
                            ),
                        }
                    )
                    continue
                normalized_changes.append(
                    {
                        "path": normalized_path,
                        "action": "create_or_replace",
                        "content": content,
                        "rationale": str(item.get("rationale", ""))[:2000],
                    }
                )
            elif action == "text_replace":
                old = item.get("old")
                new = item.get("new")
                if not isinstance(old, str) or not old:
                    rejected_changes.append({"path": path, "reason": "text_replace requires non-empty old text"})
                    continue
                if not isinstance(new, str):
                    rejected_changes.append({"path": path, "reason": "text_replace requires string new text"})
                    continue
                normalized_changes.append(
                    {
                        "path": normalized_path,
                        "action": "text_replace",
                        "old": old,
                        "new": new,
                        "rationale": str(item.get("rationale", ""))[:2000],
                    }
                )
            elif action in {"insert_before", "insert_after"}:
                anchor = item.get("anchor")
                content = item.get("content")
                if not isinstance(anchor, str) or not anchor:
                    rejected_changes.append({"path": path, "reason": f"{action} requires non-empty anchor text"})
                    continue
                if not isinstance(content, str) or not content:
                    rejected_changes.append({"path": path, "reason": f"{action} requires non-empty content"})
                    continue
                if action == "insert_after" and inserts_top_level_code_after_definition(anchor, content):
                    rejected_changes.append(
                        {
                            "path": path,
                            "reason": (
                                "insert_after a def/class line with top-level helper code is unsafe; "
                                "use insert_before on the next top-level definition instead"
                            ),
                        }
                    )
                    continue
                normalized_changes.append(
                    {
                        "path": normalized_path,
                        "action": action,
                        "anchor": anchor,
                        "content": content,
                        "rationale": str(item.get("rationale", ""))[:2000],
                    }
                )
            else:
                rejected_changes.append({"path": path, "reason": f"unsupported action: {action}"})
                continue
        risk_notes_raw = proposal.get("risk_notes", [])
        if isinstance(risk_notes_raw, str):
            risk_notes = [risk_notes_raw[:1000]]
        elif isinstance(risk_notes_raw, list):
            risk_notes = [str(item)[:1000] for item in risk_notes_raw if isinstance(item, str)]
        else:
            risk_notes = []
        normalized = {
            "summary": str(proposal.get("summary", ""))[:4000],
            "strategy_intent": str(proposal.get("strategy_intent", ""))[:4000],
            "rule_operator_hypotheses": normalize_rule_operator_hypotheses(
                proposal.get("rule_operator_hypotheses")
            ),
            "solver_contract_self_check": normalize_solver_contract_self_check(
                proposal.get("solver_contract_self_check"),
                context,
            ),
            "changes": normalized_changes,
            "rejected_changes": rejected_changes,
            "context_usage": normalize_context_usage(proposal.get("context_usage")),
            "quick_test_plan": str(proposal.get("quick_test_plan", ""))[:2000],
            "risk_notes": risk_notes,
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


def priority_worker_context(context: dict[str, Any]) -> str:
    quality_contract = build_agent_generated_solver_quality_contract(context)
    payload = {
        "round_type": "improvement_round" if context.get("iteration_edit_contract") else "baseline_or_single_round",
        "iteration_edit_contract": context.get("iteration_edit_contract") or {},
        "agent_generated_solver_quality_contract": quality_contract,
        "incumbent_requires_legality_repair": incumbent_requires_legality_repair(context),
        "legality_repair_rule": (
            "If incumbent_requires_legality_repair is true, the current incumbent is not a valid solver. "
            "A full create_or_replace of the solver entrypoint is allowed for legality repair; prefer that over "
            "fragile text_replace anchors when replacing an invalid generated solver."
        ),
        "incumbent_code_context": compact_incumbent_code_context(context.get("incumbent_code_context") or {}),
        "loop_feedback": compact_loop_feedback_for_prompt(context.get("loop_feedback") or {}),
        "round_learning_contract": {
            "must_do": [
                "Preserve the current promoted incumbent and make one accepted incremental edit.",
                "Treat the current outer round as one improvement direction. Repair or refine the same direction before switching ideas.",
                "Use failure_memory.must_avoid as hard negative memory.",
                "Do not submit a no-op proposal during improvement rounds.",
                "Do not repeat a legal-but-not-better tie-break tweak; change the neighborhood, decoder, or insertion/regret mechanism materially.",
            ],
            "quality_target": (
                "The next proposal should be both legal and attributable: one explicit rule/operator hypothesis, "
                "one bounded code mutation, fixed parser/evaluator semantics, and Core evaluator evidence only."
            ),
        },
        "candidate_feasibility_guard": {
            "rule": (
                "For local search, neighborhood, decoder, destroy-repair, or post-processing changes, "
                "never score or accept empty/partial candidate schedules. Verify full operation coverage "
                "before computing makespan and before replacing the incumbent schedule."
            ),
            "forbidden_patterns": [
                "max(... for op in candidate_schedule) if candidate_schedule else 0",
                "decoder deadlock breaks and returns a partial schedule as if feasible",
            ],
            "representation_rule": (
                "When adding a decoder or machine-sequence local search, preserve one operation representation "
                "end to end. Do not mix global operation ids, (job, op) pairs, and full schedule dictionaries in "
                "the same machine_sequences/op_info path. If a trial decoder sees an unexpected item shape or "
                "cannot decode all operations, reject that neighbor and keep the incumbent schedule."
            ),
        },
        "variant_feature_rule": (
            "Use agent_generated_solver_quality_contract.active_features to decide which constraints must appear "
            "in generated code. Standard FJSP needs coverage, eligibility, precedence, non-overlap, objective, "
            "and bounded runtime guards. Add setup/no-wait/lag/calendar/batching/transport/release/due-date "
            "logic only when the active context identifies those features."
        ),
        "active_io_parser_rule": (
            "The active_io_parser capability requires deriving every job/operation/candidate machine and duration "
            "from the active input file. Do not satisfy parser checks by reading the file and then hardcoding "
            "op_info, assignment, machine_sequences, or a fixed one-operation schedule."
        ),
        "constructive_baseline_rule": (
            "For an agent-generated baseline, use an operation-level ready list: keep one next operation per "
            "unfinished job, evaluate eligible machines for each ready operation using job_ready/machine_ready "
            "and active variant timing, then commit one operation with a seeded tie-break or restart policy. "
            "Do not submit a fixed job-by-job sweep as the initial solver."
        ),
        "solver_quality_playbook_rule": (
            "For each item in agent_generated_solver_quality_contract.capability_playbook, either implement the "
            "capability and cite concrete code evidence in solver_contract_self_check.capabilities, or mark it "
            "missing with a repair note. Evidence must name function/variable/guard symbols that appear verbatim "
            "in the submitted code; do not claim a capability is implemented from strategy text or imaginary "
            "helper names alone."
        ),
        "candidate_runtime_import_rule": (
            "When the solver command is an agent-generated examples/agent_generated*.py entrypoint, "
            "the entrypoint and helper modules under examples must run as standalone example scripts. "
            "Do not add `from harness_agent...` or `import harness_agent...` in those files; the Core JA "
            "gate rejects such imports before evaluator execution."
        ),
        "worker_instruction": {
            "round_feedback_rule": (context.get("worker_instruction") or {}).get("round_feedback_rule"),
            "incremental_edit_rule": (context.get("worker_instruction") or {}).get("incremental_edit_rule"),
        },
        "priority_knowledge_cards": compact_priority_knowledge_cards(
            context,
            limit=PRIORITY_KNOWLEDGE_CARD_LIMIT,
            max_chars_per_card=PRIORITY_KNOWLEDGE_CARD_MAX_CHARS,
        ),
        "knowledge_use_rule": (
            "Use priority_knowledge_cards after reading the incumbent_code_context and loop_feedback. "
            "The current code and failed anchors are authoritative for patch shape; RAG cards only guide "
            "the rule/operator hypothesis. If cards contain preserve/recover/avoid guidance, either follow "
            "it or explain why loop_feedback overrides it. If local evidence cites a reusable method pattern "
            "or failure mode, explain whether the proposal preserves, recovers, avoids, or intentionally "
            "ablates that mechanism. Do not treat prior instance scores as solver inputs. Cite "
            "knowledge_cards in evidence_used when it shapes the proposal."
        ),
        "experience_quality_memory_rule": (
            "If loop_feedback.experience_memory.agent_generated_quality_memory reports recurring quality or "
            "self-check risks, address those structural gaps first in the proposal summary, rule/operator "
            "hypothesis, code evidence, and solver_contract_self_check. Do not spend a new heuristic idea "
            "while the generated solver still lacks parser, representation, constructor, decoder, or active "
            "variant evidence from the prior memory."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)[:priority_context_max_chars()]


def priority_context_max_chars() -> int:
    raw_value = os.getenv("ALGOFORGE_PRIORITY_CONTEXT_MAX_CHARS")
    if raw_value is None:
        return PRIORITY_CONTEXT_DEFAULT_MAX_CHARS
    try:
        requested = int(raw_value)
    except ValueError:
        return PRIORITY_CONTEXT_DEFAULT_MAX_CHARS
    return max(PRIORITY_CONTEXT_MIN_CHARS, min(PRIORITY_CONTEXT_MAX_CHARS, requested))


def compact_loop_feedback_for_prompt(loop_feedback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(loop_feedback, dict):
        return {}
    baseline_summary = loop_feedback.get("baseline_summary")
    if not isinstance(baseline_summary, dict):
        baseline_summary = {}
    previous_rounds = loop_feedback.get("previous_rounds")
    if not isinstance(previous_rounds, list):
        previous_rounds = []
    compact_previous = []
    for item in previous_rounds[-6:]:
        if not isinstance(item, dict):
            continue
        diagnostics = item.get("proposal_diagnostics") if isinstance(item.get("proposal_diagnostics"), dict) else {}
        smoke_gate = item.get("smoke_gate") if isinstance(item.get("smoke_gate"), dict) else {}
        candidate_summary = item.get("candidate_summary") if isinstance(item.get("candidate_summary"), dict) else {}
        compact_previous.append(
            {
                "round_index": item.get("round_index"),
                "decision": item.get("decision"),
                "candidate_key": item.get("candidate_key"),
                "incumbent_key_after": item.get("incumbent_key_after"),
                "worker_status": item.get("worker_status"),
                "changed_files": item.get("worker_changed_files") or [],
                "summary": diagnostics.get("summary"),
                "strategy_intent": diagnostics.get("strategy_intent"),
                "rule_operator_hypotheses": (diagnostics.get("rule_operator_hypotheses") or [])[:4],
                "rejected_change_count": ((diagnostics.get("proposal_audit") or {}).get("rejected_change_count")),
                "proposal_warnings": ((diagnostics.get("proposal_audit") or {}).get("warnings") or [])[:6],
                "smoke_gate": {
                    "passed": smoke_gate.get("passed"),
                    "full_evaluation_started": smoke_gate.get("full_evaluation_started"),
                    "errors": (((smoke_gate.get("summary") or {}).get("validation_summary") or {}).get("top_errors") or [])[:2],
                },
                "judgment_issues": (
                    ((candidate_summary.get("validation_summary") or {}).get("agentic_judgment") or {}).get("issues")
                    or []
                )[:6],
                "judgment_suggestions": (
                    ((candidate_summary.get("validation_summary") or {}).get("agentic_judgment") or {}).get("suggestions")
                    or []
                )[:4],
                "validation_errors": ((candidate_summary.get("validation_summary") or {}).get("top_errors") or [])[:2],
            }
        )
    return {
        "round_index": loop_feedback.get("round_index"),
        "baseline_key": loop_feedback.get("baseline_key"),
        "incumbent_key_before": loop_feedback.get("incumbent_key_before"),
        "incumbent_worktree": loop_feedback.get("incumbent_worktree"),
        "baseline_best_metrics": baseline_summary.get("best_metrics"),
        "baseline_best_candidate_metrics": baseline_summary.get("best_candidate_metrics"),
        "baseline_validation_summary": baseline_summary.get("validation_summary"),
        "agent_generated_baseline_memory": compact_agent_generated_baseline_memory(
            loop_feedback.get("agent_generated_baseline_memory") or {}
        ),
        "round_semantics": loop_feedback.get("round_semantics") or {},
        "current_direction": loop_feedback.get("current_direction") or {},
        "direction_graph": compact_direction_graph_for_prompt(loop_feedback.get("direction_graph") or {}),
        "experience_memory": compact_experience_memory_for_prompt(loop_feedback.get("experience_memory") or {}),
        "skill_usage_summary": loop_feedback.get("skill_usage_summary") or {},
        "protected_promoted_facts": loop_feedback.get("protected_promoted_facts") or [],
        "failure_memory": loop_feedback.get("failure_memory") or {},
        "next_round_guidance": loop_feedback.get("next_round_guidance") or {},
        "current_round_repair": compact_current_round_repair(loop_feedback.get("current_round_repair") or {}),
        "previous_rounds": compact_previous,
        "instructions": loop_feedback.get("instructions"),
    }


def compact_agent_generated_baseline_memory(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    return {
        "status": value.get("status"),
        "accepted_as_incumbent": value.get("accepted_as_incumbent"),
        "baseline_key": value.get("baseline_key"),
        "worker_status": value.get("worker_status"),
        "worker_changed_files": (value.get("worker_changed_files") or [])[:8],
        "repair_attempt_count": value.get("repair_attempt_count"),
        "repair_recovered": value.get("repair_recovered"),
        "agentic_accepted": value.get("agentic_accepted"),
        "agentic_issues": (value.get("agentic_issues") or [])[:8],
        "proposal_summary": str(value.get("proposal_summary") or "")[:500],
        "strategy_intent": str(value.get("strategy_intent") or "")[:800],
        "rule_operator_hypotheses": (value.get("rule_operator_hypotheses") or [])[:4],
        "protection_rule": str(value.get("protection_rule") or "")[:800],
    }


def compact_direction_graph_for_prompt(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    directions = value.get("directions")
    if not isinstance(directions, list):
        directions = []
    compact_directions = []
    for item in directions[-6:]:
        if not isinstance(item, dict):
            continue
        compact_directions.append(
            {
                "direction_id": item.get("direction_id"),
                "parent_id": item.get("parent_id"),
                "round_index": item.get("round_index"),
                "title": item.get("title"),
                "status": item.get("status"),
                "decision": item.get("decision"),
                "strategy_type": item.get("strategy_type"),
                "target_files": (item.get("target_files") or [])[:8],
                "score_relation": item.get("score_relation"),
                "attempt_count": item.get("attempt_count"),
            }
        )
    return {
        "schema_version": value.get("schema_version"),
        "round_semantics": value.get("round_semantics"),
        "direction_count": value.get("direction_count"),
        "attempt_count": value.get("attempt_count"),
        "status_counts": value.get("status_counts") or {},
        "decision_counts": value.get("decision_counts") or {},
        "promoted_direction_ids": (value.get("promoted_direction_ids") or [])[-6:],
        "directions": compact_directions,
        "guidance": (value.get("guidance") or [])[:6],
    }


def compact_experience_memory_for_prompt(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    tiers = value.get("memory_tiers")
    if not isinstance(tiers, dict):
        tiers = {}
    lessons = tiers.get("candidate_lessons")
    if not isinstance(lessons, list):
        lessons = []
    compact_lessons = []
    for item in lessons[-8:]:
        if not isinstance(item, dict):
            continue
        compact_lessons.append(
            {
                "lesson_id": item.get("lesson_id"),
                "lesson_type": item.get("lesson_type"),
                "strategy": item.get("strategy"),
                "strategy_type": item.get("strategy_type"),
                "outcome": item.get("outcome"),
                "applicability": (item.get("applicability") or [])[:3],
                "contraindications": (item.get("contraindications") or [])[:3],
                "confidence": item.get("confidence"),
            }
        )
    return {
        "schema_version": value.get("schema_version"),
        "write_policy": value.get("write_policy") or {},
        "candidate_lessons": compact_lessons,
        "agent_generated_quality_memory": compact_agent_generated_quality_memory(
            value.get("agent_generated_quality_memory") or {}
        ),
        "skill_usage_summary": value.get("skill_usage_summary") or {},
        "self_evolution_metrics": value.get("self_evolution_metrics") or {},
        "next_context_guidance": (value.get("next_context_guidance") or [])[:6],
    }


def compact_agent_generated_quality_memory(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    return {
        "attempt_count": value.get("attempt_count"),
        "rejected_attempt_count": value.get("rejected_attempt_count"),
        "recovered_direction_count": value.get("recovered_direction_count"),
        "recurring_quality_risks": (value.get("recurring_quality_risks") or [])[:5],
        "recurring_self_check_risks": (value.get("recurring_self_check_risks") or [])[:5],
        "recurring_runtime_import_risks": (value.get("recurring_runtime_import_risks") or [])[:5],
        "next_prompt_rule": str(value.get("next_prompt_rule") or "")[:1000],
    }


def compact_current_round_repair(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    attempts = value.get("previous_attempts")
    if not isinstance(attempts, list):
        attempts = []
    compact_attempts = []
    for attempt in attempts[-3:]:
        if not isinstance(attempt, dict):
            continue
        judgment = attempt.get("agentic_judgment") if isinstance(attempt.get("agentic_judgment"), dict) else {}
        error_analysis = (
            attempt.get("agentic_error_analysis") if isinstance(attempt.get("agentic_error_analysis"), dict) else {}
        )
        diagnostics = (
            attempt.get("proposal_diagnostics") if isinstance(attempt.get("proposal_diagnostics"), dict) else {}
        )
        audit = diagnostics.get("proposal_audit") if isinstance(diagnostics.get("proposal_audit"), dict) else {}
        compact_attempts.append(
            {
                "attempt_index": attempt.get("attempt_index"),
                "worker_status": attempt.get("worker_status"),
                "changed_files": (attempt.get("changed_files") or [])[:8],
                "failure_signatures": (attempt.get("failure_signatures") or [])[:10],
                "judgment_issues": (judgment.get("issues") or [])[:10],
                "judgment_suggestions": (judgment.get("suggestions") or [])[:6],
                "error_diagnosis": (error_analysis.get("diagnosis") or [])[:6],
                "error_suggestions": (error_analysis.get("suggestions") or [])[:6],
                "proposal_summary": diagnostics.get("summary"),
                "proposal_strategy": diagnostics.get("strategy_intent"),
                "accepted_change_paths": (audit.get("accepted_change_paths") or [])[:8],
                "rejected_change_count": audit.get("rejected_change_count"),
                "warnings": (audit.get("warnings") or [])[:10],
                "proposal_diagnostics": {
                    "apply_rejections": (diagnostics.get("apply_rejections") or [])[:8],
                    "rejected_edits": (diagnostics.get("rejected_edits") or [])[:8],
                },
            }
        )
    return {
        "status": value.get("status"),
        "attempt_index": value.get("attempt_index"),
        "max_repair_attempts": value.get("max_repair_attempts"),
        "must_do": value.get("must_do") or [],
        "avoid": value.get("avoid") or [],
        "repair_targets": compact_repair_targets_for_prompt(value.get("repair_targets") or {}),
        "previous_attempts": compact_attempts,
    }


def compact_repair_targets_for_prompt(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {}
    list_keys = [
        "agent_generated_solver_quality_risks",
        "agent_generated_solver_self_check_risks",
        "incomplete_solution_acceptance_risks",
        "protected_promoted_fact_regressions",
        "apply_rejections",
    ]
    for key in list_keys:
        items = value.get(key)
        if isinstance(items, list) and items:
            compact[key] = items[:8]
    for key in ("python_compile_errors", "agent_generated_solver_expected_contract"):
        item = value.get(key)
        if isinstance(item, dict) and item:
            compact[key] = item
    return compact


def compact_incumbent_code_context(incumbent_code_context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(incumbent_code_context, dict):
        return {}
    files = []
    for item in incumbent_code_context.get("files") or []:
        if not isinstance(item, dict):
            continue
        raw_snippet = str(item.get("snippet") or "")
        files.append(
            {
                "relative_path": item.get("relative_path"),
                "chars": item.get("chars"),
                "truncated": item.get("truncated"),
                "top_level_symbols": extract_top_level_symbols(raw_snippet),
                "snippet": compact_incumbent_source_snippet(raw_snippet, max_chars=PRIORITY_INCUMBENT_FILE_MAX_CHARS),
                "snippet_compacted_for_priority": len(raw_snippet) > PRIORITY_INCUMBENT_FILE_MAX_CHARS,
            }
        )
    return {
        "source": incumbent_code_context.get("source"),
        "purpose": incumbent_code_context.get("purpose"),
        "files": files,
    }


def extract_top_level_symbols(snippet: str) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    offset = 0
    for line_number, line in enumerate(snippet.splitlines(), start=1):
        stripped = line.strip()
        if line.startswith(("def ", "class ")):
            symbols.append(
                {
                    "line": line_number,
                    "char": offset,
                    "signature": stripped[:180],
                }
            )
        offset += len(line) + 1
    return symbols[:40]


def compact_incumbent_source_snippet(snippet: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(snippet) <= max_chars:
        return snippet

    lower = snippet.lower()
    anchors = [
        "def parse_instance",
        "def solve",
        "def decode",
        "def local_search",
        "def relocate",
        "def main",
        "if __name__",
    ]
    windows: list[tuple[int, int]] = []

    def add_window(start: int, end: int) -> None:
        start = max(0, start)
        end = min(len(snippet), end)
        if end <= start:
            return
        windows.append((start, end))

    add_window(0, min(len(snippet), 1400))
    for anchor in anchors:
        position = lower.find(anchor)
        if position < 0:
            continue
        extra_after = 1200 if any(key in anchor for key in ("solve", "local_search", "relocate")) else 900
        add_window(position - 260, position + extra_after)
    add_window(len(snippet) - 800, len(snippet))

    result = ""
    separator = "\n\n# ... priority context omitted middle of incumbent source ...\n\n"
    merged: list[tuple[int, int]] = []
    for start, end in sorted(windows):
        if not merged or start > merged[-1][1] + 120:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    for start, end in merged:
        piece = snippet[start:end].strip()
        if not piece:
            continue
        addition = piece if not result else separator + piece
        remaining = max_chars - len(result)
        if remaining <= 0:
            break
        if len(addition) > remaining:
            if remaining > len(separator) + 120:
                result += addition[:remaining]
            break
        result += addition
    return result[:max_chars]


def compact_priority_knowledge_cards(
    context: dict[str, Any],
    *,
    limit: int,
    max_chars_per_card: int = 2400,
) -> list[dict[str, Any]]:
    cards = context.get("knowledge_cards") or []
    if not isinstance(cards, list):
        return []
    typed_cards = [card for card in cards if isinstance(card, dict)]
    if not typed_cards:
        return []

    query_terms = _knowledge_query_terms(context)
    agent_generated_mode = "agent_generated" in query_terms

    def card_score(card: dict[str, Any]) -> int:
        path = str(card.get("path") or "").lower()
        snippet = str(card.get("snippet") or "").lower()
        haystack = f"{path}\n{snippet}"
        score = 0
        for term in query_terms:
            if term and term in haystack:
                score += 4
        if "sdst" in query_terms and "sdst" in haystack:
            score += 25
        agent_generated_card = (
            "agent_generated" in haystack or "agent-generated" in haystack or "generated solver" in haystack
        )
        awls_transfer_card = (
            "agent-generated-transfer" in haystack
            or "agent_generated_transfer" in haystack
            or "method-transfer" in haystack
            or "method transfer" in haystack
        ) and "awls" in haystack
        if agent_generated_mode and agent_generated_card:
            score += 220
        if agent_generated_mode and awls_transfer_card:
            score += 140
        if agent_generated_mode and "fjsp_sdst_agent_generated_search_memory" in haystack:
            score += 80
        if agent_generated_mode and "fjsp_variant_domain_pack_rag" in haystack:
            score += 30
        if agent_generated_mode and "fjsp_agent_current_capability" in haystack:
            score += 20
        if agent_generated_mode and "awls_sdst_" in path and not agent_generated_card:
            score -= 260
        elif agent_generated_mode and "awls" in haystack and not agent_generated_card:
            score -= 120
        if "local_search" in query_terms and ("local search" in haystack or "local-search" in haystack):
            score += 12
        if "loop_feedback" in query_terms and ("memory" in haystack or "failed attempt" in haystack):
            score += 12
        return score

    ranked = sorted(enumerate(typed_cards), key=lambda item: (card_score(item[1]), -item[0]), reverse=True)
    selected: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for _index, card in ranked:
        path = str(card.get("path") or "")
        if not path or path in seen_paths:
            continue
        score = card_score(card)
        if score <= 0 and selected:
            continue
        snippet = compact_knowledge_card_snippet(
            str(card.get("snippet") or ""),
            query_terms=query_terms,
            max_chars=max_chars_per_card,
        )
        selected.append(
            {
                "path": path,
                "chars": card.get("chars"),
                "truncated": bool(card.get("truncated")),
                "relevance_score": score,
                "snippet": snippet[:max_chars_per_card],
            }
        )
        seen_paths.add(path)
        if len(selected) >= limit:
            break
    return selected


def compact_knowledge_card_snippet(snippet: str, *, query_terms: set[str], max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(snippet) <= max_chars:
        return snippet

    lower = snippet.lower().replace("_", "-")
    local_search_needles = [
        "local evidence",
        "local method evidence",
        "recent web worker-loop artifacts",
        "local search quality",
        "risk patterns",
        "candidate schedules must contain",
        "partial schedule",
        "decode fixed machine sequences",
        "operation coverage",
        "machine sequences",
        "deadlock",
    ]
    general_needles = [
        "local evidence",
        "local method evidence",
        "recent web worker-loop artifacts",
        "method-level",
        "operation-level setup-aware",
        "what to preserve",
        "preserve or recover",
        "operation-level",
        "setup-aware",
        "multi-start",
        "critical-block",
        "critical path",
        "method transfer",
        "agent-generated-transfer",
        "awls-sdst method transfer",
        "head/tail",
        "rk/lk",
        "aspiration",
        "anchor text",
        "old text",
        "regret",
        "insertion",
        "tabu",
        "avoid",
    ]
    if "local_search" in query_terms:
        needles = local_search_needles + general_needles
    else:
        needles = general_needles + local_search_needles
    for term in sorted(query_terms):
        normalized = term.lower().replace("_", "-")
        if len(normalized) >= 5 and normalized not in {"agent", "generated"}:
            needles.append(normalized)

    windows: list[tuple[int, int]] = []

    def add_window(start: int, end: int, *, overlap_margin: int = 80) -> None:
        start = max(0, start)
        end = min(len(snippet), end)
        if end <= start:
            return
        for existing_start, existing_end in windows:
            if start <= existing_end + overlap_margin and end >= existing_start - overlap_margin:
                return
        windows.append((start, end))

    def add_markdown_section(heading: str, *, char_limit: int) -> None:
        position = lower.find(heading)
        if position < 0:
            return
        line_start = snippet.rfind("\n", 0, position) + 1
        start = line_start if not lower[line_start:position].strip() else position
        next_heading = re.search(r"\n##+\s+", lower[position + 1 :])
        end = position + 1 + next_heading.start() if next_heading else len(snippet)
        add_window(start, min(end, start + char_limit), overlap_margin=-1)

    def add_anchor_excerpt(anchor: str, *, before: int, after: int) -> None:
        position = lower.find(anchor)
        if position < 0:
            return
        add_window(position - before, position + after, overlap_margin=-1)

    has_local_experiment_memory = (
        "local evidence" in lower or "local method evidence" in lower or "agent-generated" in lower
    )
    intro_chars = 360 if has_local_experiment_memory else 520
    add_window(0, min(len(snippet), min(intro_chars, max_chars)))
    if has_local_experiment_memory:
        add_markdown_section("## local evidence", char_limit=1700)
        add_markdown_section("## local method evidence", char_limit=1700)
        add_markdown_section("## what to preserve", char_limit=760)
        if "local_search" in query_terms:
            add_anchor_excerpt("risk patterns already observed", before=80, after=880)

    for needle in needles:
        position = lower.find(needle)
        if position < 0:
            continue
        add_window(position - 180, position + 560)
        if len(windows) >= 7:
            break

    result = ""
    separator = "\n...\n"
    for start, end in windows:
        piece = snippet[start:end].strip()
        if not piece:
            continue
        addition = piece if not result else separator + piece
        remaining = max_chars - len(result)
        if remaining <= 0:
            break
        if len(addition) > remaining:
            if remaining > len(separator) + 80:
                result += addition[:remaining]
            break
        result += addition
    return result[:max_chars]


def _knowledge_query_terms(context: dict[str, Any]) -> set[str]:
    terms: set[str] = set()

    def add_text(value: Any) -> None:
        text = str(value).lower().replace("-", "_")
        for raw in re.split(r"[^a-z0-9_]+", text):
            term = raw.strip("_")
            if len(term) >= 3:
                terms.add(term)

    task = context.get("task") if isinstance(context.get("task"), dict) else {}
    add_text(task.get("problem_family"))
    add_text(task.get("description"))
    for instance in task.get("instances") or []:
        if isinstance(instance, dict):
            add_text(instance.get("id"))
            add_text(instance.get("path"))

    capability = context.get("problem_family_capability")
    if isinstance(capability, dict):
        for key in ("supported_variants", "knowledge_tags", "specialization_hooks"):
            for item in capability.get(key) or []:
                add_text(item)

    evaluator_protocol = context.get("evaluator_protocol")
    if isinstance(evaluator_protocol, dict):
        add_text(evaluator_protocol.get("solver_command_template"))
        add_text(evaluator_protocol.get("quick_test_command"))

    add_text(context.get("hypothesis"))
    if context.get("iteration_edit_contract"):
        terms.add("loop_feedback")
    if context.get("loop_feedback"):
        terms.add("loop_feedback")
        add_text(context.get("loop_feedback"))
    if context.get("incumbent_code_context"):
        add_text(context.get("incumbent_code_context"))

    if any(term in terms for term in {"fjsp_sdst", "sequence_dependent_setup", "setup_matrix", "oddla20", "oddla"}):
        terms.add("sdst")
    if any("agent_generated" in term or term == "generated" for term in terms):
        terms.add("agent_generated")
    if any(term in terms for term in {"local_search_operator", "neighborhood", "tabu", "hill_climbing"}):
        terms.add("local_search")
    return terms


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


def normalize_solver_contract_self_check(value: Any, context: dict[str, Any]) -> dict[str, Any]:
    quality_contract = build_agent_generated_solver_quality_contract(context)
    if not isinstance(value, dict):
        return {
            "present": False,
            "active_features": [],
            "capabilities": [],
            "representation": "",
            "decoder": "",
            "variant_handling": [],
            "runtime_bounds": "",
            "incumbent_preservation": "",
            "remaining_gaps": [],
            "expected_active_features": quality_contract.get("active_features", []),
            "expected_capabilities": _quality_contract_capabilities(quality_contract),
        }

    active_features = _normalize_string_list(value.get("active_features"), limit=30, max_chars=80)
    capabilities = normalize_solver_capability_records(value.get("capabilities"))
    return {
        "present": True,
        "active_features": active_features,
        "capabilities": capabilities,
        "implemented_capabilities": sorted(
            {
                item["name"]
                for item in capabilities
                if item.get("status") == "implemented"
            }
        ),
        "representation": str(value.get("representation", ""))[:1200],
        "decoder": str(value.get("decoder", ""))[:1200],
        "variant_handling": _normalize_string_list(value.get("variant_handling"), limit=20, max_chars=400),
        "runtime_bounds": str(value.get("runtime_bounds", ""))[:1000],
        "incumbent_preservation": str(value.get("incumbent_preservation", ""))[:1000],
        "remaining_gaps": _normalize_string_list(value.get("remaining_gaps"), limit=20, max_chars=400),
        "expected_active_features": quality_contract.get("active_features", []),
        "expected_capabilities": _quality_contract_capabilities(quality_contract),
    }


def normalize_solver_capability_records(value: Any) -> list[dict[str, str]]:
    if isinstance(value, dict):
        raw_items: list[Any] = [
            {"name": key, **(item if isinstance(item, dict) else {"evidence": item})}
            for key, item in value.items()
        ]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_items:
        if isinstance(item, str):
            raw_name = item
            status = "implemented"
            evidence = ""
        elif isinstance(item, dict):
            raw_name = str(item.get("name") or item.get("capability") or "")
            status = str(item.get("status") or "").strip().lower()
            evidence = str(item.get("evidence") or item.get("where") or item.get("implementation") or "")
        else:
            continue
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_name.strip())[:96]
        if not name or name in seen:
            continue
        seen.add(name)
        if status not in {"implemented", "missing", "not_applicable", "planned"}:
            status = "implemented" if evidence else "missing"
        records.append({"name": name, "status": status, "evidence": evidence[:1200]})
        if len(records) >= 60:
            break
    return records


def _normalize_string_list(value: Any, *, limit: int, max_chars: int) -> list[str]:
    if isinstance(value, str):
        raw_items: list[Any] = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item).strip()[:max_chars]
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _quality_contract_capabilities(quality_contract: dict[str, Any]) -> list[str]:
    if not quality_contract.get("enabled"):
        return []
    capabilities: list[str] = []
    for key in ("required_code_capabilities", "variant_required_code_capabilities"):
        for item in quality_contract.get(key) or []:
            if isinstance(item, str) and item not in capabilities:
                capabilities.append(item)
    return capabilities


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
    priority_knowledge = compact_priority_knowledge_cards(context, limit=5, max_chars_per_card=400)
    knowledge_referenced = _proposal_references_knowledge(proposal, priority_knowledge)
    quality_contract = build_agent_generated_solver_quality_contract(context)
    solver_self_check_audit = build_solver_contract_self_check_audit(
        proposal.get("solver_contract_self_check") or {},
        quality_contract=quality_contract,
        proposal=proposal,
        accepted_paths=accepted_paths,
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
    if accepted_paths and priority_knowledge and not knowledge_referenced:
        warnings.append("priority_knowledge_cards_not_referenced")
    warnings.extend(solver_self_check_audit["warnings"])

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
        "priority_knowledge_paths": [str(card.get("path") or "") for card in priority_knowledge],
        "declared_knowledge_used": knowledge_referenced,
        "solver_contract_self_check": solver_self_check_audit,
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


def build_solver_contract_self_check_audit(
    self_check: dict[str, Any],
    *,
    quality_contract: dict[str, Any],
    proposal: dict[str, Any],
    accepted_paths: list[str],
) -> dict[str, Any]:
    warnings: list[str] = []
    changed_agent_generated_solver = any(_looks_like_agent_generated_solver_path(path) for path in accepted_paths)
    if not quality_contract.get("enabled") or not changed_agent_generated_solver:
        return {
            "required": False,
            "present": bool(self_check.get("present")),
            "changed_agent_generated_solver": changed_agent_generated_solver,
            "missing_active_features": [],
            "missing_capabilities": [],
            "capabilities_without_evidence": [],
            "capabilities_without_concrete_source_evidence": [],
            "capabilities_with_source_mismatch": [],
            "warnings": warnings,
        }

    if not self_check.get("present"):
        warnings.append("agent_generated_solver_self_check_missing")
        return {
            "required": True,
            "present": False,
            "changed_agent_generated_solver": changed_agent_generated_solver,
            "missing_active_features": quality_contract.get("active_features", []),
            "missing_capabilities": _quality_contract_capabilities(quality_contract),
            "capabilities_without_evidence": [],
            "capabilities_without_concrete_source_evidence": [],
            "capabilities_with_source_mismatch": [],
            "warnings": warnings,
        }

    declared_features = {str(item) for item in self_check.get("active_features") or []}
    expected_features = {str(item) for item in quality_contract.get("active_features") or []}
    missing_features = sorted(expected_features - declared_features)
    if missing_features:
        warnings.append("agent_generated_solver_self_check_missing_active_features")

    implemented = {
        str(item.get("name"))
        for item in self_check.get("capabilities") or []
        if isinstance(item, dict) and item.get("status") == "implemented"
    }
    expected_capabilities = set(_quality_contract_capabilities(quality_contract))
    missing_capabilities = sorted(expected_capabilities - implemented)
    if missing_capabilities:
        warnings.append("agent_generated_solver_self_check_missing_required_capabilities")

    capabilities_without_evidence = sorted(
        str(item.get("name"))
        for item in self_check.get("capabilities") or []
        if isinstance(item, dict)
        and item.get("status") == "implemented"
        and not str(item.get("evidence") or "").strip()
    )
    if capabilities_without_evidence:
        warnings.append("agent_generated_solver_self_check_missing_evidence")
    vague_capability_evidence = sorted(
        str(item.get("name"))
        for item in self_check.get("capabilities") or []
        if isinstance(item, dict)
        and item.get("status") == "implemented"
        and _solver_capability_evidence_is_vague(str(item.get("evidence") or ""))
    )
    if vague_capability_evidence:
        warnings.append("agent_generated_solver_self_check_vague_evidence")

    change_source_text = _agent_generated_change_source_text(proposal)
    source_evidence = _self_check_source_evidence_audit(
        self_check,
        expected_capabilities=expected_capabilities,
        source_text=change_source_text,
    )
    if source_evidence["without_concrete_source_evidence"]:
        warnings.append("agent_generated_solver_self_check_no_concrete_source_evidence")
    if source_evidence["source_mismatch"]:
        warnings.append("agent_generated_solver_self_check_source_mismatch")

    return {
        "required": True,
        "present": True,
        "changed_agent_generated_solver": changed_agent_generated_solver,
        "missing_active_features": missing_features,
        "missing_capabilities": missing_capabilities,
        "capabilities_without_evidence": capabilities_without_evidence,
        "capabilities_with_vague_evidence": vague_capability_evidence,
        "capabilities_without_concrete_source_evidence": source_evidence[
            "without_concrete_source_evidence"
        ],
        "capabilities_with_source_mismatch": source_evidence["source_mismatch"],
        "warnings": warnings,
    }


def _solver_capability_evidence_is_vague(evidence: str) -> bool:
    stripped = evidence.strip().lower()
    if not stripped:
        return False
    vague_values = {"done", "implemented", "yes", "ok", "handled", "supported", "complete"}
    if stripped in vague_values:
        return True
    return len(stripped) < 20 and not any(token in stripped for token in ("def ", "parse", "decode", "guard", "check", "main"))


def _self_check_source_evidence_audit(
    self_check: dict[str, Any],
    *,
    expected_capabilities: set[str],
    source_text: str,
) -> dict[str, list[str]]:
    if not source_text.strip():
        return {
            "without_concrete_source_evidence": [],
            "source_mismatch": [],
        }
    source_lower = source_text.lower()
    without_concrete: list[str] = []
    source_mismatch: list[str] = []
    for item in self_check.get("capabilities") or []:
        if not isinstance(item, dict) or item.get("status") != "implemented":
            continue
        capability = str(item.get("name") or "")
        if capability not in expected_capabilities:
            continue
        evidence = str(item.get("evidence") or "")
        tokens = [
            token
            for token in _solver_capability_evidence_tokens(evidence)
            if token != capability and token not in expected_capabilities
        ]
        if not tokens:
            without_concrete.append(capability)
            continue
        missing = [token for token in tokens if token.lower() not in source_lower]
        if len(missing) == len(tokens):
            source_mismatch.append(capability)
    return {
        "without_concrete_source_evidence": without_concrete,
        "source_mismatch": source_mismatch,
    }


def _solver_capability_evidence_tokens(evidence: str) -> list[str]:
    raw_tokens = re.findall(r"`([^`]+)`|([A-Za-z_][A-Za-z0-9_]{2,})", evidence)
    tokens = [first or second for first, second in raw_tokens]
    generic = {
        "implemented",
        "implementation",
        "capability",
        "capabilities",
        "function",
        "functions",
        "guard",
        "guards",
        "logic",
        "solver",
        "schedule",
        "schedules",
        "source",
        "code",
        "uses",
        "used",
        "with",
        "where",
        "evidence",
        "active",
        "variant",
        "feature",
        "features",
        "this",
        "that",
        "every",
        "each",
        "path",
        "paths",
        "handled",
        "supported",
        "complete",
    }
    result: list[str] = []
    for token in tokens:
        for part in re.split(r"[/.,:;()\[\]\s]+", token):
            stripped = part.strip("_")
            if len(stripped) < 3:
                continue
            lowered = stripped.lower()
            if lowered in generic:
                continue
            if stripped not in result:
                result.append(stripped)
            if len(result) >= 8:
                return result
    return result


def _agent_generated_change_source_text(proposal: dict[str, Any]) -> str:
    parts: list[str] = []
    for change in proposal.get("changes") or []:
        if not isinstance(change, dict):
            continue
        if not _looks_like_agent_generated_solver_path(str(change.get("path") or "")):
            continue
        for key in ("content", "new"):
            value = change.get(key)
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


def _looks_like_agent_generated_solver_path(path: str) -> bool:
    normalized = normalize_relative_path(path).lower()
    if not normalized.startswith("examples/") or not normalized.endswith(".py"):
        return False
    return "agent_generated" in normalized or "generated_fjsp" in normalized


def _proposal_references_knowledge(proposal: dict[str, Any], priority_knowledge: list[dict[str, Any]]) -> bool:
    proposal_text = proposal_search_text(proposal)
    if "knowledge_cards" in proposal_text or "knowledge cards" in proposal_text or "rag" in proposal_text:
        return True
    for card in priority_knowledge:
        path = str(card.get("path") or "")
        if not path:
            continue
        name = Path(path).name.lower()
        stem = Path(path).stem.lower()
        if name and name in proposal_text:
            return True
        if stem and stem in proposal_text:
            return True
    return False


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
    self_check = proposal.get("solver_contract_self_check") or {}
    if isinstance(self_check, dict):
        parts.extend(
            [
                " ".join(str(value) for value in self_check.get("active_features") or []),
                str(self_check.get("representation") or ""),
                str(self_check.get("decoder") or ""),
                " ".join(str(value) for value in self_check.get("variant_handling") or []),
                str(self_check.get("runtime_bounds") or ""),
                str(self_check.get("incumbent_preservation") or ""),
                " ".join(str(value) for value in self_check.get("remaining_gaps") or []),
            ]
        )
        for item in self_check.get("capabilities") or []:
            if isinstance(item, dict):
                parts.extend([str(item.get("name") or ""), str(item.get("status") or ""), str(item.get("evidence") or "")])
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


def inserts_top_level_code_after_definition(anchor: str, content: str) -> bool:
    """Detect the common patch shape that leaves a function/class with no body."""

    anchor_tail = anchor.strip().splitlines()[-1] if anchor.strip() else ""
    if not re.match(r"^(async\s+def|def|class)\s+.+:\s*(#.*)?$", anchor_tail):
        return False
    for line in content.splitlines():
        if not line.strip():
            continue
        return not line[0].isspace() and bool(re.match(r"^(@|async\s+def|def|class|import|from)\b", line))
    return False


def create_or_replace_forbidden(path_value: str, context: dict[str, Any]) -> bool:
    contract = context.get("iteration_edit_contract")
    if not isinstance(contract, dict) or contract.get("mode") != "incremental_after_baseline":
        return False
    if incumbent_requires_legality_repair(context):
        return False
    normalized = normalize_relative_path(path_value)
    if not _looks_like_solver_file(normalized):
        return False
    incumbent_path = _context_incumbent_worktree(context)
    if incumbent_path is None:
        return False
    return (incumbent_path / normalized).exists()


def incumbent_requires_legality_repair(context: dict[str, Any]) -> bool:
    loop_feedback = context.get("loop_feedback")
    if not isinstance(loop_feedback, dict):
        return False
    incumbent_key = loop_feedback.get("incumbent_key_before")
    if isinstance(incumbent_key, list) and any(not _finite_number(value) for value in incumbent_key):
        return True
    baseline_summary = loop_feedback.get("baseline_summary")
    if isinstance(baseline_summary, dict):
        total = baseline_summary.get("total")
        valid = baseline_summary.get("valid")
        if isinstance(total, int) and total > 0 and valid == 0:
            return True
    return False


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _context_incumbent_worktree(context: dict[str, Any]) -> Path | None:
    loop_feedback = context.get("loop_feedback")
    if not isinstance(loop_feedback, dict):
        return None
    raw_path = loop_feedback.get("incumbent_worktree")
    if not raw_path:
        return None
    return Path(str(raw_path))


def _looks_like_solver_file(path_value: str) -> bool:
    normalized = normalize_relative_path(path_value).lower()
    if not normalized.endswith(".py"):
        return False
    name = Path(normalized).name
    return "solver" in name


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
        action = str(change.get("action", "create_or_replace"))
        if action == "create_or_replace":
            target.write_text(str(change.get("content", "")), encoding="utf-8")
        elif action == "replace_slot_block":
            slot_id = str(change.get("slot_id", "")).strip()
            slot, slot_error = confirmed_context_slot(context, slot_id)
            if slot is None:
                proposal.setdefault("apply_rejections", []).append({"path": relative_path, "reason": slot_error})
                continue
            expected_path = normalize_relative_path(str(slot.get("target_file", "")))
            if relative_path != expected_path:
                proposal.setdefault("apply_rejections", []).append(
                    {"path": relative_path, "reason": f"slot {slot_id!r} target_file is {expected_path!r}"}
                )
                continue
            if not target.exists():
                proposal.setdefault("apply_rejections", []).append({"path": relative_path, "reason": "target file does not exist"})
                continue
            text = target.read_text(encoding="utf-8")
            try:
                updated = replace_marked_block(
                    text,
                    str(slot.get("marker_start", "")),
                    str(slot.get("marker_end", "")),
                    str(change.get("content", "")),
                )
            except ValueError as exc:
                proposal.setdefault("apply_rejections", []).append({"path": relative_path, "reason": str(exc)})
                continue
            target.write_text(updated, encoding="utf-8")
        elif action == "text_replace":
            if not target.exists():
                proposal.setdefault("apply_rejections", []).append({"path": relative_path, "reason": "target file does not exist"})
                continue
            text = target.read_text(encoding="utf-8")
            old = str(change.get("old", ""))
            if old not in text:
                proposal.setdefault("apply_rejections", []).append({"path": relative_path, "reason": "old text not found"})
                continue
            target.write_text(text.replace(old, str(change.get("new", "")), 1), encoding="utf-8")
        elif action == "insert_after":
            if not target.exists():
                proposal.setdefault("apply_rejections", []).append({"path": relative_path, "reason": "target file does not exist"})
                continue
            text = target.read_text(encoding="utf-8")
            anchor = str(change.get("anchor", ""))
            if anchor not in text:
                proposal.setdefault("apply_rejections", []).append({"path": relative_path, "reason": "anchor text not found"})
                continue
            insert_text = str(change.get("content", ""))
            target.write_text(insert_after_anchor(text, anchor, insert_text), encoding="utf-8")
        elif action == "insert_before":
            if not target.exists():
                proposal.setdefault("apply_rejections", []).append({"path": relative_path, "reason": "target file does not exist"})
                continue
            text = target.read_text(encoding="utf-8")
            anchor = str(change.get("anchor", ""))
            if anchor not in text:
                proposal.setdefault("apply_rejections", []).append({"path": relative_path, "reason": "anchor text not found"})
                continue
            insert_text = str(change.get("content", ""))
            target.write_text(insert_before_anchor(text, anchor, insert_text), encoding="utf-8")
        else:
            proposal.setdefault("apply_rejections", []).append({"path": relative_path, "reason": f"unsupported action: {action}"})
            continue
        changed_files.append(relative_path)
    return changed_files


def insert_after_anchor(text: str, anchor: str, insert_text: str) -> str:
    start = text.find(anchor)
    if start < 0:
        raise ValueError("anchor text not found")
    end = start + len(anchor)
    before = text[:end]
    after = text[end:]
    normalized_insert = str(insert_text)
    if before and not before.endswith(("\n", "\r")) and not normalized_insert.startswith(("\n", "\r")):
        normalized_insert = "\n" + normalized_insert
    if after and not after.startswith(("\n", "\r")) and not normalized_insert.endswith(("\n", "\r")):
        normalized_insert += "\n"
    return before + normalized_insert + after


def insert_before_anchor(text: str, anchor: str, insert_text: str) -> str:
    start = text.find(anchor)
    if start < 0:
        raise ValueError("anchor text not found")
    before = text[:start]
    after = text[start:]
    normalized_insert = str(insert_text)
    if before and not before.endswith(("\n", "\r")) and not normalized_insert.startswith(("\n", "\r")):
        normalized_insert = "\n" + normalized_insert
    if after and not after.startswith(("\n", "\r")) and not normalized_insert.endswith(("\n", "\r")):
        normalized_insert += "\n"
    return before + normalized_insert + after


def confirmed_context_slot(context: dict[str, Any], slot_id: str) -> tuple[dict[str, Any] | None, str]:
    if not slot_id:
        return None, "replace_slot_block requires slot_id"
    errors = validate_slot_manifest_gate(context, slot_id)
    if errors:
        return None, "; ".join(errors)
    manifest = context.get("slot_manifest")
    slots = manifest.get("slots") if isinstance(manifest, dict) else []
    if not isinstance(slots, list):
        return None, "slot_manifest.slots must be a list"
    for item in slots:
        if isinstance(item, dict) and item.get("slot_id") == slot_id:
            return item, ""
    return None, f"slot_manifest does not contain required slot_id {slot_id!r}"


def normalize_slot_replacement_content(content: str, slot: dict[str, Any]) -> str:
    normalized = strip_markdown_code_fence(content)
    marker_start = str(slot.get("marker_start", ""))
    marker_end = str(slot.get("marker_end", ""))
    if marker_start and marker_end and marker_start in normalized and marker_end in normalized:
        try:
            normalized = extract_marked_block(normalized, marker_start, marker_end)
        except ValueError:
            pass
    return normalized.rstrip() + "\n"


def strip_markdown_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


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
    self_check = proposal.get("solver_contract_self_check") or {}
    if self_check:
        lines.extend(
            [
                "",
                "## Solver Contract Self-Check",
                "",
                f"- present: `{self_check.get('present')}`",
                f"- active_features: `{json.dumps(self_check.get('active_features') or [], ensure_ascii=False)}`",
                f"- implemented_capabilities: `{json.dumps(self_check.get('implemented_capabilities') or [], ensure_ascii=False)}`",
                f"- representation: {self_check.get('representation') or 'N/A'}",
                f"- decoder: {self_check.get('decoder') or 'N/A'}",
                f"- variant_handling: `{json.dumps(self_check.get('variant_handling') or [], ensure_ascii=False)}`",
                f"- runtime_bounds: {self_check.get('runtime_bounds') or 'N/A'}",
                f"- incumbent_preservation: {self_check.get('incumbent_preservation') or 'N/A'}",
                f"- remaining_gaps: `{json.dumps(self_check.get('remaining_gaps') or [], ensure_ascii=False)}`",
                "",
            ]
        )
        capabilities = self_check.get("capabilities") or []
        if capabilities:
            lines.append("### Capability Evidence")
            lines.append("")
            for item in capabilities:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"- `{item.get('name')}` / `{item.get('status')}`: {item.get('evidence') or 'N/A'}"
                )
            lines.append("")
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
                f"- solver_contract_self_check: `{json.dumps(audit.get('solver_contract_self_check') or {}, ensure_ascii=False)}`",
                f"- warnings: `{json.dumps(audit.get('warnings') or [], ensure_ascii=False)}`",
            ]
        )
    rejected = proposal.get("rejected_changes", [])
    if rejected:
        lines.extend(["", "## Rejected Changes", ""])
        for item in rejected:
            lines.append(f"- `{item.get('path')}`: {item.get('reason')}")
    apply_rejections = proposal.get("apply_rejections", [])
    if apply_rejections:
        lines.extend(["", "## Apply Rejections", ""])
        for item in apply_rejections:
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
