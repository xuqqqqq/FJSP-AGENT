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
        "slot_uses_nonexistent_operation_index_durations",
        "neighborhood_adds_random_no_move_fallback",
        "neighborhood_gates_change_machine_on_empty_same_moves",
        "neighborhood_retries_failed_near_critical_threshold",
        "neighborhood_retries_failed_same_machine_window",
        "neighborhood_retries_failed_tight_tardiness_filter",
        "same_machine_retries_pure_exact_trial",
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
    warnings.extend(slot_specific_generic_warnings(slot, normalized_changes))
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
        "slot_uses_nonexistent_operation_index_durations",
        "neighborhood_adds_random_no_move_fallback",
        "neighborhood_gates_change_machine_on_empty_same_moves",
        "neighborhood_retries_failed_near_critical_threshold",
        "neighborhood_retries_failed_same_machine_window",
        "neighborhood_retries_failed_tight_tardiness_filter",
        "same_machine_retries_pure_exact_trial",
    }
    return any(str(item) in repair_warnings for item in warnings)


def slot_specific_generic_warnings(slot: dict[str, Any], changes: list[dict[str, str]]) -> list[str]:
    """Detect known failed idea classes that generic novelty text can miss."""

    slot_id = str(slot.get("slot_id") or "")
    content = "\n".join(str(item.get("content") or "") for item in changes).lower()
    if not content:
        return []
    warnings: list[str] = []
    if re.search(r"schedule\.index\.durations\b|\bindex\.durations\b", content):
        warnings.append("slot_uses_nonexistent_operation_index_durations")
    if slot_id == "awls_sdst_neighborhood_selection":
        return warnings + awls_sdst_neighborhood_selection_warnings(content)
    if slot_id != "awls_sdst_same_machine_evaluation":
        return warnings
    uses_setup_propagation = "setup_time_between" in content and ("new_r" in content or "new_q" in content)
    uses_exact_trial = ".clone(" in content and ".apply_move(" in content and "trial.makespan" in content
    if uses_setup_propagation and not uses_exact_trial:
        warnings.append("same_machine_setup_propagation_without_exact_trial")
    if uses_exact_trial and "setup_time_between" not in content and re.search(r"0\.00?1\s*\*\s*float\(legacy\)", content):
        warnings.append("same_machine_retries_pure_exact_trial")
    return warnings


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
    if "if not all_moves" in content and "schedule.rng" in content and ("shuffle(" in content or "choice(" in content):
        warnings.append("neighborhood_adds_random_no_move_fallback")
    if (
        "if not all_moves" in content
        and "change_machine_window" in content
        and "exhaustive_modes" not in content
        and re.search(r"critical_blocks\([^)]*exhaustive\s*=\s*false", content)
    ):
        warnings.append("neighborhood_gates_change_machine_on_empty_same_moves")
    return list(dict.fromkeys(warnings))


def generic_slot_repair_guidance(slot: dict[str, Any]) -> str:
    slot_id = str(slot.get("slot_id") or "")
    if slot_id == "awls_sdst_neighborhood_selection":
        return (
            "- Do not retry the known non-improving neighborhood patterns: near-critical 0.99*makespan filters, "
            "+/-10 or +/-3 same-machine windows, or tight tardiness > -5 insertion filters.\n"
            "- If editing this slot, use a materially different bounded candidate-generation idea such as "
            "boundary-biased N7 moves, bounded NK alternate-machine candidates from change_machine_window, "
            "or setup-heavy arc focus submitted only through consider_same / consider_change.\n"
            "- Use schedule.index.duration(node, schedule.on_machine[node]) for processing time; "
            "OperationIndex has no schedule.index.durations attribute.\n"
            "- Do not add random fallback moves that run only after all_moves is empty, and do not gate all "
            "change-machine candidates behind `if not all_moves` after same-machine generation; both patterns "
            "have tied or badly worsened oddla20.\n"
            "- Do not call trial.apply_move, directly mutate schedule, or bypass the existing closures."
        )
    if slot_id == "awls_sdst_same_machine_evaluation":
        return (
            "- Setup-aware R/Q propagation approximations have failed unless backed by an exact cloned trial.\n"
            "- Pure exact cloned trial scored as `trial.makespan + 0.001 * legacy` has already tied oddla20; "
            "do not retry it unchanged.  If using exact trial, add a materially different bounded tie-breaker "
            "or gating rule while preserving makespan pressure."
        )
    return "- Keep the replacement inside the selected slot contract and make the novelty materially different from failure memory."


def risk_notes_describe_concrete_blocker(text: str) -> bool:
    """Return whether an empty proposal names an actual contract/blocking reason."""

    blocker_cues = (
        "blocked",
        "blocker",
        "cannot safely",
        "unsafe",
        "contract",
        "invariant",
        "missing",
        "unavailable",
        "unsupported",
        "outside the slot",
        "requires parser",
        "requires evaluator",
        "requires io",
        "no safe edit",
        "无法安全",
        "阻塞",
        "契约",
        "不变量",
        "缺少",
        "不可用",
        "不支持",
        "超出槽",
        "需要修改parser",
        "需要修改evaluator",
        "需要修改io",
    )
    return any(cue in text for cue in blocker_cues)


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
