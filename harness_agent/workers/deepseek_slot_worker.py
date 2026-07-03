from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from ..deepseek_client import DeepSeekClient, is_deepseek_configured
from ..slot_contract import replace_marked_block, validate_slot_manifest_gate
from ..worker import CodingWorker, ExperimentSpec, WorkerCapabilities, WorkerResult
from .deepseek_worker import apply_code_edit_proposal, confirmed_context_slot, extract_json_object, render_code_edit_markdown


SLOT_RELATIVE_PATH = "examples/awls_evolved_slots.py"
EVOLVE_START = "# EVOLVE_START"
EVOLVE_END = "# EVOLVE_END"
ALLOWED_FUNCTION_NAMES = {"float", "int", "abs", "max", "min", "round"}
ALLOWED_VALUE_KEYS = {
    "weight",
    "cooldown",
    "rr",
    "gamma",
    "cooling",
    "base",
    "sqrt_weight",
    "log_weight",
    "is_critical",
    "forward",
    "backward",
    "duration",
    "machine_load",
    "position",
    "setup_adjacent",
    "setup_adjacent_ratio",
    "setup_is_sdst",
    "setup_next",
    "setup_next_ratio",
    "setup_predecessor_critical",
    "setup_prev",
    "setup_prev_ratio",
    "setup_successor_critical",
}
FORBIDDEN_AST_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.Global,
    ast.Nonlocal,
    ast.With,
    ast.AsyncWith,
    ast.Lambda,
    ast.ClassDef,
    ast.Delete,
    ast.Raise,
    ast.Yield,
    ast.YieldFrom,
    ast.Await,
)
REQUIRED_SLOT_ID = "awls_zi_policy"


class DeepSeekSlotWorker(CodingWorker):
    """DeepSeek worker that can only rewrite one EVOLVE-marked AWLS policy slot."""

    def __init__(self, model: str = "deepseek-v4-pro") -> None:
        self.model = model
        self.available = is_deepseek_configured()

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            name="deepseek_slot" if self.available else "deepseek_slot_unavailable",
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
        selected_slot, slot_error = selected_confirmed_slot(context)
        if selected_slot is None:
            gate_errors = [slot_error]
            slot_id = ""
        elif is_awls_zi_slot(selected_slot):
            gate_errors = validate_awls_slot_contract(context)
            slot_id = REQUIRED_SLOT_ID
        else:
            slot_id = str(selected_slot.get("slot_id", ""))
            gate_errors = validate_generic_slot_contract(context, slot_id)
        if gate_errors:
            gate_path = output_dir / "slot_contract_rejection.json"
            gate_path.write_text(
                json.dumps(
                    {
                        "status": "contract_rejected",
                        "slot_id": slot_id,
                        "errors": gate_errors,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            return WorkerResult(
                status="contract_rejected",
                changed_files=[],
                summary="Slot manifest does not confirm exactly one supported code slot.",
                artifacts={"output_dir": str(output_dir), "slot_contract_rejection": str(gate_path)},
            )
        if selected_slot is not None and not is_awls_zi_slot(selected_slot):
            return self._run_generic_slot_experiment(spec=spec, context=context, output_dir=output_dir, slot=selected_slot)
        current_slot = (Path(spec.worktree_path) / SLOT_RELATIVE_PATH).read_text(encoding="utf-8")
        client = DeepSeekClient.from_env(model=self.model)
        prompt = self._slot_prompt(context=context, current_slot=current_slot, max_steps=spec.max_steps)
        raw = client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are an algorithm-code-slot designer. Return compact JSON only. "
                        "Do not edit parsers, evaluators, benchmark code, or any file path."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.25,
            max_tokens=4500,
            json_mode=True,
        )
        raw_path = output_dir / "deepseek_slot_raw.json"
        raw_path.write_text(raw, encoding="utf-8")
        try:
            proposal = extract_json_object(raw)
        except json.JSONDecodeError as exc:
            repair = self._repair_slot_json(client, raw, str(exc), max_tokens=4500)
            (output_dir / "deepseek_slot_repair_response.json").write_text(repair, encoding="utf-8")
            proposal = extract_json_object(repair)

        normalized = self._normalize_slot_proposal(proposal)
        proposal_path = output_dir / "proposal.json"
        markdown_path = output_dir / "proposal.md"
        changed_files: list[str] = []
        if spec.apply_changes and not normalized.get("rejected"):
            slot_path = Path(spec.worktree_path) / SLOT_RELATIVE_PATH
            slot_text = slot_path.read_text(encoding="utf-8")
            slot_path.write_text(replace_evolve_block(slot_text, normalized["function_code"]), encoding="utf-8")
            changed_files = [SLOT_RELATIVE_PATH]
        normalized["changed_files"] = changed_files
        proposal_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(render_slot_markdown(normalized), encoding="utf-8")

        return WorkerResult(
            status="applied" if changed_files else "proposal_created",
            changed_files=changed_files,
            summary=str(normalized.get("summary") or normalized.get("strategy_intent") or "DeepSeek slot proposal created."),
            raw_log_path=str(raw_path),
            artifacts={
                "output_dir": str(output_dir),
                "proposal": str(proposal_path),
                "proposal_markdown": str(markdown_path),
            },
        )

    def _run_generic_slot_experiment(
        self,
        *,
        spec: ExperimentSpec,
        context: dict[str, Any],
        output_dir: Path,
        slot: dict[str, Any],
    ) -> WorkerResult:
        client = DeepSeekClient.from_env(model=self.model)
        prompt = self._generic_slot_prompt(context=context, slot=slot, max_steps=spec.max_steps)
        raw = client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are an algorithm-code-slot designer. Return compact JSON only. "
                        "Modify only the user-confirmed slot block. Do not edit parsers, evaluators, or benchmark code."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=12000,
            json_mode=True,
        )
        raw_path = output_dir / "deepseek_slot_raw.json"
        raw_path.write_text(raw, encoding="utf-8")
        try:
            proposal = extract_generic_slot_proposal(raw)
        except json.JSONDecodeError as exc:
            repair = self._repair_generic_slot_json(client, raw, str(exc), max_tokens=12000)
            (output_dir / "deepseek_slot_repair_response.json").write_text(repair, encoding="utf-8")
            proposal = extract_generic_slot_proposal(repair)

        normalized = self._normalize_generic_slot_proposal(proposal, slot, context=context)
        if generic_slot_needs_repair(normalized):
            original_normalized = normalized
            repair_prompt = self._repair_generic_slot_proposal_prompt(
                prompt=prompt,
                proposal=normalized,
                slot=slot,
            )
            (output_dir / "deepseek_slot_semantic_repair_prompt.md").write_text(repair_prompt, encoding="utf-8")
            repair = client.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "Repair the rejected slot-block proposal. Return compact JSON only. "
                            "Modify only the user-confirmed slot block."
                        ),
                    },
                    {"role": "user", "content": repair_prompt},
                ],
                temperature=0.1,
                max_tokens=12000,
                json_mode=True,
            )
            (output_dir / "deepseek_slot_semantic_repair_response.json").write_text(repair, encoding="utf-8")
            try:
                repaired = self._normalize_generic_slot_proposal(
                    extract_generic_slot_proposal(repair),
                    slot,
                    context=context,
                )
            except json.JSONDecodeError:
                repaired = original_normalized
            if should_accept_generic_slot_repair(original_normalized, repaired):
                normalized = repaired
            else:
                normalized = reject_unrepaired_generic_slot(original_normalized)
        proposal_path = output_dir / "proposal.json"
        markdown_path = output_dir / "proposal.md"
        changed_files: list[str] = []
        if spec.apply_changes and normalized.get("changes"):
            changed_files = apply_code_edit_proposal(
                proposal=normalized,
                worktree_path=Path(spec.worktree_path),
                context=context,
            )
        normalized["changed_files"] = changed_files
        proposal_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(render_code_edit_markdown(normalized), encoding="utf-8")

        return WorkerResult(
            status="applied" if changed_files else "proposal_created",
            changed_files=changed_files,
            summary=str(normalized.get("summary") or normalized.get("strategy_intent") or "DeepSeek slot proposal created."),
            raw_log_path=str(raw_path),
            artifacts={
                "output_dir": str(output_dir),
                "proposal": str(proposal_path),
                "proposal_markdown": str(markdown_path),
            },
        )

    def _slot_prompt(self, *, context: dict[str, Any], current_slot: str, max_steps: int) -> str:
        return f"""
We are evolving a standard FJSP AWLS local-search solver under a fixed evaluator.

You may modify exactly one code slot: `{SLOT_RELATIVE_PATH}` between
`{EVOLVE_START}` and `{EVOLVE_END}`. The solver will call:

    safe_evolved_zi(values)

where `values` contains:
{", ".join(sorted(ALLOWED_VALUE_KEYS))}

Return JSON only:
{{
  "summary": "short description",
  "strategy_intent": "natural-language rule idea before code",
  "rule_operator_hypotheses": [
    "hypothesis about why this zi rule should improve makespan"
  ],
  "function_code": "def evolved_zi(values: dict[str, float]) -> float:\\n    ...",
  "quick_test_plan": "python -m compileall examples/awls_evolved_slots.py examples/standard_fjsp_awls_solver.py",
  "risk_notes": ["short risk note"]
}}

Rules:
- Maximum internal design/edit steps requested by Core: {max_steps}.
- Return a complete Python function named exactly `evolved_zi`.
- Do not import modules, read files, write files, call subprocesses, or use randomness.
- Use only numeric expressions, if/else, local variables, and values.get("key", default).
- The function must return a finite non-negative float. The wrapper will clamp it.
- Keep the function short; target 10 to 35 lines.
- Do not claim benchmark success; the evaluator will decide.

Current slot file:
```python
{current_slot[-9000:]}
```

Context packet excerpt:
```json
{json.dumps(compact_context(context), ensure_ascii=False, indent=2)[:9000]}
```
""".strip()

    def _generic_slot_prompt(self, *, context: dict[str, Any], slot: dict[str, Any], max_steps: int) -> str:
        slot_id = str(slot.get("slot_id", ""))
        target_file = str(slot.get("target_file", ""))
        failure_memory = selected_slot_failure_memory(context, slot, max_items=12)
        return f"""
We are evolving one confirmed code slot under a fixed evaluator. You may modify
exactly this slot and nothing else:

- slot_id: `{slot_id}`
- target_file: `{target_file}`
- marker_start: `{slot.get('marker_start')}`
- marker_end: `{slot.get('marker_end')}`
- purpose: {slot.get('purpose')}

Slot IO contract:
- Inputs: {json.dumps(slot.get('inputs') or [], ensure_ascii=False)}
- Outputs: {json.dumps(slot.get('outputs') or [], ensure_ascii=False)}
- Invariants: {json.dumps(slot.get('invariants') or [], ensure_ascii=False)}
- Allowed edits: {json.dumps(slot.get('allowed_edits') or [], ensure_ascii=False)}
- Forbidden edits: {json.dumps(slot.get('forbidden_edits') or [], ensure_ascii=False)}

Selected-slot failure memory:
{json.dumps(failure_memory, ensure_ascii=False, indent=2)}

Return JSON only:
{{
  "summary": "one paragraph summary",
  "strategy_intent": "natural-language strategy before editing code",
  "rule_operator_hypotheses": [
    {{
      "name": "unique_rule_or_operator_name",
      "type": "local_search_operator",
      "novelty": "how this differs from prior failed or baseline behavior",
      "expected_effect": "which evaluator metric should improve and why",
      "evidence_used": ["slot_manifest", "knowledge_cards", "loop_feedback"],
      "target_files": ["{target_file}"],
      "ablation_plan": "how Core can isolate this slot change"
    }}
  ],
  "changes": [
    {{
      "action": "replace_slot_block",
      "slot_id": "{slot_id}",
      "content": "replacement code between marker_start and marker_end only",
      "rationale": "why this slot replacement helps"
    }}
  ],
  "context_usage": {{
    "used_project_intake": true,
    "referenced_files": ["{target_file}"],
    "notes": "which slot IO/knowledge evidence shaped the change"
  }},
  "quick_test_plan": "one or more validation commands",
  "risk_notes": ["risk 1"]
}}

Rules:
- Maximum internal design/edit steps requested by Core: {max_steps}.
- Return exactly one `replace_slot_block` change for `{slot_id}`, or an empty
  changes list with risk_notes if no safe edit is possible.
- `content` must contain only replacement code inside the slot markers. Do not
  include marker lines, the whole file, Markdown fences, imports, parser edits,
  evaluator edits, benchmark edits, or output-schema changes.
- Preserve the slot inputs, outputs, invariants, and surrounding function
  control flow. The fixed evaluator will decide success.
- For FJSP-SDST/AWLS slots, score remains makespan only; LB/UB are diagnostic.
- Prefer bounded candidate-generation or ranking changes over broad rewrites.
- Before writing code, use selected-slot failure memory and loop_feedback as
  negative evidence.  The `novelty` field for every hypothesis must name the
  failed idea class it avoids or materially changes.
- Do not retry an avoided failed pattern unchanged.  If a prior rolled-back
  idea is revisited, explain the concrete technical difference in novelty and
  keep the changed code inside this single slot.

Current slot context:
```python
{slot_context_for_prompt(slot, max_chars=12000)}
```

Context packet excerpt:
```json
{json.dumps(compact_context(context), ensure_ascii=False, indent=2)[:12000]}
```
""".strip()

    def _repair_slot_json(self, client: DeepSeekClient, raw: str, error: str, *, max_tokens: int) -> str:
        return client.chat(
            [
                {
                    "role": "system",
                    "content": "Repair invalid JSON for the AWLS slot proposal. Return JSON only.",
                },
                {
                    "role": "user",
                    "content": (
                        "Use exactly these keys: summary, strategy_intent, rule_operator_hypotheses, "
                        "function_code, quick_test_plan, risk_notes.\n\n"
                        f"JSON error: {error}\n\nInvalid response:\n{raw[:9000]}"
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=max_tokens,
            json_mode=True,
        )

    def _repair_generic_slot_json(self, client: DeepSeekClient, raw: str, error: str, *, max_tokens: int) -> str:
        return client.chat(
            [
                {
                    "role": "system",
                    "content": "Repair invalid JSON for the slot-block proposal. Return JSON only.",
                },
                {
                    "role": "user",
                    "content": (
                        "Use exactly these keys: summary, strategy_intent, rule_operator_hypotheses, "
                        "changes, context_usage, quick_test_plan, risk_notes. Each change must use "
                        "replace_slot_block(slot_id, content). Return JSON only.\n\n"
                        f"JSON error: {error}\n\nInvalid response:\n{raw[:9000]}"
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=max_tokens,
            json_mode=True,
        )

    def _repair_generic_slot_proposal_prompt(
        self,
        *,
        prompt: str,
        proposal: dict[str, Any],
        slot: dict[str, Any],
    ) -> str:
        audit = proposal.get("proposal_audit") if isinstance(proposal.get("proposal_audit"), dict) else {}
        warnings = audit.get("warnings") if isinstance(audit, dict) else []
        return f"""
Your previous slot proposal was syntactically valid JSON but failed the platform
semantic proposal gate.

Rejected warnings:
{json.dumps(warnings or [], ensure_ascii=False, indent=2)}

The selected slot is `{slot.get('slot_id')}` in `{slot.get('target_file')}`.
If a safe edit is possible, return exactly one `replace_slot_block` change with
a natural-language hypothesis and novelty that references failure memory.  If
no safe edit is possible, return an empty `changes` list only with non-empty
`risk_notes` explaining the concrete blocker.

Slot-specific repair guidance:
{generic_slot_repair_guidance(slot)}

Available same-machine exact-trial API, when `awls_sdst_same_machine_evaluation`
is the selected slot:
```python
legacy = ...  # existing local score inside the slot
if not schedule.index.instance.has_sequence_dependent_setup:
    return legacy
try:
    trial = schedule.clone()
    trial.apply_move(move)
    return float(trial.makespan) + 0.001 * float(legacy)
except (ValueError, KeyError, IndexError):
    return legacy
```

Previous normalized proposal:
```json
{json.dumps(proposal, ensure_ascii=False, indent=2)[:6000]}
```

Current slot context:
```python
{slot_context_for_prompt(slot, max_chars=9000)}
```

Original instructions:
{prompt[:12000]}
""".strip()

    def _normalize_slot_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]:
        summary = str(proposal.get("summary", ""))[:3000]
        strategy_intent = str(proposal.get("strategy_intent", ""))[:3000]
        hypotheses = proposal.get("rule_operator_hypotheses", [])
        if not isinstance(hypotheses, list):
            hypotheses = [str(hypotheses)]
        function_code = normalize_function_code(str(proposal.get("function_code", "")))
        rejected: list[str] = []
        try:
            validate_slot_function(function_code)
        except ValueError as exc:
            rejected.append(str(exc))
        risk_notes = proposal.get("risk_notes", [])
        if isinstance(risk_notes, str):
            risk_notes = [risk_notes]
        if not isinstance(risk_notes, list):
            risk_notes = []
        return {
            "summary": summary,
            "strategy_intent": strategy_intent,
            "rule_operator_hypotheses": [str(item)[:1000] for item in hypotheses],
            "function_code": function_code,
            "target_file": SLOT_RELATIVE_PATH,
            "rejected": rejected,
            "quick_test_plan": str(proposal.get("quick_test_plan", ""))[:2000],
            "risk_notes": [str(item)[:1000] for item in risk_notes],
        }

    def _normalize_generic_slot_proposal(
        self,
        proposal: dict[str, Any],
        slot: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        slot_id = str(slot.get("slot_id", ""))
        target_file = str(slot.get("target_file", ""))
        changes: list[dict[str, str]] = []
        rejected_changes: list[dict[str, str]] = []
        for item in proposal.get("changes", []):
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("replace_slot_block"), dict):
                nested = dict(item["replace_slot_block"])
                item = {
                    **nested,
                    "action": "replace_slot_block",
                    "rationale": item.get("rationale", nested.get("rationale", "")),
                }
            action = str(item.get("action", "") or "replace_slot_block").strip()
            proposed_slot_id = str(item.get("slot_id", slot_id)).strip()
            content = item.get("content", item.get("replacement"))
            if action != "replace_slot_block":
                rejected_changes.append(
                    {"path": str(item.get("path", target_file)), "reason": "generic slot worker only accepts replace_slot_block"}
                )
                continue
            if proposed_slot_id != slot_id:
                rejected_changes.append({"path": target_file, "reason": f"slot_id must be {slot_id!r}"})
                continue
            if not isinstance(content, str) or not content.strip():
                rejected_changes.append({"path": target_file, "reason": "replace_slot_block requires non-empty string content"})
                continue
            changes.append(
                {
                    "path": target_file,
                    "action": "replace_slot_block",
                    "slot_id": slot_id,
                    "content": normalize_generic_slot_content(content, slot),
                    "rationale": str(item.get("rationale", ""))[:2000],
                }
            )
        if len(changes) > 1:
            rejected_changes.extend(
                {"path": item["path"], "reason": "generic slot worker accepts only one slot replacement"}
                for item in changes[1:]
            )
            changes = changes[:1]

        hypotheses = proposal.get("rule_operator_hypotheses") or []
        if not isinstance(hypotheses, list):
            hypotheses = [hypotheses]
        risk_notes = proposal.get("risk_notes") or []
        if isinstance(risk_notes, str):
            risk_notes = [risk_notes]
        if not isinstance(risk_notes, list):
            risk_notes = []
        return {
            "summary": str(proposal.get("summary", ""))[:3000],
            "strategy_intent": str(proposal.get("strategy_intent", ""))[:3000],
            "rule_operator_hypotheses": [
                normalize_hypothesis(item, target_file) for item in hypotheses if isinstance(item, dict)
            ],
            "changes": changes,
            "rejected_changes": rejected_changes,
            "context_usage": normalize_context_usage(proposal.get("context_usage"), target_file),
            "quick_test_plan": str(proposal.get("quick_test_plan", ""))[:2000],
            "risk_notes": [str(item)[:1000] for item in risk_notes],
            "proposal_audit": build_generic_slot_audit(
                proposal=proposal,
                normalized_changes=changes,
                rejected_changes=rejected_changes,
                context=context or {},
                slot=slot,
            ),
        }


def compact_context(context: dict[str, Any]) -> dict[str, Any]:
    task = context.get("task", {})
    contract = context.get("contract", {})
    docs = context.get("docs", [])
    slot_manifest = context.get("slot_manifest") or {}
    slots = slot_manifest.get("slots") if isinstance(slot_manifest, dict) else []
    if not isinstance(slots, list):
        slots = []
    selected_slot, _error = selected_confirmed_slot(context)
    return {
        "task": task,
        "problem_family_capability": context.get("problem_family_capability") or {},
        "objectives": contract.get("objectives", []),
        "instances": contract.get("instances", [])[:3],
        "commands": contract.get("commands", {}),
        "evaluator_protocol": context.get("evaluator_protocol") or {},
        "slot_manifest": {
            "status": slot_manifest.get("status") if isinstance(slot_manifest, dict) else None,
            "confirmation_required": slot_manifest.get("confirmation_required") if isinstance(slot_manifest, dict) else None,
            "selected_slot": selected_slot,
        },
        "docs": docs[:2],
        "knowledge_cards": prioritize_knowledge_cards_for_slot(context, selected_slot, limit=6),
        "selected_slot_failure_memory": selected_slot_failure_memory(context, selected_slot, max_items=8)
        if selected_slot
        else {},
        "previous_evidence": context.get("previous_evidence", [])[:4],
        "loop_feedback": context.get("loop_feedback", {}),
        "hypothesis": context.get("hypothesis", ""),
    }


def extract_generic_slot_proposal(text: str) -> dict[str, Any]:
    """Extract a top-level generic slot proposal, not an arbitrary nested JSON object."""

    proposal = extract_json_object(text)
    if is_generic_slot_proposal_shape(proposal):
        return proposal
    raise json.JSONDecodeError(
        "top-level JSON object must include generic slot proposal keys",
        text,
        0,
    )


def is_generic_slot_proposal_shape(proposal: dict[str, Any]) -> bool:
    return any(key in proposal for key in ("summary", "strategy_intent", "changes", "risk_notes", "context_usage"))


def should_accept_generic_slot_repair(original: dict[str, Any], repaired: dict[str, Any]) -> bool:
    """Keep semantic repair only when it makes proposal quality no worse."""

    original_changes = original.get("changes") if isinstance(original.get("changes"), list) else []
    repaired_changes = repaired.get("changes") if isinstance(repaired.get("changes"), list) else []
    if generic_slot_has_must_repair_warning(original):
        return bool(repaired_changes) and not generic_slot_has_must_repair_warning(repaired)
    if original_changes and not repaired_changes:
        return False
    if repaired_changes and not original_changes:
        return True
    return generic_repair_warning_count(repaired) <= generic_repair_warning_count(original)


def reject_unrepaired_generic_slot(proposal: dict[str, Any]) -> dict[str, Any]:
    rejected = dict(proposal)
    rejected["changes"] = []
    risk_notes = list(rejected.get("risk_notes") or [])
    risk_notes.append("Semantic repair did not produce an acceptable replacement for a must-repair slot warning.")
    rejected["risk_notes"] = risk_notes
    audit = dict(rejected.get("proposal_audit") or {})
    audit["accepted_change_count"] = 0
    audit["accepted_change_paths"] = []
    warnings = list(audit.get("warnings") or [])
    if "unrepaired_must_repair_warning" not in warnings:
        warnings.append("unrepaired_must_repair_warning")
    audit["warnings"] = warnings
    rejected["proposal_audit"] = audit
    return rejected


def generic_slot_has_must_repair_warning(proposal: dict[str, Any]) -> bool:
    audit = proposal.get("proposal_audit")
    if not isinstance(audit, dict):
        return False
    warnings = audit.get("warnings")
    if not isinstance(warnings, list):
        return False
    must_repair = {
        "same_machine_setup_propagation_without_exact_trial",
        "slot_uses_nonexistent_operation_index_start_node",
        "slot_uses_nonexistent_operation_index_durations",
        "slot_uses_nonexistent_setup_time_api",
        "neighborhood_adds_random_no_move_fallback",
        "neighborhood_adds_setup_no_move_fallback",
        "neighborhood_gates_change_machine_on_empty_same_moves",
        "neighborhood_retries_failed_near_critical_threshold",
        "neighborhood_retries_failed_same_machine_window",
        "neighborhood_retries_failed_tight_tardiness_filter",
        "neighborhood_retries_latest_block_topk_overpruning",
        "neighborhood_retries_global_move_count_cap",
        "neighborhood_retries_unordered_candidate_machine_cap",
        "neighborhood_retries_random_diversity_sampling",
        "neighborhood_retries_random_change_only_lane",
        "neighborhood_shuffles_candidate_machine_dict",
        "portfolio_retries_seed_mapping_only",
        "portfolio_retries_best_lane_rerun",
        "portfolio_retries_subrun_seed_splitting",
        "portfolio_retries_setup_ratio_best_lane_exploitation",
        "portfolio_missing_lane_summaries_initialization",
        "same_machine_retries_pure_exact_trial",
        "same_machine_retries_legacy_ratio_exact_gate",
        "same_machine_retries_exact_setup_tiebreak",
        "same_machine_retries_noncritical_worsening_exact_gate",
        "same_machine_uses_nonexistent_move_node",
        "same_machine_uses_end_node_end_time_as_makespan",
        "same_machine_uses_nonexistent_awls_trial",
        "initialization_retries_append_only_setup_completion",
        "initialization_retries_low_setup_tiebreak",
        "initialization_regret_label_without_second_best",
        "initialization_retries_max_regret_append_dispatch",
        "initialization_retries_regret_roulette_append_dispatch",
        "initialization_retries_tail_ratio_regret_append_dispatch",
        "initialization_missing_required_topology_or_repair",
        "initialization_non_append_without_acyclic_guard",
        "initialization_rebuilds_ready_after_committed_insert",
        "initialization_retries_static_bottleneck_ignores_setup",
        "initialization_defines_wrong_entrypoint",
        "initialization_uses_nonexistent_instance_api",
        "move_evaluation_retries_proxy_ratio_exact_gate",
        "move_evaluation_retries_critical_proximity_setup_penalty",
        "move_evaluation_uses_end_node_end_time_as_makespan",
        "move_selection_generates_candidates",
        "move_selection_mutates_candidate_lists",
        "move_selection_mutates_schedule_directly",
        "move_selection_retries_small_best_moves_exact_recheck",
        "move_selection_retries_global_setup_sum_tiebreak",
        "move_selection_retries_random_noise_escape",
        "move_selection_uses_invalid_setup_time_between_signature",
        "move_selection_uses_nonexistent_node_to_operation_key",
        "move_selection_uses_nonexistent_operations_api",
        "move_selection_uses_dict_get_on_schedule_lists",
        "move_selection_misinterprets_move_key_shape",
        "move_selection_uses_where_node_as_machine_id",
        "move_selection_trial_apply_without_clone",
        "weight_update_calls_forbidden_runtime_api",
        "weight_update_mutates_schedule_structure",
        "weight_update_retries_sdst_improvement_weight_decay",
        "weight_update_uses_random_or_io",
        "search_transition_calls_forbidden_runtime_api",
        "search_transition_mutates_schedule_structure",
        "search_transition_promotes_worse_best",
        "search_transition_stats_without_none_guard",
        "search_transition_uses_io_or_unseeded_random",
        "search_transition_retries_relative_degradation_best_reset",
        "tabu_memory_missing_or_multiple_tabu_add",
        "tabu_memory_calls_forbidden_runtime_api",
        "tabu_memory_mutates_schedule_or_tabu_directly",
        "tabu_memory_uses_nonexistent_api",
        "tabu_memory_uses_setup_without_import",
        "tabu_memory_uses_io_or_unseeded_random",
        "tabu_memory_retries_short_front_back_sequence",
        "tabu_memory_retries_expanded_critical_fraction_sequence",
        "tabu_memory_retries_target_machine_change_tabu",
        "all_slot_changes_rejected",
        "slot_change_rejected_wrong_slot_id",
        "slot_content_python_syntax_error",
    }
    return any(str(item) in must_repair for item in warnings)


def generic_repair_warning_count(proposal: dict[str, Any]) -> int:
    audit = proposal.get("proposal_audit")
    if not isinstance(audit, dict):
        return 0
    warnings = audit.get("warnings")
    return len(warnings) if isinstance(warnings, list) else 0


def selected_slot_failure_memory(
    context: dict[str, Any],
    selected_slot: dict[str, Any] | None,
    *,
    max_items: int,
) -> dict[str, Any]:
    """Extract compact negative evidence for the selected slot.

    This is prompt context only.  It helps the coding worker avoid stale
    no-improvement ideas, while Core evaluator promotion remains unchanged.
    """

    if selected_slot is None:
        return {"status": "missing_slot", "avoid_patterns": [], "rolled_back_rounds": []}
    cards = prioritize_knowledge_cards_for_slot(context, selected_slot, limit=6)
    avoid_patterns: list[dict[str, str]] = []
    for card in cards:
        snippet = str(card.get("snippet") or "")
        path = str(card.get("path") or "")
        for line in extract_negative_memory_lines(snippet, limit=max_items):
            avoid_patterns.append({"source": path, "evidence": line})
            if len(avoid_patterns) >= max_items:
                break
        if len(avoid_patterns) >= max_items:
            break

    rolled_back_rounds: list[dict[str, Any]] = []
    feedback = context.get("loop_feedback") if isinstance(context.get("loop_feedback"), dict) else {}
    for item in feedback.get("previous_rounds") or []:
        if not isinstance(item, dict) or item.get("decision") != "rolled_back":
            continue
        diagnostics = item.get("proposal_diagnostics") if isinstance(item.get("proposal_diagnostics"), dict) else {}
        rolled_back_rounds.append(
            {
                "round_index": item.get("round_index"),
                "candidate_key": item.get("candidate_key"),
                "summary": str(diagnostics.get("summary") or "")[:260],
                "strategy_intent": str(diagnostics.get("strategy_intent") or "")[:260],
                "hypotheses": [
                    {
                        "name": hypothesis.get("name"),
                        "type": hypothesis.get("type"),
                        "novelty": str(hypothesis.get("novelty") or "")[:220],
                    }
                    for hypothesis in (diagnostics.get("rule_operator_hypotheses") or [])[:4]
                    if isinstance(hypothesis, dict)
                ],
            }
        )
        if len(rolled_back_rounds) >= max_items:
            break

    return {
        "status": "available" if avoid_patterns or rolled_back_rounds else "empty",
        "slot_id": str(selected_slot.get("slot_id") or ""),
        "instruction": (
            "Treat these as negative evidence.  Do not retry an avoided pattern "
            "unchanged; novelty must explain a concrete technical difference."
        ),
        "avoid_patterns": avoid_patterns,
        "rolled_back_rounds": rolled_back_rounds,
    }


def extract_negative_memory_lines(text: str, *, limit: int) -> list[str]:
    cues = (
        "do not",
        "failed",
        "worsen",
        "worse",
        "rolled back",
        "rolled-back",
        "did not improve",
        "did not beat",
        "tied",
        "crashed",
        "invalid",
        "avoid",
        "不要",
        "失败",
        "变差",
        "回退",
    )
    lines: list[str] = []
    current_bullet: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            if current_bullet:
                _append_negative_line(lines, " ".join(current_bullet), cues, limit)
                current_bullet = []
            continue
        if stripped.startswith(("-", "*")):
            if current_bullet:
                _append_negative_line(lines, " ".join(current_bullet), cues, limit)
            current_bullet = [stripped.lstrip("-* ").strip()]
        elif current_bullet and (raw_line.startswith(" ") or raw_line.startswith("\t")):
            current_bullet.append(stripped)
        else:
            if current_bullet:
                _append_negative_line(lines, " ".join(current_bullet), cues, limit)
                current_bullet = []
            _append_negative_line(lines, stripped, cues, limit)
        if len(lines) >= limit:
            return lines[:limit]
    if current_bullet and len(lines) < limit:
        _append_negative_line(lines, " ".join(current_bullet), cues, limit)
    return lines[:limit]


def _append_negative_line(lines: list[str], text: str, cues: tuple[str, ...], limit: int) -> None:
    normalized = " ".join(text.split())
    if not normalized or len(lines) >= limit:
        return
    lower = normalized.lower()
    if any(cue in lower for cue in cues):
        lines.append(normalized[:700])


def build_generic_slot_audit(
    *,
    proposal: dict[str, Any],
    normalized_changes: list[dict[str, str]],
    rejected_changes: list[dict[str, str]],
    context: dict[str, Any],
    slot: dict[str, Any],
) -> dict[str, Any]:
    failure_memory = selected_slot_failure_memory(context, slot, max_items=10)
    hypotheses = proposal.get("rule_operator_hypotheses") or []
    if not isinstance(hypotheses, list):
        hypotheses = []
    novelty_text = "\n".join(
        str(item.get("novelty") or "") for item in hypotheses if isinstance(item, dict)
    ).lower()
    risk_notes = proposal.get("risk_notes") or []
    if isinstance(risk_notes, str):
        risk_notes = [risk_notes]
    if not isinstance(risk_notes, list):
        risk_notes = []
    has_risk_notes = any(str(item).strip() for item in risk_notes)
    risk_text = "\n".join(str(item) for item in risk_notes).lower()
    summary_text = str(proposal.get("summary") or "").lower()
    intent_text = str(proposal.get("strategy_intent") or "").lower()
    warnings: list[str] = []
    rejected_reasons = " ".join(str(item.get("reason") or "") for item in rejected_changes if isinstance(item, dict)).lower()
    if rejected_changes and not normalized_changes:
        warnings.append("all_slot_changes_rejected")
    if "slot_id must be" in rejected_reasons:
        warnings.append("slot_change_rejected_wrong_slot_id")
    if not normalized_changes and not rejected_changes and not has_risk_notes:
        warnings.append("empty_slot_proposal_without_risk_note")
    if not normalized_changes and not rejected_changes and has_risk_notes:
        if not risk_notes_describe_concrete_blocker(risk_text):
            warnings.append("empty_slot_proposal_without_concrete_blocker")
        if any(cue in f"{summary_text}\n{intent_text}\n{risk_text}" for cue in ("revert", "original", "baseline", "回退", "原始", "基线")):
            warnings.append("empty_slot_proposal_reverts_to_baseline")
    if normalized_changes and not hypotheses:
        warnings.append("missing_rule_operator_hypotheses")
    avoid_patterns = failure_memory.get("avoid_patterns") or []
    if normalized_changes and avoid_patterns and not any(
        cue in novelty_text
        for cue in ("avoid", "failed", "worsen", "rolled", "different", "material", "instead", "not retry", "失败", "变差")
    ):
        warnings.append("novelty_does_not_reference_failure_memory")
    warnings.extend(slot_specific_generic_warnings(slot, normalized_changes, context=context))
    if len(normalized_changes) > 1:
        warnings.append("generic_slot_accepts_only_one_change")
    return {
        "slot_id": str(slot.get("slot_id") or ""),
        "target_file": str(slot.get("target_file") or ""),
        "accepted_change_count": len(normalized_changes),
        "rejected_change_count": len(rejected_changes),
        "accepted_change_paths": [str(item.get("path") or "") for item in normalized_changes],
        "failure_memory_status": failure_memory.get("status"),
        "avoid_pattern_count": len(avoid_patterns),
        "rolled_back_round_count": len(failure_memory.get("rolled_back_rounds") or []),
        "operator_lineage": {
            "hypothesis_count": len([item for item in hypotheses if isinstance(item, dict)]),
            "hypothesis_names": [
                str(item.get("name") or "")[:120] for item in hypotheses if isinstance(item, dict)
            ][:8],
        },
        "warnings": warnings,
    }


def generic_slot_needs_repair(proposal: dict[str, Any]) -> bool:
    audit = proposal.get("proposal_audit")
    if not isinstance(audit, dict):
        return False
    warnings = audit.get("warnings")
    if not isinstance(warnings, list):
        return False
    repair_warnings = {
        "empty_slot_proposal_without_risk_note",
        "empty_slot_proposal_without_concrete_blocker",
        "empty_slot_proposal_reverts_to_baseline",
        "same_machine_setup_propagation_without_exact_trial",
        "slot_uses_nonexistent_operation_index_start_node",
        "slot_uses_nonexistent_operation_index_durations",
        "slot_uses_nonexistent_setup_time_api",
        "neighborhood_adds_random_no_move_fallback",
        "neighborhood_adds_setup_no_move_fallback",
        "neighborhood_gates_change_machine_on_empty_same_moves",
        "neighborhood_retries_failed_near_critical_threshold",
        "neighborhood_retries_failed_same_machine_window",
        "neighborhood_retries_failed_tight_tardiness_filter",
        "neighborhood_retries_latest_block_topk_overpruning",
        "neighborhood_retries_global_move_count_cap",
        "neighborhood_retries_unordered_candidate_machine_cap",
        "neighborhood_retries_random_diversity_sampling",
        "neighborhood_retries_random_change_only_lane",
        "neighborhood_shuffles_candidate_machine_dict",
        "portfolio_retries_seed_mapping_only",
        "portfolio_retries_best_lane_rerun",
        "portfolio_retries_subrun_seed_splitting",
        "portfolio_retries_setup_ratio_best_lane_exploitation",
        "portfolio_missing_lane_summaries_initialization",
        "same_machine_retries_pure_exact_trial",
        "same_machine_retries_legacy_ratio_exact_gate",
        "same_machine_retries_exact_setup_tiebreak",
        "same_machine_retries_noncritical_worsening_exact_gate",
        "same_machine_uses_nonexistent_move_node",
        "same_machine_uses_end_node_end_time_as_makespan",
        "same_machine_uses_nonexistent_awls_trial",
        "initialization_retries_append_only_setup_completion",
        "initialization_retries_low_setup_tiebreak",
        "initialization_regret_label_without_second_best",
        "initialization_retries_max_regret_append_dispatch",
        "initialization_retries_regret_roulette_append_dispatch",
        "initialization_retries_tail_ratio_regret_append_dispatch",
        "initialization_missing_required_topology_or_repair",
        "initialization_non_append_without_acyclic_guard",
        "initialization_rebuilds_ready_after_committed_insert",
        "initialization_retries_static_bottleneck_ignores_setup",
        "initialization_defines_wrong_entrypoint",
        "initialization_uses_nonexistent_instance_api",
        "move_evaluation_retries_proxy_ratio_exact_gate",
        "move_evaluation_retries_critical_proximity_setup_penalty",
        "move_evaluation_uses_end_node_end_time_as_makespan",
        "move_selection_generates_candidates",
        "move_selection_mutates_candidate_lists",
        "move_selection_mutates_schedule_directly",
        "move_selection_retries_small_best_moves_exact_recheck",
        "move_selection_retries_global_setup_sum_tiebreak",
        "move_selection_retries_random_noise_escape",
        "move_selection_uses_invalid_setup_time_between_signature",
        "move_selection_uses_nonexistent_node_to_operation_key",
        "move_selection_uses_nonexistent_operations_api",
        "move_selection_uses_dict_get_on_schedule_lists",
        "move_selection_misinterprets_move_key_shape",
        "move_selection_uses_where_node_as_machine_id",
        "move_selection_trial_apply_without_clone",
        "weight_update_calls_forbidden_runtime_api",
        "weight_update_mutates_schedule_structure",
        "weight_update_retries_sdst_improvement_weight_decay",
        "weight_update_uses_random_or_io",
        "search_transition_calls_forbidden_runtime_api",
        "search_transition_mutates_schedule_structure",
        "search_transition_promotes_worse_best",
        "search_transition_stats_without_none_guard",
        "search_transition_uses_io_or_unseeded_random",
        "search_transition_retries_relative_degradation_best_reset",
        "tabu_memory_missing_or_multiple_tabu_add",
        "tabu_memory_calls_forbidden_runtime_api",
        "tabu_memory_mutates_schedule_or_tabu_directly",
        "tabu_memory_uses_nonexistent_api",
        "tabu_memory_uses_setup_without_import",
        "tabu_memory_uses_io_or_unseeded_random",
        "tabu_memory_retries_short_front_back_sequence",
        "tabu_memory_retries_expanded_critical_fraction_sequence",
        "tabu_memory_retries_target_machine_change_tabu",
        "all_slot_changes_rejected",
        "slot_change_rejected_wrong_slot_id",
        "slot_content_python_syntax_error",
        "zi_features_slot_inert_under_current_zi_policy",
    }
    return any(str(item) in repair_warnings for item in warnings)


def slot_specific_generic_warnings(
    slot: dict[str, Any],
    changes: list[dict[str, str]],
    *,
    context: dict[str, Any] | None = None,
) -> list[str]:
    """Detect known failed idea classes that generic novelty text can miss."""

    slot_id = str(slot.get("slot_id") or "")
    content = "\n".join(str(item.get("content") or "") for item in changes).lower()
    if not content:
        return []
    warnings: list[str] = []
    if python_slot_content_syntax_error(content):
        warnings.append("slot_content_python_syntax_error")
    if re.search(r"\b(?:schedule\.)?index\.start_node\b", content):
        warnings.append("slot_uses_nonexistent_operation_index_start_node")
    if re.search(r"schedule\.index\.durations\b|\bindex\.durations\b", content):
        warnings.append("slot_uses_nonexistent_operation_index_durations")
    if re.search(r"\b(?:schedule(?:\.index)?|index)\.setup_time\b", content):
        warnings.append("slot_uses_nonexistent_setup_time_api")
    if slot_id == "awls_sdst_initialization":
        return warnings + awls_sdst_initialization_warnings(content, context=context)
    if slot_id == "awls_sdst_move_evaluation":
        return warnings + awls_sdst_move_evaluation_warnings(content)
    if slot_id == "awls_sdst_neighborhood_selection":
        return warnings + awls_sdst_neighborhood_selection_warnings(content)
    if slot_id == "awls_sdst_move_selection":
        return warnings + awls_sdst_move_selection_warnings(content)
    if slot_id == "awls_sdst_portfolio_search_control":
        return warnings + awls_sdst_portfolio_search_control_warnings(content)
    if slot_id == "awls_sdst_weight_update":
        return warnings + awls_sdst_weight_update_warnings(content)
    if slot_id == "awls_sdst_search_transition":
        return warnings + awls_sdst_search_transition_warnings(content)
    if slot_id == "awls_sdst_tabu_memory":
        return warnings + awls_sdst_tabu_memory_warnings(content)
    if slot_id == "awls_sdst_zi_features":
        return warnings + awls_sdst_zi_features_warnings(context=context)
    if slot_id != "awls_sdst_same_machine_evaluation":
        return warnings
    uses_setup_propagation = "setup_time_between" in content and ("new_r" in content or "new_q" in content)
    uses_exact_trial = ".clone(" in content and ".apply_move(" in content and "trial.makespan" in content
    if uses_setup_propagation and not uses_exact_trial:
        warnings.append("same_machine_setup_propagation_without_exact_trial")
    if re.search(r"\bmove\.node\b", content):
        warnings.append("same_machine_uses_nonexistent_move_node")
    if re.search(r"\bawlstrial\b", content):
        warnings.append("same_machine_uses_nonexistent_awls_trial")
    if re.search(r"\bend_time\s*\[\s*(?:schedule\.)?index\.end_node\s*\]", content):
        warnings.append("same_machine_uses_end_node_end_time_as_makespan")
    if re.search(r"\bschedule\.end_time\s*\[\s*schedule\.index\.end_node\s*\]", content):
        warnings.append("same_machine_uses_end_node_end_time_as_makespan")
    if uses_exact_trial and "setup_time_between" not in content and re.search(r"0\.00?1\s*\*\s*float\(legacy\)", content):
        warnings.append("same_machine_retries_pure_exact_trial")
    legacy_ratio_exact_gate = (
        uses_exact_trial
        and "setup_time_between" not in content
        and re.search(r"\blegacy\s*<=\s*schedule\.makespan\s*\*\s*1\.1\b", content)
        and re.search(r"0\.00?1\s*\*\s*float\(legacy\)", content)
    )
    if legacy_ratio_exact_gate:
        warnings.append("same_machine_retries_legacy_ratio_exact_gate")
    setup_tiebreak_only = (
        uses_exact_trial
        and "setup_time_between" in content
        and ("total_setup" in content or "block_setup" in content or "setup_sum" in content)
        and re.search(r"0\.00?1\s*\*\s*(?:float\()?[\w_]*setup", content)
    )
    if setup_tiebreak_only:
        warnings.append("same_machine_retries_exact_setup_tiebreak")
    noncritical_worsening_exact_gate = (
        uses_exact_trial
        and "move.which" in content
        and "backward_path_length" in content
        and "trial_makespan" in content
        and re.search(r"trial_makespan\s*>\s*schedule\.makespan", content)
        and re.search(r"trial_makespan\s*\+=\s*0\.1\s*\*", content)
    )
    if noncritical_worsening_exact_gate:
        warnings.append("same_machine_retries_noncritical_worsening_exact_gate")
    return warnings


def awls_sdst_zi_features_warnings(*, context: dict[str, Any] | None = None) -> list[str]:
    zi_policy = solver_command_zi_policy(context)
    if zi_policy and zi_policy not in {"formula", "slot"}:
        return ["zi_features_slot_inert_under_current_zi_policy"]
    return []


def solver_command_zi_policy(context: dict[str, Any] | None) -> str:
    if not isinstance(context, dict):
        return ""
    evaluator_protocol = context.get("evaluator_protocol")
    if not isinstance(evaluator_protocol, dict):
        return ""
    command = str(evaluator_protocol.get("solver_command_template") or "").lower()
    if not command:
        return ""
    match = re.search(r"--zi-policy(?:=|\s+)([^\s{}]+)", command)
    return match.group(1).strip("\"'") if match else ""


def awls_sdst_move_evaluation_warnings(content: str) -> list[str]:
    warnings: list[str] = []
    uses_exact_trial = ".apply_move(" in content and "trial.makespan" in content
    if re.search(r"\bend_time\s*\[\s*(?:schedule\.)?index\.end_node\s*\]", content):
        warnings.append("move_evaluation_uses_end_node_end_time_as_makespan")
    if re.search(r"\bschedule\.end_time\s*\[\s*schedule\.index\.end_node\s*\]", content):
        warnings.append("move_evaluation_uses_end_node_end_time_as_makespan")
    uses_proxy_ratio_gate = re.search(r"\bbest_proxy\b[\s\S]{0,160}\b1\.0?5\b", content) or re.search(
        r"\b1\.0?5\b[\s\S]{0,160}\bbest_proxy\b", content
    )
    uses_function_attribute_proxy_state = (
        "change_machine_evaluate_parts._best_proxy" in content
        or "_best_proxy_for_schedule" in content
    )
    uses_hard_outside_gate_penalty = re.search(r"\b1e9\b|\b1000000000(?:\.0)?\b", content)
    if (
        uses_exact_trial
        and uses_proxy_ratio_gate
        and uses_function_attribute_proxy_state
        and uses_hard_outside_gate_penalty
    ):
        warnings.append("move_evaluation_retries_proxy_ratio_exact_gate")
    critical_proximity_setup = (
        "critical_factor" in content
        and "setup_sum" in content
        and "base_value" in content
        and re.search(r"\bmin\s*\(\s*1\.0\s*,\s*base_value\s*/", content)
    )
    if critical_proximity_setup:
        warnings.append("move_evaluation_retries_critical_proximity_setup_penalty")
    return warnings


def python_slot_content_syntax_error(content: str) -> bool:
    """Return whether normalized Python slot content is structurally invalid."""

    wrapper = "def __slot_probe__():\n" + "".join(
        f"    {line}" if line.strip() else line for line in content.splitlines(keepends=True)
    )
    try:
        ast.parse(wrapper)
    except SyntaxError:
        return True
    return False


def awls_sdst_initialization_warnings(content: str, *, context: dict[str, Any] | None = None) -> list[str]:
    """Flag repeated SDST initialization proposals that already regressed."""

    warnings: list[str] = []
    uses_setup = "setup_time_between" in content
    appends_to_machine_sequence = bool(
        re.search(r"\bsequences\s*\[[^\]\n]+\]\s*\.append\s*\(", content)
        or re.search(r"\bseq\s*=\s*sequences\s*\[[^\]\n]+\][\s\S]{0,300}\bseq\s*\.append\s*\(", content)
    )
    append_only = appends_to_machine_sequence and not re.search(r"\.insert\(|sequence\[[^\n]+:[^\n]+\]", content)
    has_regret_word = "regret" in content
    has_named_second_best = bool(
        re.search(r"\bsecond[_\s-]*best[_a-z0-9]*\b", content)
        or re.search(r"\bsecond[_\s-]*(?:cost|score|completion|finish|machine)\b", content)
        or re.search(r"\b(?:two_best|best_two|top_two|sorted_costs|candidate_costs)\b", content)
    ) and bool(
        re.search(r"\bregret\s*=", content)
        or re.search(
            r"\b(?:second[_\s-]*best[_a-z0-9]*|second[_\s-]*(?:cost|score|completion|finish)|candidate_costs\[[^\]]*1[^\]]*\])\s*[-]",
            content,
        )
    )
    has_sorted_pair_regret = bool(
        re.search(r"\b(?:completions|costs|candidate_costs|machine_costs)\s*\.sort\s*\(", content)
        and re.search(
            r"\bregret\s*=\s*(?:\w+\s*if\s+)?"
            r"(?:completions|costs|candidate_costs|machine_costs)\s*\[[^\]]*1[^\]]*\][^\n-]*"
            r"-\s*(?:completions|costs|candidate_costs|machine_costs)\s*\[[^\]]*0[^\]]*\]",
            content,
        )
    )
    has_second_best_regret = has_regret_word and (has_named_second_best or has_sorted_pair_regret)
    if has_regret_word and not has_second_best_regret:
        warnings.append("initialization_regret_label_without_second_best")
    setup_completion = uses_setup and ("machine_ready" in content or "setup_ready" in content) and "completion" in content
    if append_only and setup_completion and not has_second_best_regret:
        warnings.append("initialization_retries_append_only_setup_completion")
    low_setup_tie = uses_setup and re.search(r"(setup|setup_time|setup_cost)[^\n]*(?:min|best|tie|sort|key)", content)
    if append_only and low_setup_tie and not has_second_best_regret:
        warnings.append("initialization_retries_low_setup_tiebreak")
    max_regret_append_dispatch = (
        append_only
        and has_second_best_regret
        and re.search(r"\bmax_regret\s*=", content)
        and re.search(r"\b(?:ready_ops|op_priorities)\b", content)
        and re.search(r"\b(?:best_comp|best_cost|best_machine)\b", content)
        and not re.search(
            r"(?:tail|remaining|bottleneck|critical|repair|local[_\s-]*search|awlsschedule|topological_sort)",
            content,
        )
    )
    if max_regret_append_dispatch:
        warnings.append("initialization_retries_max_regret_append_dispatch")
    regret_roulette_append_dispatch = (
        append_only
        and has_second_best_regret
        and re.search(r"\b(?:weights?|total_weight|rng\.choices|roulette|weighted)\b", content)
        and re.search(r"\b(?:candidates|filtered)\b", content)
        and not re.search(
            r"(?:tail|remaining|bottleneck|critical|repair|local[_\s-]*search|awlsschedule|topological_sort)",
            content,
        )
    )
    if regret_roulette_append_dispatch:
        warnings.append("initialization_retries_regret_roulette_append_dispatch")
    tail_ratio_regret_append_dispatch = (
        append_only
        and has_second_best_regret
        and re.search(r"\b(?:remaining|tail)[_\w]*\b", content)
        and re.search(r"\b(?:priority|score|ratio)\s*=\s*[^=\n]*(?:remaining|tail)[_\w]*\s*/", content)
        and re.search(r"\bready_ops\s*\.sort\b|\bsort\(\s*key\s*=", content)
        and not re.search(r"(?:bottleneck|repair|local[_\s-]*search|awlsschedule|topological_sort)", content)
    )
    if tail_ratio_regret_append_dispatch:
        warnings.append("initialization_retries_tail_ratio_regret_append_dispatch")
    if re.search(r"^\s*def\s+awls_sdst_initialization\s*\(", content, re.MULTILINE):
        warnings.append("initialization_defines_wrong_entrypoint")
    nonexistent_instance_api = bool(
        re.search(
            r"\b(?:index\.)?instance\.(?:ops|n_jobs|n_machines|sds_data)\b|"
            r"\bself\.index\b|\bself\.initial_sequences\b|"
            r"\bprocessing_times\b|\beligible_machines\b",
            content,
        )
    )
    if nonexistent_instance_api:
        warnings.append("initialization_uses_nonexistent_instance_api")
    context_text = ""
    if isinstance(context, dict):
        context_text = str(context.get("hypothesis") or "").lower()
    requires_topology_or_repair = bool(
        context_text
        and re.search(
            r"(?:must\s+not\s+be\s+another\s+append|not\s+be\s+another\s+append|"
            r"topology|topological|non-append|nonappend|repair|assignment-then-sequencing|"
            r"separate\s+sequencing|reorder|insert)",
            context_text,
        )
    )
    has_required_topology_or_repair = bool(
        re.search(
            r"\b(?:awlsschedule|topological_sort|validate_standard_schedule)\b|"
            r"\.insert\s*\(|\bswap\b|\brepair\b|\bassignment[_-]?then[_-]?sequencing\b|"
            r"\bseparate\s+sequencing\b|\breorder\b",
            content,
        )
    )
    if requires_topology_or_repair and append_only and not has_required_topology_or_repair:
        warnings.append("initialization_missing_required_topology_or_repair")
    committed_non_append = bool(
        re.search(r"sequences\s*\[[^\n\]]+\]\s*\.insert\s*\(", content)
        or re.search(r"\bseq\s*\.\s*insert\s*\(", content)
    )
    has_real_topology_guard = bool(
        re.search(r"\b(?:topological_sort|validate_standard_schedule|awlsschedule)\b", content)
    )
    rebuilds_ready_after_insert = bool(
        committed_non_append
        and re.search(
            r"for\s+\w+\s+in\s+sequences\s*\[[^\n\]]+\]\s*:[\s\S]{0,900}job_ready\s*\[[^\n]+\]\s*=",
            content,
        )
    )
    if committed_non_append and not has_real_topology_guard:
        warnings.append("initialization_non_append_without_acyclic_guard")
    if rebuilds_ready_after_insert:
        warnings.append("initialization_rebuilds_ready_after_committed_insert")
    static_bottleneck_only = (
        "bottleneck_machine" in content
        and "bottleneck_priority" in content
        and ("machine_loads" in content or "machine_load_count" in content)
        and not uses_setup
    )
    if static_bottleneck_only:
        warnings.append("initialization_retries_static_bottleneck_ignores_setup")
    return list(dict.fromkeys(warnings))


def awls_sdst_neighborhood_selection_warnings(content: str) -> list[str]:
    """Flag repeated SDST neighborhood proposals that already tied or regressed."""

    warnings: list[str] = []
    near_critical_cues = ("near_critical", "near-critical", "near critical", "critical_gap", "slack", "tardiness")
    has_near_critical_cue = any(cue in content for cue in near_critical_cues)
    if "0.99" in content and "makespan" in content and has_near_critical_cue:
        warnings.append("neighborhood_retries_failed_near_critical_threshold")
    if has_near_critical_cue and re.search(
        r"\b(?:\w*_)?(?:window|radius|span|limit|max_offset)\s*=\s*(?:3|10)\b",
        content,
    ):
        warnings.append("neighborhood_retries_failed_same_machine_window")
    if has_near_critical_cue and re.search(r"range\([^)]*(?:[-+]\s*(?:3|10)|(?:3|10)\s*[-+])", content):
        warnings.append("neighborhood_retries_failed_same_machine_window")
    if re.search(r"[<>]=?\s*-?\s*5\b", content) and "tardiness" in content:
        warnings.append("neighborhood_retries_failed_tight_tardiness_filter")
    topk_latest_blocks = (
        re.search(r"\btop_k\s*=\s*[1-5]\b", content)
        and "critical_blocks" in content
        and "exhaustive=false" in content.replace(" ", "")
        and ("end_time" in content or "lateness" in content or "latest" in content)
        and not re.search(r"exhaustive\s*=\s*true", content)
    )
    if topk_latest_blocks:
        warnings.append("neighborhood_retries_latest_block_topk_overpruning")
    global_move_cap = (
        re.search(r"\bMAX_MOVES\s*=\s*(?:50|100|200|300)\b", content)
        or re.search(r"\b(?:max_moves|total_move_limit)\s*=\s*(?:50|100|200|300)\b", content)
    )
    if global_move_cap and "assignment" not in content and "change_machine_window" in content:
        warnings.append("neighborhood_retries_global_move_count_cap")
    candidate_machine_cap = (
        re.search(
            r"\b(?:max_candidate_machines|max_change_machines|max_candidate_machine_count|machine_limit)\s*=\s*[1-5]\b",
            content,
        )
        and re.search(
            r"\bfor\s+\w+\s+in\s+\w+\s*\[\s*:\s*(?:max_candidate_machines|max_change_machines|max_candidate_machine_count|machine_limit)\s*\]\s*:",
            content,
        )
        and "change_machine_window" in content
    )
    ordered_candidate_cap = bool(
        re.search(r"\b(?:bounded_candidates|candidate_list|candidate_machines)\s*=\s*sorted\s*\(", content)
        or re.search(r"\b(?:bounded_candidates|candidate_list|candidate_machines)\.sort\s*\(", content)
    )
    if candidate_machine_cap and not ordered_candidate_cap:
        warnings.append("neighborhood_retries_unordered_candidate_machine_cap")
    random_diversity_sampling = (
        re.search(r"\bmax_blocks\s*=\s*\d+", content)
        and re.search(r"\bmax_same_per_block\s*=\s*\d+", content)
        and re.search(r"\btotal_move_limit\s*=\s*\d+", content)
        and ("shuffle(" in content or ".sample(" in content)
    )
    if random_diversity_sampling:
        warnings.append("neighborhood_retries_random_diversity_sampling")
    random_change_only_lane = (
        re.search(r"\b(?:use_)?change_only\b", content)
        and re.search(r"\bschedule\.rng\.randrange\(\s*100\s*\)\s*<\s*\w+", content)
        and re.search(r"\bif\s+not\s+(?:use_)?change_only\s*:", content)
        and "consider_same" in content
        and "consider_change" in content
    )
    if random_change_only_lane:
        warnings.append("neighborhood_retries_random_change_only_lane")
    if re.search(r"\bschedule\.rng\.shuffle\(\s*schedule\.index\.candidates\[[^\]]+\]\s*\)", content):
        warnings.append("neighborhood_shuffles_candidate_machine_dict")
    if re.search(r"\bcandidate_machines\s*=\s*schedule\.index\.candidates\[[^\]]+\][\s\S]{0,160}\bschedule\.rng\.shuffle\(\s*candidate_machines\s*\)", content):
        warnings.append("neighborhood_shuffles_candidate_machine_dict")
    if "if not all_moves" in content and "schedule.rng" in content and ("shuffle(" in content or "choice(" in content):
        warnings.append("neighborhood_adds_random_no_move_fallback")
    if (
        "if not all_moves" in content
        and "setup" in content
        and ("consider_same" in content or "consider_change" in content)
    ):
        warnings.append("neighborhood_adds_setup_no_move_fallback")
    if (
        "if not all_moves" in content
        and "change_machine_window" in content
        and "exhaustive_modes" not in content
        and re.search(r"critical_blocks\([^)]*exhaustive\s*=\s*false", content)
    ):
        warnings.append("neighborhood_gates_change_machine_on_empty_same_moves")
    return list(dict.fromkeys(warnings))


def awls_sdst_portfolio_search_control_warnings(content: str) -> list[str]:
    """Flag repeated SDST portfolio-control proposals that already tied."""

    warnings: list[str] = []
    if not re.search(r"\blane_summaries\s*(?::[^=\n]+)?=\s*(?:\[\s*\]|list\s*\(\s*\))", content):
        warnings.append("portfolio_missing_lane_summaries_initialization")
    seed_mapping_change = "effective_lane_seed" in content and (
        "7919" in content or "% 10000" in content or "modulo" in content or "lane-order" in content
    )
    budget_or_order_change = any(
        cue in content
        for cue in (
            "lane_budgets.sort",
            "sorted(portfolio_lanes",
            "remaining_time",
            "early_stop",
            "early-stop",
            "break",
            "reverse=",
            "time_limit_sec -",
            "rerun",
            "deepening",
        )
    )
    if seed_mapping_change and not budget_or_order_change:
        warnings.append("portfolio_retries_seed_mapping_only")
    best_lane_rerun = (
        re.search(r"\bbest_lane\b[\s\S]{0,900}\bsolve_awls_single\s*\(", content)
        and re.search(r"\b(?:best\.makespan|candidate\.makespan)\b", content)
        and re.search(r"\b(?:remaining_time|remaining_budget|phase2|second_phase|rerun|deepening)\b", content)
    )
    if best_lane_rerun:
        warnings.append("portfolio_retries_best_lane_rerun")
    subrun_seed_splitting = (
        re.search(r"\bsub_idx\b|\bsubrun\b|\bsub_run\b", content)
        and re.search(r"\b123457\b|\b7919\b|\bseed_offset\b", content)
        and re.search(r"\bsolve_awls_single\s*\(", content)
    )
    if subrun_seed_splitting:
        warnings.append("portfolio_retries_subrun_seed_splitting")
    setup_ratio_best_lane = (
        re.search(r"\bsetup[_\w]*ratio\b", content)
        and re.search(r"\b(?:gamma\s*\*|critical_block_exhaustive_pct\s*\*|doubled|double|restarts\s*\*)", content)
        and re.search(r"\bbest_lane\b[\s\S]{0,900}\bsolve_awls_single\s*\(", content)
    )
    if setup_ratio_best_lane:
        warnings.append("portfolio_retries_setup_ratio_best_lane_exploitation")
    return list(dict.fromkeys(warnings))


def awls_sdst_move_selection_warnings(content: str) -> list[str]:
    """Keep move-selection edits inside the select-only contract."""

    warnings: list[str] = []
    if re.search(r"\b(?:consider_same|consider_change)\s*\(", content):
        warnings.append("move_selection_generates_candidates")
    if re.search(r"\b(?:all_moves|ranked_moves|best_moves)\s*(?:\+=|-=|\*=|/=|//=|%=)", content) or re.search(
        r"\b(?:all_moves|ranked_moves|best_moves)\s*\.\s*(?:append|extend|insert)\s*\(",
        content,
    ):
        warnings.append("move_selection_mutates_candidate_lists")
    if re.search(r"\bschedule\s*\.\s*apply_move\s*\(", content):
        warnings.append("move_selection_mutates_schedule_directly")
    for line in content.splitlines():
        if re.search(r"\bschedule\.(?!rng\b|clone\b)[\w\.\[\]\(\)'\", ]+\.(?:append|extend|insert|pop|remove|clear|sort)\s*\(", line):
            warnings.append("move_selection_mutates_schedule_directly")
            break
        if re.search(r"\bschedule\.(?!rng\b|clone\b)[a-z_]\w*(?:\[[^\n=]*\])?\s*(?:=|\+=|-=|\*=|/=|//=|%=)", line):
            warnings.append("move_selection_mutates_schedule_directly")
            break
    if re.search(r"\btrial\s*\.\s*apply_move\s*\(", content) and not re.search(r"\btrial\s*=\s*schedule\s*\.\s*clone\s*\(", content):
        warnings.append("move_selection_trial_apply_without_clone")
    if re.search(r"\b(?!trial\b)(?!schedule\b)[a-z_]\w*\s*\.\s*apply_move\s*\(", content):
        warnings.append("move_selection_trial_apply_without_clone")
    small_best_moves_exact_recheck = (
        "best_moves" in content
        and ".clone(" in content
        and ".apply_move(" in content
        and re.search(r"\b(?:sample_size|subset_size)\s*=\s*min\(\s*3\s*,\s*len\(best_moves\)\s*\)", content)
    )
    if small_best_moves_exact_recheck:
        warnings.append("move_selection_retries_small_best_moves_exact_recheck")
    global_setup_sum_tiebreak = (
        re.search(r"\b(?:_?total_setup|setup_sum|trial_setup)\b", content)
        and "setup_time_between" in content
        and "machine_sequences" in content
        and "trial.makespan" in content
        and re.search(r"\(\s*trial\.makespan\s*,\s*(?:trial_setup|setup_sum|_total_setup\([^)]+\))", content)
    )
    if global_setup_sum_tiebreak:
        warnings.append("move_selection_retries_global_setup_sum_tiebreak")
    random_noise_escape = (
        re.search(r"\branked_with_noise\b|\.uniform\(\s*-0\.001\s*,\s*0\.001\s*\)", content)
        or (
            re.search(r"\brng\.randrange\(\s*100\s*\)\s*<\s*(?:5|10)\b", content)
            and re.search(r"\bchoice\(\s*all_moves\s*\)", content)
        )
    )
    if random_noise_escape:
        warnings.append("move_selection_retries_random_noise_escape")
    invalid_setup_signature = bool(
        re.search(r"\bsetup_time_between\(\s*(?:sched|schedule)\.index\s*,", content)
        or re.search(r"\bsetup_time_between\(\s*[^,\n]+\s*,\s*op1\s*,\s*op2\s*\)", content)
    )
    if invalid_setup_signature:
        warnings.append("move_selection_uses_invalid_setup_time_between_signature")
    if re.search(r"\b(?:schedule|sched|trial|idx|index)\.node_to_operation_key\b", content):
        warnings.append("move_selection_uses_nonexistent_node_to_operation_key")
    if re.search(r"\b(?:sched|schedule|trial)\.operations\b", content):
        warnings.append("move_selection_uses_nonexistent_operations_api")
    if re.search(r"\b(?:schedule|sched|trial)\.(?:on_machine|machine_predecessor|machine_successor|job_predecessor|job_successor)\.get\s*\(", content):
        warnings.append("move_selection_uses_dict_get_on_schedule_lists")
    misreads_move_key_as_op_key = (
        re.search(r"\b(?:op_key|target_m|target_machine)\b", content)
        and re.search(r"\b(?:move_type|method)\s*,\s*(?:op_key|op)\s*,\s*(?:target_m|target_machine)\s*=\s*move_key", content)
    )
    if misreads_move_key_as_op_key or re.search(r"move_type\s*==\s*['\"]change_machine['\"]", content):
        warnings.append("move_selection_misinterprets_move_key_shape")
    if re.search(r"\b(?:machine_id|target_machine|target_m)\s*=\s*move_key\s*\[\s*2\s*\]", content):
        warnings.append("move_selection_uses_where_node_as_machine_id")
    return list(dict.fromkeys(warnings))


def awls_sdst_weight_update_warnings(content: str) -> list[str]:
    """Keep adaptive-weight edits from becoming hidden solver or schedule rewrites."""

    warnings: list[str] = []
    if re.search(
        r"\b(?:apply_move|find_move|tabu_search|solve_awls|solve_awls_single|validate_standard_schedule)\s*\(",
        content,
    ):
        warnings.append("weight_update_calls_forbidden_runtime_api")
    if re.search(r"\b(?:open|read_text|write_text|subprocess|multiprocessing|requests|socket)\b", content):
        warnings.append("weight_update_uses_random_or_io")
    if re.search(r"\b(?:random\.|schedule\.rng\.|rng\.)", content):
        warnings.append("weight_update_uses_random_or_io")
    mutable_schedule_fields = (
        "machine_sequences",
        "job_predecessor",
        "job_successor",
        "machine_predecessor",
        "machine_successor",
        "on_machine",
        "on_machine_pos",
        "start_time",
        "end_time",
        "makespan",
    )
    field_pattern = "|".join(re.escape(field) for field in mutable_schedule_fields)
    if re.search(rf"\bschedule\.(?:{field_pattern})\b", content):
        warnings.append("weight_update_mutates_schedule_structure")
    if re.search(r"\bschedule\.(?!op_weight\b|op_cooldown\b)[a-z_]\w*(?:\[[^\n=]*\])?\s*(?:=|\+=|-=|\*=|/=|//=|%=)", content):
        warnings.append("weight_update_mutates_schedule_structure")
    if re.search(
        r"schedule\.op_weight\s*\[\s*moved_node\s*\]\s*=\s*max\s*\(\s*0\s*,\s*schedule\.op_weight\s*\[\s*moved_node\s*\]\s*-\s*1\s*\)",
        content,
    ):
        warnings.append("weight_update_retries_sdst_improvement_weight_decay")
    return list(dict.fromkeys(warnings))


def awls_sdst_search_transition_warnings(content: str) -> list[str]:
    """Keep transition edits from becoming hidden move generation or validator bypasses."""

    warnings: list[str] = []
    if re.search(
        r"\b(?:find_move|tabu_search|solve_awls|solve_awls_single|validate_standard_schedule)\s*\(",
        content,
    ):
        warnings.append("search_transition_calls_forbidden_runtime_api")
    if re.search(r"\b(?:current|schedule|trial|best)\s*\.\s*apply_move\s*\(", content):
        warnings.append("search_transition_calls_forbidden_runtime_api")
    if re.search(r"\badd_move_tabu\s*\(", content):
        warnings.append("search_transition_calls_forbidden_runtime_api")
    if re.search(r"\b(?:open|read_text|write_text|subprocess|multiprocessing|requests|socket|os\.environ)\b", content):
        warnings.append("search_transition_uses_io_or_unseeded_random")
    if re.search(r"\brandom\.", content):
        warnings.append("search_transition_uses_io_or_unseeded_random")
    mutable_schedule_fields = (
        "machine_sequences",
        "job_predecessor",
        "job_successor",
        "machine_predecessor",
        "machine_successor",
        "on_machine",
        "on_machine_pos",
        "start_time",
        "end_time",
        "makespan",
    )
    field_pattern = "|".join(re.escape(field) for field in mutable_schedule_fields)
    if re.search(rf"\b(?:current|best|schedule|trial)\.(?:{field_pattern})\b\s*(?:=|\+=|-=|\*=|/=|//=|%=)", content):
        warnings.append("search_transition_mutates_schedule_structure")
    if re.search(
        rf"\b(?:current|best|schedule|trial)\.(?:machine_sequences|job_predecessor|job_successor|machine_predecessor|machine_successor)\b[^\n]*\.(?:append|extend|insert|pop|remove|clear|sort)\s*\(",
        content,
    ):
        warnings.append("search_transition_mutates_schedule_structure")
    best_assignment = re.search(r"\bbest\s*=\s*current\s*(?!\.)", content)
    if best_assignment:
        warnings.append("search_transition_mutates_schedule_structure")
    worse_best_guard = re.search(
        r"\bif\s+current\.makespan\s*(?:>|>=)\s*best\.makespan\s*:\s*\n\s*best\s*=\s*current\.clone\(\)",
        content,
    )
    if worse_best_guard:
        warnings.append("search_transition_promotes_worse_best")
    if (
        re.search(r"\bbest\s*=\s*current\.clone\(\)", content)
        and not re.search(r"current\.makespan\s*<\s*best\.makespan", content)
    ):
        warnings.append("search_transition_promotes_worse_best")
    relative_degradation_reset = (
        re.search(r"\bcurrent\.makespan\s*>\s*(?:int\()?best\.makespan\s*\*\s*1\.0[1-9]", content)
        and re.search(r"\bcurrent\s*=\s*best\.clone\(\)", content)
    )
    if relative_degradation_reset:
        warnings.append("search_transition_retries_relative_degradation_best_reset")
    uses_stats = re.search(r"\bstats\s*(?:\.|\[)", content)
    if uses_stats and "stats is not none" not in content:
        warnings.append("search_transition_stats_without_none_guard")
    return list(dict.fromkeys(warnings))


def awls_sdst_tabu_memory_warnings(content: str) -> list[str]:
    """Keep tabu-memory edits as bounded bookkeeping, not hidden search rewrites."""

    warnings: list[str] = []
    tabu_add_count = len(re.findall(r"\btabu\s*\.\s*add\s*\(", content))
    if tabu_add_count != 1:
        warnings.append("tabu_memory_missing_or_multiple_tabu_add")
    if re.search(
        r"\b(?:find_move|tabu_search|solve_awls|solve_awls_single|validate_standard_schedule)\s*\(",
        content,
    ):
        warnings.append("tabu_memory_calls_forbidden_runtime_api")
    if re.search(r"\b(?:schedule|current|trial|best)\s*\.\s*(?:apply_move|clone)\s*\(", content):
        warnings.append("tabu_memory_calls_forbidden_runtime_api")
    if re.search(r"\b(?:open|read_text|write_text|subprocess|multiprocessing|requests|socket|os\.environ)\b", content):
        warnings.append("tabu_memory_uses_io_or_unseeded_random")
    if re.search(r"\brandom\.", content):
        warnings.append("tabu_memory_uses_io_or_unseeded_random")
    if (
        "has_sequence_dependent_setup(" in content
        or re.search(r"\bschedule\.index\.operation_key\b", content)
        or re.search(r"\bschedule\.is_critical\s*\(", content)
    ):
        warnings.append("tabu_memory_uses_nonexistent_api")
    if "setup_time_between" in content and "from harness_agent.standard_fjsp import setup_time_between" not in content:
        warnings.append("tabu_memory_uses_setup_without_import")
    short_front_back_sequence = (
        re.search(r"move\.method\s*==\s*front[\s\S]{0,180}sequence\s*=\s*\[\s*move\.which\s*,\s*move\.where\s*\]", content)
        and re.search(r"move\.method\s*==\s*back[\s\S]{0,180}sequence\s*=\s*\[\s*move\.where\s*,\s*move\.which\s*\]", content)
    )
    if short_front_back_sequence:
        warnings.append("tabu_memory_retries_short_front_back_sequence")
    expanded_critical_fraction = (
        "critical_count" in content
        and "fraction" in content
        and "schedule.is_critical_operation" in content
        and re.search(r"schedule\.machine_predecessor\s*\[\s*move\.where\s*\]", content)
        and re.search(r"schedule\.machine_successor\s*\[\s*stop\s*\]", content)
    )
    if expanded_critical_fraction:
        warnings.append("tabu_memory_retries_expanded_critical_fraction_sequence")
    target_machine_change_tabu = (
        "target_machine" in content
        and re.search(r"\bmachine_id\s*=\s*target_machine\b", content)
        and re.search(r"\bmove\.method\s+in\s*\(\s*change_machine_front\s*,\s*change_machine_back\s*\)", content)
        and re.search(r"\btenure\s*=\s*\(\s*tenure_min\s*\+\s*tenure_max\s*\)\s*//\s*2", content)
    )
    if target_machine_change_tabu:
        warnings.append("tabu_memory_retries_target_machine_change_tabu")
    if re.search(r"\btabu\s*\.\s*items\b", content):
        warnings.append("tabu_memory_mutates_schedule_or_tabu_directly")
    mutable_schedule_fields = (
        "machine_sequences",
        "job_predecessor",
        "job_successor",
        "machine_predecessor",
        "machine_successor",
        "on_machine",
        "on_machine_pos",
        "start_time",
        "end_time",
        "makespan",
    )
    field_pattern = "|".join(re.escape(field) for field in mutable_schedule_fields)
    if re.search(rf"\b(?:schedule|current|trial|best)\.(?:{field_pattern})\b\s*(?:=|\+=|-=|\*=|/=|//=|%=)", content):
        warnings.append("tabu_memory_mutates_schedule_or_tabu_directly")
    if re.search(
        rf"\b(?:schedule|current|trial|best)\.(?:machine_sequences|job_predecessor|job_successor|machine_predecessor|machine_successor)\b[^\n]*\.(?:append|extend|insert|pop|remove|clear|sort)\s*\(",
        content,
    ):
        warnings.append("tabu_memory_mutates_schedule_or_tabu_directly")
    return list(dict.fromkeys(warnings))


def generic_slot_repair_guidance(slot: dict[str, Any]) -> str:
    slot_id = str(slot.get("slot_id") or "")
    if slot_id == "awls_sdst_neighborhood_selection":
        return (
            "- Do not retry the known non-improving neighborhood patterns: near-critical 0.99*makespan filters, "
            "+/-10 or +/-3 same-machine windows, or tight tardiness > -5 insertion filters.\n"
            "- Do not replace the incumbent exhaustive/non-exhaustive critical-block pass with a fixed top-K latest-block "
            "subset only; top_k=3 latest-block pruning worsened oddla20 from 1010 to 1280.\n"
            "- Do not retry flat global move-count caps such as MAX_MOVES=200 or random diversity sampling with "
            "max_blocks/max_same_per_block/total_move_limit; those tied or failed at runtime.\n"
            "- Do not retry unordered first-N candidate-machine caps such as max_candidate_machines=3; that "
            "bounded NK slice tied oddla20 at 1010. If bounding alternate machines, order them by a real "
            "setup/load/slack score before slicing.\n"
            "- Do not add a random change-machine-only lane that skips same-machine/N7 generation; the 50% "
            "change-only variant worsened oddla20 from 1010 to 1039.\n"
            "- If editing this slot, use a materially different bounded candidate-generation idea such as "
            "boundary-biased N7 moves, bounded NK alternate-machine candidates from change_machine_window, "
            "or setup-heavy arc focus submitted only through consider_same / consider_change.\n"
            "- Return exactly one `replace_slot_block` change with `slot_id` set to `awls_sdst_neighborhood_selection`; "
            "wrong slot IDs or proposal-only responses are rejected before evaluation.\n"
            "- Use schedule.index.duration(node, schedule.on_machine[node]) for processing time; "
            "OperationIndex has no schedule.index.durations attribute.\n"
            "- Do not add random fallback moves that run only after all_moves is empty, and do not gate all "
            "change-machine candidates behind `if not all_moves` after same-machine generation; both patterns "
            "have tied or badly worsened oddla20.\n"
            "- Do not add setup-heavy candidates only under `if not all_moves`; that setup fallback tied oddla20 "
            "and usually does not affect the active incumbent traversal.\n"
            "- Do not call `schedule.setup_time`, `schedule.index.setup_time`, or `index.setup_time`; use "
            "`setup_time_between` with operation-key tuples if setup lookup is necessary.\n"
            "- If shuffling candidate machines, convert `schedule.index.candidates[node]` to a list first; it is a dict.\n"
            "- Do not call trial.apply_move, directly mutate schedule, or bypass the existing closures."
        )
    if slot_id == "awls_sdst_initialization":
        return (
            "- Do not retry append-only setup-aware earliest completion, low-setup tie-breaks, fixed small RCL, "
            "or tail-aware append scoring unchanged; those worsened oddla20.\n"
            "- The next acceptable initialization attempts should use true second-best-machine regret, "
            "assignment-then-sequencing, or bounded non-append insertion while scheduling each operation exactly once.\n"
            "- If using regret, compute best_machine_cost, second_best_machine_cost, and "
            "`regret = second_best_machine_cost - best_machine_cost` from candidate machine costs; a variable named "
            "`regret` without a second-best comparison is rejected.\n"
            "- Do not retry maximum-regret append-only dispatch that selects the highest "
            "`second_best_comp - best_comp` ready operation and assigns it to its best append machine; it worsened "
            "oddla20 from 1010 to 1066 despite reducing setup time.\n"
            "- Do not retry append-only second-best-regret roulette/weighted-random dispatch without real "
            "tail, bottleneck, repair, or topology mechanisms; it tied oddla20 at 1010 while only reducing "
            "setup time from 1940 to 1910.\n"
            "- Do not retry append-only remaining-work/earliest-completion tail-ratio dispatch with regret "
            "tie-breaks; it worsened oddla20 from 1010 to 1138 despite being legal.\n"
            "- When the round hypothesis requires topology, repair, non-append insertion, or assignment-then-sequencing, "
            "the replacement code must contain a real structure for that mechanism, such as `AwlsSchedule(...).topological_sort()`, "
            "a guarded sequence insert/swap, or a separate sequencing/repair phase; a renamed append-only priority formula is rejected.\n"
            "- This is a function-body slot inside `greedy_gt_init(index, rng, random_factor, idle_bonus)`: do not define "
            "`def awls_sdst_initialization`, do not use `self`, and return `(sequences, on_machine)`.\n"
            "- Use the real local APIs: `index.instance.job_count`, `index.instance.machine_count`, "
            "`index.job_to_nodes`, `index.candidates[node]`, `index.duration(node, machine_id)`, "
            "`index.node_to_job[node]`, and `index.node_to_op[node]`.  `StandardFjspInstance` has no "
            "`n_jobs`, `n_machines`, `ops`, `eligible_machines`, `processing_times`, or `sds_data` attributes.\n"
            "- Do not retry static single-bottleneck priority that ignores setup/tail/dynamic readiness; it was "
            "legal but worsened oddla20 from 1010 to 1029.\n"
            "- If using non-append insertion, do not directly commit `sequences[machine].insert(...)` without an "
            "acyclic/topological feasibility guard, and do not rebuild global job_ready for already scheduled "
            "operations after insertion; that produced a disjunctive-graph cycle on oddla20.\n"
            "- Use setup_time_between(index.instance, machine_id, previous_op, current_op, index) with operation-key tuples; "
            "never pass raw node ids or index.op_index."
        )
    if slot_id == "awls_sdst_same_machine_evaluation":
        return (
            "- Setup-aware R/Q propagation approximations have failed unless backed by an exact cloned trial.\n"
            "- Pure exact cloned trial scored as `trial.makespan + 0.001 * legacy` has already tied oddla20; "
            "do not retry it unchanged.\n"
            "- Do not wrap that pure exact trial in only a `legacy <= 1.1 * schedule.makespan` gate; semantic "
            "repair produced that variant and it remained an unrepaired repeat of the same idea class.\n"
            "- Exact cloned trial with only `0.001 * total_setup` / block-setup tie-breaker also tied oddla20; "
            "do not retry setup-time-only tie-breaking unchanged.\n"
            "- Do not retry exact trial plus a non-critical worsening penalty of `0.1 * (trial_makespan - "
            "schedule.makespan)` gated by `move.which` and backward_path_length; it legally tied oddla20 at 1010.\n"
            "- Move has fields `method`, `which`, and `where`; do not use nonexistent `move.node`.  Use "
            "`move.which` for the moved operation.\n"
            "- There is no `AwlsTrial` class in `harness_agent.standard_fjsp`; exact same-machine trials must use "
            "`trial = schedule.clone()`, `trial.apply_move(move)`, and `trial.makespan`.\n"
            "- Use `schedule.makespan` / `trial.makespan`; do not use "
            "`schedule.end_time[schedule.index.end_node]` as a makespan proxy.\n"
            "- If using exact trial again, add a materially different bounded gating rule, critical-tail pressure, "
            "or move-locality rule while preserving makespan pressure."
        )
    if slot_id == "awls_sdst_move_evaluation":
        return (
            "- Do not retry simple linear setup-delta penalties or full exact scoring over every change-machine "
            "candidate; both worsened oddla20.\n"
            "- Do not retry proxy-ratio gated exact scoring that stores `_best_proxy` on "
            "`change_machine_evaluate_parts`, exact-scores only candidates within about 5% of that proxy, "
            "and assigns 1e9-style hard penalties outside the gate; it worsened oddla20 from 1010 to 1023.\n"
            "- Do not retry `critical_factor = min(1, base_proxy / makespan)` multiplied by `setup_sum`; "
            "the legal version worsened the current 1002 incumbent to 1010.\n"
            "- Preserve the legacy AWLS proxy for makespan/tail pressure unless the replacement has a materially "
            "different bounded mechanism, such as move-local critical-tail pressure, ordered candidate context, "
            "or a gate that does not suppress outside-gate candidates with huge constants.\n"
            "- If exact scoring is used, clone first, apply `Move(method, which, where)`, catch ValueError/KeyError, "
            "and use `trial.makespan`; do not read `end_time[index.end_node]` as the makespan.\n"
            "- If a current makespan is needed inside this slot, use `schedule.makespan`, not "
            "`schedule.end_time[schedule.index.end_node]`.\n"
            "- If setup lookup is used, import and call `setup_time_between(instance, machine_id, previous_op, "
            "current_op, op_index)` with operation-key tuples and never with `current_op=None`.\n"
            "- `OperationIndex` has no `start_node` attribute.  Use the module constant `START_NODE` for the "
            "source sentinel and `schedule.index.end_node` for the sink sentinel."
        )
    if slot_id == "awls_sdst_portfolio_search_control":
        return (
            "- Do not retry seed-mapping-only perturbations such as adding idx * 7919 modulo 10000; that tied "
            "oddla20 at 1010 without improving the incumbent.\n"
            "- Do not retry probe-then-rerun-current-best or best-lane deepening with doubled restarts; broad-scan "
            "then rerun-best variants tied oddla20 at 1010.\n"
            "- Do not retry splitting each lane into deterministic sub-runs with offsets such as `sub_idx * 123457`; "
            "short multi-scramble lane splitting tied oddla20 at 1010.\n"
            "- Do not retry setup-ratio adaptive reruns of the current best lane that only increase gamma, "
            "critical_block_exhaustive_pct, or restarts; that remained best-lane exploitation and tied 1010.\n"
            "- If editing this slot, change a real search-control mechanism such as bounded lane ordering, "
            "per-lane budget allocation, early-stop policy, or auditable tie-breaking among equal makespans.\n"
            "- Keep the objective as makespan and preserve lane_summaries with seed/init/restarts/time/makespan diagnostics.\n"
            "- The replacement block must initialize `lane_summaries: list[str] = []` inside the slot before "
            "appending diagnostics; a prior two-phase candidate crashed with NameError after deleting it."
        )
    if slot_id == "awls_sdst_move_selection":
        return (
            "- This slot selects among already collected move keys only; do not call consider_same or consider_change.\n"
            "- Do not append, extend, insert into, or otherwise generate all_moves, ranked_moves, or best_moves.\n"
            "- Do not mutate schedule directly and do not call schedule.apply_move; exact checks must use "
            "`trial = schedule.clone()` followed by `trial.apply_move(Move(*move_key))`.\n"
            "- Preserve makespan as the primary exact objective.  Setup time may only be a bounded tie-breaker "
            "after exact makespan or approximate value.\n"
            "- AWLS move keys are `(method, which_node, where_node)` with method constants FRONT, BACK, "
            "CHANGE_MACHINE_FRONT, and CHANGE_MACHINE_BACK; do not treat them as operation-key tuples or "
            "use string literals like `change_machine`.\n"
            "- The third move-key field is `where_node`, not a machine id.  After `trial.apply_move(Move(*move_key))`, "
            "derive the affected machine from `trial.on_machine[move.which]` or another real node, not from `move_key[2]`.\n"
            "- AwlsSchedule has no `operations` record list.  Use machine_sequences, on_machine, "
            "machine_predecessor/successor, end_time, backward_path_length, and makespan.\n"
            "- `on_machine`, `machine_predecessor`, `machine_successor`, `job_predecessor`, and "
            "`job_successor` are lists, not dicts; use indexed access like `trial.on_machine[node]` and "
            "treat missing predecessor/successor sentinels as `-1`, not `None`.\n"
            "- Convert AWLS node ids to operation keys with module-level `operation_key(schedule, node)`; "
            "OperationIndex has no `node_to_operation_key` field.\n"
            "- Do not retry `min(3, len(best_moves))` exact rechecks over best_moves; both triggered and "
            "unconditional variants tied oddla20 at 1010.\n"
            "- Do not retry exact top-k selection whose main novelty is a full machine-sequence/global setup-sum "
            "tie-break; the operation_key/setup_time_between version tied oddla20 at 1010.\n"
            "- Do not retry random-noise ranking or unconditional random all_moves escapes; the setup tie-break + "
            "5%/10% random escape variant worsened oddla20 from 1010 to 1030.\n"
            "- If using setup_time_between, use the five-argument contract with operation-key tuples; "
            "`setup_time_between(sched.index, op1, op2)` is invalid.\n"
            "- Keep exact rechecks bounded by exact_select_top_k, ranked_moves, best_moves, or a small deterministic "
            "subset of all_moves; do not add a nested local search loop."
        )
    if slot_id == "awls_sdst_weight_update":
        return (
            "- This slot may mutate only schedule.op_weight and schedule.op_cooldown for real operation nodes.\n"
            "- Do not call apply_move, find_move, tabu_search, solve_awls, solve_awls_single, or evaluator/validator APIs.\n"
            "- Do not mutate machine_sequences, predecessor/successor links, on_machine, start/end times, or makespan.\n"
            "- Do not use random numbers, file IO, subprocesses, multiprocessing, network access, or environment variables.\n"
            "- Do not retry SDST improvement-step moved-node weight decay such as "
            "`schedule.op_weight[moved_node] = max(0, schedule.op_weight[moved_node] - 1)`; it worsened oddla20 "
            "from 1007 to 1008 under the 28s incumbent contract.\n"
            "- Preserve the new-best reset behavior after current_makespan < best_makespan_before unless a bounded "
            "alternative is explicitly justified by the hypothesis."
        )
    if slot_id == "awls_sdst_zi_features":
        return (
            "- This slot only adds numeric entries to `values` consumed by `zi_policy=formula` or `zi_policy=slot`.\n"
            "- In standard worker-loop slot mode, this selected slot is evaluated with a conservative formula consumer "
            "unless the request explicitly sets a different zi policy, so feature edits are observable by Core.\n"
            "- If the active solver command uses `--zi-policy critical`, `cpp`, `cpp-exact`, `aggressive`, `sqrt`, or "
            "`none`, feature-only changes are inert and must be rejected or paired with a policy/command that consumes them.\n"
            "- Do not spend a round only adding feature keys under a non-consuming zi policy; switch the evaluation to "
            "`formula`/`slot` or select a slot that the current policy actually uses, such as weight update or search transition.\n"
            "- Keep feature values finite and bounded, mutate only `values`, and never call local search or evaluator APIs."
        )
    if slot_id == "awls_sdst_search_transition":
        return (
            "- This slot controls only the post-move tabu-search transition after a legal move and weight update.\n"
            "- Preserve `best` as the lowest makespan seen in this tabu_search call; do not assign current to best "
            "unless current.makespan < best.makespan.\n"
            "- Do not call find_move, apply_move, add_move_tabu, tabu_search, solve_awls, solve_awls_single, or evaluator/validator APIs.\n"
            "- Do not mutate schedule topology fields, start/end times, on_machine, or makespan directly; assign whole clones only.\n"
            "- `stats` is optional; guard every stats read or write with `if stats is not None:`.\n"
            "- Plateau, backtrack, or restart logic must be bounded and deterministic from in-scope values, using only current.rng if randomness is needed."
        )
    if slot_id == "awls_sdst_tabu_memory":
        return (
            "- This slot only computes the local tabu memory update for the accepted move.\n"
            "- Call `tabu.add(machine_id, sequence, expires_at)` exactly once; do not mutate `tabu.items` directly.\n"
            "- Do not call apply_move, clone, find_move, tabu_search, solve_awls, solve_awls_single, or evaluator/validator APIs.\n"
            "- Use `schedule.index.instance.has_sequence_dependent_setup` as a property and module-level "
            "`operation_key(schedule, node)`; there is no `schedule.index.operation_key` helper.\n"
            "- Do not mutate schedule topology fields, start/end times, on_machine, or makespan.\n"
            "- Do not retry shortening FRONT/BACK tabu sequences to only `[move.which, move.where]` or "
            "`[move.where, move.which]` with short local tenures; it worsened oddla20 from 1010 to 1039.\n"
            "- Do not retry target-machine change-move tabu with midpoint deterministic tenure; it regressed the "
            "accepted sequence-length tenure baseline from 1002 to 1010.\n"
            "- Use only schedule.rng for seeded random tenure, and keep expires_at finite and >= iteration."
        )
    return "- Keep the replacement inside the selected slot contract and make the novelty materially different from failure memory."


def risk_notes_describe_concrete_blocker(text: str) -> bool:
    """Return whether an empty proposal names an actual contract/blocking reason."""

    explicit_blocker_cues = (
        "blocked",
        "blocker",
        "cannot safely",
        "unsafe",
        "no safe edit",
        "无法安全",
        "阻塞",
    )
    if any(cue in text for cue in explicit_blocker_cues):
        return True

    contract_blocker_cues = (
        "contract",
        "invariant",
        "unavailable",
        "unsupported",
        "outside the slot",
        "requires parser",
        "requires evaluator",
        "requires io",
        "契约",
        "不变量",
        "不可用",
        "不支持",
        "超出槽",
        "需要修改parser",
        "需要修改evaluator",
        "需要修改io",
    )
    missing_blocker_cues = (
        "missing required",
        "missing contract",
        "missing invariant",
        "missing api",
        "missing slot",
        "missing helper",
        "缺少必要",
        "缺少契约",
        "缺少api",
    )
    return any(cue in text for cue in contract_blocker_cues + missing_blocker_cues)


def validate_awls_slot_contract(context: dict[str, Any]) -> list[str]:
    """Validate that the context packet explicitly confirms the AWLS zi slot."""

    return validate_slot_manifest_gate(
        context,
        REQUIRED_SLOT_ID,
        expected_target_file=SLOT_RELATIVE_PATH,
        expected_marker_start=EVOLVE_START,
        expected_marker_end=EVOLVE_END,
    )


def validate_generic_slot_contract(context: dict[str, Any], slot_id: str) -> list[str]:
    errors = validate_slot_manifest_gate(context, slot_id)
    slot, slot_error = confirmed_context_slot(context, slot_id)
    if slot is None:
        return [*errors, slot_error] if errors else [slot_error]
    if str(slot.get("language") or "python") != "python":
        errors.append("generic DeepSeek slot worker currently supports only python slots")
    if str(slot.get("target_file") or "") == SLOT_RELATIVE_PATH:
        errors.append("AWLS zi slot must use the dedicated zi slot contract")
    if not str(slot.get("marker_start") or "") or not str(slot.get("marker_end") or ""):
        errors.append("slot marker_start and marker_end are required")
    return errors


def selected_confirmed_slot(context: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    manifest = context.get("slot_manifest")
    if not isinstance(manifest, dict) or not manifest.get("exists", True):
        return None, "context packet is missing a readable slot_manifest"
    slots = manifest.get("slots")
    if not isinstance(slots, list):
        return None, "slot_manifest.slots must be a list"
    confirmed = [slot for slot in slots if isinstance(slot, dict) and bool(slot.get("user_confirmed", False))]
    if not confirmed:
        return None, "slot_manifest does not contain a user_confirmed slot"
    if len(confirmed) > 1:
        return None, "DeepSeekSlotWorker requires exactly one user_confirmed slot"
    return confirmed[0], ""


def is_awls_zi_slot(slot: dict[str, Any]) -> bool:
    return (
        str(slot.get("slot_id", "")) == REQUIRED_SLOT_ID
        and str(slot.get("target_file", "")) == SLOT_RELATIVE_PATH
        and str(slot.get("marker_start", "")) == EVOLVE_START
        and str(slot.get("marker_end", "")) == EVOLVE_END
    )


def slot_context_for_prompt(slot: dict[str, Any], *, max_chars: int) -> str:
    parts = [
        str(slot.get("context_before") or ""),
        str(slot.get("marker_start") or ""),
        str(slot.get("original_content") or ""),
        str(slot.get("marker_end") or ""),
        str(slot.get("context_after") or ""),
    ]
    text = "\n".join(part.rstrip("\n") for part in parts if part)
    return text[-max_chars:]


def strip_marker_lines(content: str, slot: dict[str, Any]) -> str:
    stripped = strip_markdown_code_fence(content)
    marker_start = str(slot.get("marker_start", ""))
    marker_end = str(slot.get("marker_end", ""))
    if marker_start and marker_end and marker_start in stripped and marker_end in stripped:
        try:
            lines = stripped.splitlines(keepends=True)
            start_index = next(index for index, line in enumerate(lines) if line.strip() == marker_start.strip())
            end_index = next(index for index, line in enumerate(lines) if line.strip() == marker_end.strip())
            if end_index > start_index:
                stripped = "".join(lines[start_index + 1 : end_index])
        except StopIteration:
            pass
    return stripped.rstrip() + "\n"


def normalize_generic_slot_content(content: str, slot: dict[str, Any]) -> str:
    stripped = strip_marker_lines(content, slot)
    if str(slot.get("language") or "python") != "python":
        return stripped
    target_indent = minimum_nonblank_indent(str(slot.get("original_content") or ""))
    if target_indent <= 0:
        return stripped
    current_indent = minimum_nonblank_indent(stripped)
    if current_indent < 0 or current_indent == target_indent:
        return stripped
    if current_indent < target_indent:
        prefix = " " * (target_indent - current_indent)
        return "".join(prefix + line if line.strip() else line for line in stripped.splitlines(keepends=True))
    return dedent_to_indent(stripped, current_indent - target_indent)


def minimum_nonblank_indent(text: str) -> int:
    indents = [len(line) - len(line.lstrip(" ")) for line in text.splitlines() if line.strip()]
    return min(indents) if indents else -1


def dedent_to_indent(text: str, spaces: int) -> str:
    if spaces <= 0:
        return text
    result: list[str] = []
    prefix = " " * spaces
    for line in text.splitlines(keepends=True):
        if line.strip() and line.startswith(prefix):
            result.append(line[spaces:])
        else:
            result.append(line)
    return "".join(result)


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


def normalize_hypothesis(item: dict[str, Any], target_file: str) -> dict[str, Any]:
    target_files = item.get("target_files")
    if not isinstance(target_files, list) or not target_files:
        target_files = [target_file]
    evidence_used = item.get("evidence_used")
    if not isinstance(evidence_used, list):
        evidence_used = []
    return {
        "name": str(item.get("name") or "slot_change")[:120],
        "type": str(item.get("type") or "local_search_operator")[:80],
        "novelty": str(item.get("novelty") or "")[:1000],
        "expected_effect": str(item.get("expected_effect") or "")[:1000],
        "evidence_used": [str(value)[:160] for value in evidence_used[:12]],
        "target_files": [str(value)[:240] for value in target_files[:8]],
        "ablation_plan": str(item.get("ablation_plan") or "")[:1000],
    }


def normalize_context_usage(value: Any, target_file: str) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    referenced = payload.get("referenced_files")
    if not isinstance(referenced, list) or not referenced:
        referenced = [target_file]
    return {
        "used_project_intake": bool(payload.get("used_project_intake", False)),
        "referenced_files": [str(item)[:240] for item in referenced[:12]],
        "notes": str(payload.get("notes") or "")[:1000],
    }


def prioritize_knowledge_cards_for_slot(
    context: dict[str, Any],
    selected_slot: dict[str, Any] | None,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    cards = context.get("knowledge_cards") or []
    if not isinstance(cards, list):
        return []
    typed_cards = [card for card in cards if isinstance(card, dict)]
    if selected_slot is None:
        return typed_cards[:limit]

    slot_id = str(selected_slot.get("slot_id") or "").lower()
    tags = selected_slot.get("knowledge_tags") or []
    tag_terms = [str(tag).lower() for tag in tags if str(tag).strip()]

    def card_score(card: dict[str, Any]) -> int:
        path = str(card.get("path") or "").lower()
        snippet = str(card.get("snippet") or "").lower()
        haystack = f"{path}\n{snippet}"
        score = 0
        if slot_id and slot_id in haystack:
            score += 100
        if "awls_sdst" in path and "sdst" in tag_terms:
            score += 50
        score += sum(3 for tag in tag_terms if tag and tag in haystack)
        return score

    ranked = sorted(enumerate(typed_cards), key=lambda item: (card_score(item[1]), -item[0]), reverse=True)
    selected: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for _index, card in ranked:
        if card_score(card) <= 0:
            continue
        path = str(card.get("path") or "")
        if path in seen_paths:
            continue
        selected.append(card)
        seen_paths.add(path)
        if len(selected) >= limit:
            return selected
    for card in typed_cards:
        path = str(card.get("path") or "")
        if path in seen_paths:
            continue
        selected.append(card)
        seen_paths.add(path)
        if len(selected) >= limit:
            break
    return selected


def normalize_function_code(code: str) -> str:
    stripped = code.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:python)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    if "def evolved_zi" not in stripped:
        raise ValueError("function_code must define evolved_zi")
    return stripped.rstrip() + "\n"


def validate_slot_function(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"slot function has syntax error: {exc.msg}") from exc
    function_defs = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(function_defs) != 1 or function_defs[0].name != "evolved_zi":
        raise ValueError("slot code must contain exactly one function named evolved_zi")
    if len(tree.body) != 1:
        raise ValueError("slot code may not contain top-level code outside evolved_zi")
    for node in ast.walk(tree):
        if isinstance(node, FORBIDDEN_AST_NODES):
            raise ValueError(f"slot code uses forbidden syntax: {type(node).__name__}")
        if isinstance(node, ast.Call):
            validate_call(node)


def validate_call(node: ast.Call) -> None:
    if isinstance(node.func, ast.Attribute):
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "values" and node.func.attr == "get"):
            raise ValueError("slot code may only call values.get(...) or whitelisted numeric functions")
        return
    if isinstance(node.func, ast.Name):
        if node.func.id == "values":
            raise ValueError("slot code must call values.get(...), not values(...)")
        if node.func.id not in ALLOWED_FUNCTION_NAMES:
            raise ValueError(f"slot code calls non-whitelisted function: {node.func.id}")
        return
    raise ValueError("slot code contains unsupported call expression")


def replace_evolve_block(text: str, function_code: str) -> str:
    return replace_marked_block(text, EVOLVE_START, EVOLVE_END, function_code)


def render_slot_markdown(proposal: dict[str, Any]) -> str:
    lines = ["# DeepSeek AWLS Slot Proposal", ""]
    if proposal.get("summary"):
        lines.extend(["## Summary", "", str(proposal["summary"]), ""])
    if proposal.get("strategy_intent"):
        lines.extend(["## Strategy Intent", "", str(proposal["strategy_intent"]), ""])
    hypotheses = proposal.get("rule_operator_hypotheses", [])
    if hypotheses:
        lines.extend(["## Rule Hypotheses", ""])
        lines.extend([f"- {item}" for item in hypotheses])
        lines.append("")
    if proposal.get("function_code"):
        lines.extend(["## Function Code", "", "```python", str(proposal["function_code"]).rstrip(), "```", ""])
    rejected = proposal.get("rejected", [])
    if rejected:
        lines.extend(["## Rejections", ""])
        lines.extend([f"- {item}" for item in rejected])
        lines.append("")
    if proposal.get("risk_notes"):
        lines.extend(["## Risk Notes", ""])
        lines.extend([f"- {item}" for item in proposal["risk_notes"]])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
