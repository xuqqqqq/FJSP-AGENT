from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from ..deepseek_client import DeepSeekClient, is_deepseek_configured
from ..slot_contract import replace_marked_block, validate_slot_manifest_gate
from ..worker import CodingWorker, ExperimentSpec, WorkerCapabilities, WorkerResult
from .deepseek_worker import extract_json_object


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
        gate_errors = validate_awls_slot_contract(context)
        if gate_errors:
            gate_path = output_dir / "slot_contract_rejection.json"
            gate_path.write_text(
                json.dumps(
                    {
                        "status": "contract_rejected",
                        "slot_id": REQUIRED_SLOT_ID,
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
                summary="Slot manifest does not confirm the AWLS zi code slot.",
                artifacts={"output_dir": str(output_dir), "slot_contract_rejection": str(gate_path)},
            )
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


def compact_context(context: dict[str, Any]) -> dict[str, Any]:
    task = context.get("task", {})
    contract = context.get("contract", {})
    docs = context.get("docs", [])
    slot_manifest = context.get("slot_manifest") or {}
    slots = slot_manifest.get("slots") if isinstance(slot_manifest, dict) else []
    if not isinstance(slots, list):
        slots = []
    awls_slot = next(
        (
            slot
            for slot in slots
            if isinstance(slot, dict) and slot.get("slot_id") == "awls_zi_policy"
        ),
        None,
    )
    return {
        "task": task,
        "problem_family_capability": context.get("problem_family_capability") or {},
        "objectives": contract.get("objectives", []),
        "instances": contract.get("instances", [])[:3],
        "commands": contract.get("commands", {}),
        "slot_manifest": {
            "status": slot_manifest.get("status") if isinstance(slot_manifest, dict) else None,
            "confirmation_required": slot_manifest.get("confirmation_required") if isinstance(slot_manifest, dict) else None,
            "selected_slot": awls_slot,
        },
        "docs": docs[:2],
        "previous_evidence": context.get("previous_evidence", [])[:4],
        "hypothesis": context.get("hypothesis", ""),
    }


def validate_awls_slot_contract(context: dict[str, Any]) -> list[str]:
    """Validate that the context packet explicitly confirms the AWLS zi slot."""

    return validate_slot_manifest_gate(
        context,
        REQUIRED_SLOT_ID,
        expected_target_file=SLOT_RELATIVE_PATH,
        expected_marker_start=EVOLVE_START,
        expected_marker_end=EVOLVE_END,
    )


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
