"""DeepSeek Coding Worker。

这是一个“结构化备用适配器”而不是直接改文件的默认运行时：它把主 Agent
提供的上下文压缩成 prompt，请模型返回受限 JSON proposal，再由 harness
统一做规范化、审计、路径门禁和 apply。这样即使模型输出波动较大，核心
编排层仍能保留可追溯、可拒绝、可部分落地的控制面。
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

from harness_agent.context.compaction import compact_json, stable_worker_context_json
from harness_agent.context.loader import load_context_dict
from ..deepseek_client import DeepSeekClient, is_deepseek_configured
from harness_agent.agents.quality_contract import build_agent_generated_solver_quality_contract
from harness_agent.slots.contract import extract_marked_block, replace_marked_block, validate_slot_manifest_gate
from harness_agent.agents.reachability import unreachable_defined_function_helpers
from ..worker import CodingWorker, ExperimentSpec, WorkerCapabilities, WorkerResult


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


# === 响应提取与 Worker 主流程 ==================================================

def extract_json_object(text: str) -> dict[str, Any]:
    """尽量从模型响应里提取第一个顶层 JSON object。

    DeepSeekWorker 依赖结构化输出做后续审计；这里允许剥离 Markdown fence
    和前后噪声，是为了把“格式轻微漂移”和“真实内容不可用”区分开来。
    """
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
    """把 LLM 代码编辑意图转成可审计 proposal 的结构化适配器。

    与 OpenCodeWorker 的“直接在 worktree 内运行子进程”路线不同，这里先
    产出 JSON proposal，再由 harness 统一校验 `ExperimentSpec` 允许的
    路径、slot 和 action。这样便于在无直接文件执行权限时保留可控的备用
    通道，也便于做 proposal 级审计。
    """

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
        """执行一次“上下文 -> proposal -> 可选 apply”的结构化 worker 周期。

        数据流与 OpenCodeWorker 有明显差异：
        - 输入仍来自 `ExperimentSpec` 的 context packet / worktree / output_dir；
        - 模型先返回 JSON proposal，而不是直接改 worktree；
        - harness 在本地完成规范化、审计和 apply，因此 `apply_changes=False`
          时也能完整保存 proposal 供主流程复核。

        该适配器不管理外部子进程生命周期；授权状态由
        `is_deepseek_configured()` 和客户端环境决定，未配置时直接返回
        `unavailable`，避免进入半可用状态。
        """
        output_dir = Path(spec.output_dir) if spec.output_dir else Path(spec.worktree_path) / ".algoforge_worker" / spec.experiment_id
        output_dir.mkdir(parents=True, exist_ok=True)
        if not self.available:
            return WorkerResult(
                status="unavailable",
                changed_files=[],
                summary="DeepSeek API is not configured.",
                artifacts={"output_dir": str(output_dir)},
            )

        context = load_context_dict(Path(spec.context_packet_path))
        client = DeepSeekClient.from_env(model=self.model)
        prompt = self._code_edit_prompt(context=context, max_steps=spec.max_steps)
        response = client.chat_with_usage(
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
        content = response.content
        raw_path = output_dir / "deepseek_code_edit_raw.json"
        raw_path.write_text(content, encoding="utf-8")
        usage_path = output_dir / "deepseek_usage.json"
        usage_path.write_text(
            json.dumps(
                {
                    "usage": response.usage,
                    "cache_hit_ratio": response.cache_hit_ratio,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            raw_proposal = extract_json_object(content)
        except json.JSONDecodeError as exc:
            # 先修 JSON 外壳，再走同一套规范化/审计流程，避免因格式噪声丢失
            # 可能仍有价值的代码内容。
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
            # 是否真正写入 worktree 由编排层决定；proposal 始终先落盘，保证
            # apply 前后都能追踪“模型原意”和“harness 接受的最终变体”。
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
                "usage": str(usage_path),
            },
        )

    def _code_edit_prompt(self, *, context: dict[str, Any], max_steps: int) -> str:
        """生成结构化代码编辑 prompt。

        stable context 放缓存友好的任务常量，dynamic priority context 放当前
        轮 incumbent、repair、knowledge cards 等易变信息，目的是让模型先
        吃到稳定约束，再读取本轮最需要响应的动态尾部。
        """
        stable_context = stable_worker_context_json(context).text
        priority_context = priority_worker_context(context)
        return f"""
You are inside an AlgoForge coding-worker loop. The harness/evaluator is the
source of truth; your job is to propose a small code change that can be audited
and then evaluated by Core. When the priority context says this is an
agent-generated baseline or legality repair, the safe change may be a complete
standalone generated solver entrypoint rather than a tiny patch.

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
      "target_files": ["active solver path from evaluator_protocol.solver_command_template"],
      "ablation_plan": "how Core can isolate this rule/operator effect in a later run"
    }}
  ],
  "solver_contract_self_check": {{
    "active_features": ["copy exact active_features from agent_generated_solver_quality_contract"],
    "capabilities": [
      {{
        "name": "one required_code_capability or variant_required_code_capability",
        "status": "implemented",
        "evidence": "concrete function/variable/guard symbols from submitted code"
      }}
    ],
    "representation": "cite source symbols for the operation identity, assignment, and machine sequence structures the code uses",
    "decoder": "cite the source function that rebuilds a complete schedule and rejects infeasible candidates",
    "variant_handling": ["for each active variant_required_code_capability, cite the concrete timing/capacity/objective guard; use [] when none are active"],
    "runtime_bounds": "cite source symbols where restarts/iterations/windows/deadlines are capped",
    "incumbent_preservation": "cite source symbols showing how failed candidates keep the incumbent schedule",
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
- Do not ask the coding runtime to run formal benchmarks, the evaluator command,
  multiple seeds, repeated solver trials, parameter sweeps, or the full test
  suite. The worker quick_test_plan is limited to one compile check and one
  fixed-seed short smoke; Core owns formal evaluation.
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
- When baseline generation requires `create_or_replace` with a complete
  standalone generated solver, keep the file compact and JSON-safe: target
  under about 260 lines, avoid long comments/docstrings/embedded reports, and
  include only code that is actually called by `main(...)` or `solve(...)`.
  Truncated full-file JSON content will be rejected before evaluation.
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
  the task contract explicitly asks to implement those surfaces.  For
  current-project standard FJSP solver files, prefer importing the existing
  parser/evaluator helpers instead of reimplementing machine-index or duration
  parsing. This import guidance does not apply to standalone agent-generated
  `examples/agent_generated*.py` solver runtimes.
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
- For standard FJSP text instances when the active context has no setup times
  (`setup_time_kinds` empty/none and no `sequence_dependent_setup` feature),
  parse packed job lines with a token cursor.  Each job line starts with
  `operation_count`; every operation on that same line consumes
  `candidate_count` followed by exactly `2 * candidate_count` machine-duration
  tokens.  Do not increment the physical file-line index once per operation.
- When agent_generated_solver_quality_contract.enabled is true and you edit an
  agent-generated solver, fill `solver_contract_self_check` before the changes:
  list the active_features you detected from IO/requirements/diagnostics, mark
  each required and variant_required capability as implemented/missing/not_applicable,
  and cite concrete function names, variables, or guards as evidence. Evidence
  must name symbols that appear verbatim in the proposed code, such as
  `parse_instance`, `op_info`, `decode_schedule`, `expected_ops`, `deadline`, or
  `best_schedule`. Do not mark a capability implemented unless the proposed code
  contains the cited evidence and the cited code is called before output is
  written. The narrative fields `representation`, `decoder`,
  `variant_handling`, `runtime_bounds`, and `incumbent_preservation` must also
  cite source symbols that appear in the proposed code; do not use those fields
  for high-level strategy text that has no matching implementation anchor.
- The declared output schema must match the actual bytes written to
  `--output`. For standard FJSP generated solvers, write a JSON object such as
  `{{"format": "...", "schedule": [...], "makespan": ...}}`; never write a bare
  schedule list via `json.dump(best_schedule, f)` or
  `Path(output).write_text(json.dumps(best_schedule))`.
- Any parser, decoder, schedule builder, or validation/self-check helper you
  define must be called by the generated solver flow, such as `solve(...)`,
  `improve(...)`, or `main(...)`, before writing output. Do not define
  decorative helpers just to satisfy evidence checks.
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
- For `operation_level_ready_list_constructor`, do not select one ready
  operation and then call `rng.choice(eligible)` or `random.choice(machines)`.
  Build `ready_choices`/`candidate_choices` by looping over every ready
  operation and every eligible machine, compute `start` and `finish` from
  `job_ready` and `machine_ready`, then commit the best or seeded tie-break
  candidate. This is required even for a randomized baseline.
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
- If Priority context contains `loop_feedback.current_direction_plan`, treat it
  as the Main Agent experiment contract. Translate that plan into code evidence;
  do not replace it with an unrelated worker-authored algorithm direction.
- If Priority context contains `active_method_package`, adapt only that package
  in this direction. Read the implementation asset and behavior contract, keep
  the executable method structure, and do not combine unrelated method families.
- If previous_pipeline_memory.operator_guidance is present, use its must_do,
  preserve, mutate, and avoid lists when forming rule_operator_hypotheses and
  novelty statements.
- If Priority context contains loop_feedback.current_round_repair, this is an
  in-round repair attempt after Core rejected the previous proposal. First fix
  the listed JA/evaluator issues; do not repeat rejected anchors, unsupported
  actions, protected-fact regressions, no-op proposals, or syntax errors.
- Read the Stable task context first, then the Dynamic round context. If
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
- A machine-sequence decoder must not simply replay
  `for machine_id, sequence in machine_sequences.items(): for op in sequence`.
  Decode with a progress/topological loop: schedule a machine's next operation
  only when its job predecessor is already scheduled, otherwise continue; if no
  operation can progress, reject the candidate as infeasible.
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

Stable task context (cache-friendly and unchanged within one task):
{stable_context}

Dynamic round context (incumbent, retrieved methods, and recent feedback):
{priority_context}
""".strip()

    def _repair_code_edit_json(self, client: DeepSeekClient, raw: str, error: str, max_tokens: int) -> str:
        """让模型只修复 JSON 包装层，不重写原始策略意图。"""
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
        """把原始 proposal 规整为 harness 可审计、可落地的受限结构。

        这里是 structured adapter 的核心安全带：
        - 统一 action/path/slot/content 形状；
        - 把不允许的编辑移入 `rejected_changes`；
        - 生成 proposal audit，供主流程判断这轮建议是否可信。
        """
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


# === Priority context：把主流程状态压缩给 Worker =================================

def priority_worker_context(context: dict[str, Any]) -> str:
    """构造 dynamic priority context。

    这一段承接主编排层的“当前轮状态机”：baseline 还是 improvement、
    incumbent 是否需要合法性修复、Main Agent 当前方向、quality contract、
    knowledge cards、repair memory 等都在这里浓缩后提供给 worker。
    """
    quality_contract = build_agent_generated_solver_quality_contract(context)
    is_improvement_round = bool(context.get("iteration_edit_contract"))
    operator_stage = operator_improvement_stage_for_worker(context, quality_contract=quality_contract)
    method_scope_rule = (
        "Derive algorithm behavior only from active_method_package assets, priority_knowledge_cards, the active "
        "requirement/IO contract, and the Main Agent direction. Adapt those requirements to the current incumbent; "
        "do not assume a method merely because it was useful in another task. Verify claims through reachable call "
        "paths, consumed values, state transitions, rollback behavior, and bounded tests. Identifiers are only source "
        "locators and never count as implementation evidence."
    )
    payload = {
        "round_type": "improvement_round" if is_improvement_round else "baseline_or_single_round",
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
        "operator_improvement_stage": operator_stage,
        "round_learning_contract": round_learning_contract_for_worker(is_improvement_round=is_improvement_round),
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
            "Use agent_generated_solver_quality_contract.active_features and capability_playbook to decide which "
            "constraints must appear in generated code. Do not import capabilities from inactive variants."
        ),
        "active_io_parser_rule": (
            "The active_io_parser capability requires deriving every job/operation/candidate machine and duration "
            "from the active input file. Do not satisfy parser checks by reading the file and then hardcoding "
            "op_info, assignment, machine_sequences, or a fixed one-operation schedule."
        ),
        "solver_quality_playbook_rule": (
            "For each item in agent_generated_solver_quality_contract.capability_playbook, either implement the "
            "capability and cite concrete code evidence in solver_contract_self_check.capabilities, or mark it "
            "missing with a repair note. Evidence must cite reachable source locations and explain the relevant "
            "inputs, outputs, state transition, consumer, and guard. Symbol names are navigation labels only; do "
            "not infer capability from a method-like name or reject equivalent behavior because identifiers differ. "
            "The solver_contract_self_check narrative fields representation, decoder, variant_handling, "
            "runtime_bounds, and incumbent_preservation must cite submitted behavior, not only describe the intended method."
        ),
        "generated_solver_call_flow_rule": (
            "Parser, decoder, schedule-builder, and validation/self-check helpers must be called by the runnable "
            "generated solver flow before output is written. A helper that is only defined or cited in "
            "solver_contract_self_check is not enough."
        ),
        "candidate_runtime_import_rule": (
            "When the solver command is an agent-generated examples/agent_generated*.py entrypoint, "
            "the entrypoint and helper modules under examples must run as standalone example scripts. "
            "Do not add `from harness_agent...` or `import harness_agent...` in those files; the Core JA "
            "gate rejects such imports before evaluator execution."
        ),
        "worker_instruction": {
            "required_order": (context.get("worker_instruction") or {}).get("required_order"),
            "round_feedback_rule": (context.get("worker_instruction") or {}).get("round_feedback_rule"),
            "incremental_edit_rule": (context.get("worker_instruction") or {}).get("incremental_edit_rule"),
        },
        "agent_generated_method_skeleton_rule": method_scope_rule,
        "priority_knowledge_cards": compact_priority_knowledge_cards(
            context,
            limit=priority_knowledge_card_limit(context),
            max_chars_per_card=PRIORITY_KNOWLEDGE_CARD_MAX_CHARS,
        ),
        "active_method_package": context.get("active_method_package") or {},
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
        "algorithm_semantic_memory_rule": (
            "If loop_feedback.experience_memory.algorithm_semantic_memory is present, preserve repaired method "
            "semantics and address recurring categories with the cited behavioral tests before restating a named "
            "algorithm claim. A legal score does not override evidence-backed semantic findings."
        ),
        "main_agent_direction_rule": (
            "When loop_feedback.current_direction_plan is present, implement its change_scope, preserve, avoid, "
            "knowledge_paths, acceptance_checks, and stop_conditions. Worker hypotheses may refine the plan into "
            "code-level actions but must not switch to another method inside the same direction."
        ),
    }
    return compact_json(payload, max_chars=priority_context_max_chars()).text


def round_learning_contract_for_worker(*, is_improvement_round: bool) -> dict[str, Any]:
    """按轮次阶段下发不同的学习/修改契约。"""
    if not is_improvement_round:
        return {
            "must_do": [
                "Create or repair a complete standalone generated solver before any objective-only tuning.",
                "Use the active IO contract and instance diagnostics to implement parser, stable representation, decoder/build path, output schema, and self-check evidence.",
                "Treat agent_generated_solver_quality_contract.required_code_capabilities and variant_required_code_capabilities as the acceptance checklist.",
                "Do not preserve a nonexistent incumbent; create_or_replace of the solver entrypoint is allowed during baseline generation or legality repair.",
                "Do not spend steps on local search until parser, constructor, decoder, and self-check capabilities can pass review.",
            ],
            "quality_target": (
                "The baseline proposal should be runnable, legal, and attributable: one explicit construction "
                "hypothesis, complete IO-derived schedule generation, fixed parser/evaluator semantics, and Core "
                "evaluator evidence only."
            ),
        }
    return {
        "must_do": [
            "Preserve the current promoted incumbent and make one accepted incremental edit.",
            "Treat the current outer round as one improvement direction. Repair or refine the same direction before switching ideas.",
            "Use failure_memory.must_avoid as hard negative memory.",
            "Do not submit a no-op proposal during improvement rounds.",
            "When the incumbent is legal and repair feedback is not blocking, choose one method-level operator from retrieved local-search/operator knowledge instead of another tie-break-only tweak.",
            "Do not repeat a legal-but-not-better tie-break tweak; change the neighborhood, decoder, or insertion/regret mechanism materially.",
        ],
        "quality_target": (
            "The next proposal should be both legal and attributable: one explicit rule/operator hypothesis, "
            "one bounded code mutation, fixed parser/evaluator semantics, and Core evaluator evidence only."
        ),
    }


def operator_improvement_stage_for_worker(
    context: dict[str, Any],
    *,
    quality_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """判断当前轮是否已经可以从“造出合法解”切到“算子改进”阶段。"""

    if not context.get("iteration_edit_contract"):
        return {"active": False, "reason": "not_improvement_round"}
    if incumbent_requires_legality_repair(context):
        return {"active": False, "reason": "incumbent_requires_legality_repair"}
    loop_feedback = context.get("loop_feedback") if isinstance(context.get("loop_feedback"), dict) else {}
    incumbent_key = loop_feedback.get("incumbent_key_before")
    baseline_memory = (
        loop_feedback.get("agent_generated_baseline_memory")
        if isinstance(loop_feedback.get("agent_generated_baseline_memory"), dict)
        else {}
    )
    has_legal_incumbent = _finite_objective_key(incumbent_key) or bool(
        baseline_memory.get("accepted_as_incumbent")
    )
    if not has_legal_incumbent:
        return {"active": False, "reason": "no_finite_legal_incumbent"}

    current_repair = (
        loop_feedback.get("current_round_repair")
        if isinstance(loop_feedback.get("current_round_repair"), dict)
        else {}
    )
    repair_status = str(current_repair.get("status") or "").strip()
    if repair_status == "repair_required" and repair_blocks_operator_stage(current_repair):
        return {
            "active": False,
            "reason": "current_attempt_needs_legality_or_schema_repair",
            "repair_rule": "Fix current_round_repair.repair_targets before adding a new local-search operator.",
        }

    active_features = []
    if isinstance(quality_contract, dict):
        active_features = list(quality_contract.get("active_features") or [])
    method_stage = incumbent_method_stage_for_worker(context)
    mode = "same_direction_refinement" if repair_status == "refinement_required" else "new_operator_direction"
    if repair_status == "repair_required":
        mode = "same_direction_repair"
    return {
        "active": True,
        "mode": mode,
        "active_features": active_features[:16],
        "incumbent_method_stage": method_stage,
        "required_next_operator_capabilities": required_next_operator_capabilities(method_stage),
        "purpose": (
            "A legal incumbent exists. The worker should now activate method knowledge for bounded search operators, "
            "not keep only repairing construction prose or tuning dispatch tie-breaks."
        ),
        "must_do": [
            "Choose one bounded improvement from the Main Agent direction and retrieved method assets; cite the exact card paths in evidence_used.",
            "Preserve the incumbent's Core-proven IO, legality, runtime, and rollback behavior.",
            "Make the proposed behavior reachable from the solver entry flow and validate it with the behavioral checks required by the selected method contract.",
            "If required_next_operator_capabilities is non-empty, implement the cited behavior and its required test; renaming helpers or deleting a claim does not repair a semantic failure.",
            "Keep same-direction repair focused on evidence-backed Semantic Reviewer findings instead of switching algorithms inside the round.",
            "Do not copy instance-specific scores, schedules, or target makespans from reports into solver code.",
        ],
    }


def incumbent_method_stage_for_worker(context: dict[str, Any]) -> dict[str, Any]:
    """只返回 evaluator / semantic review 真正支撑的方法证据。

    这里刻意忽略“函数名长得像某算法”之类的静态命名暗示，避免 worker 被
    错误引导去做重命名式修补，而不是修真实语义缺陷。
    """

    loop_feedback = context.get("loop_feedback") if isinstance(context.get("loop_feedback"), dict) else {}
    current_repair = (
        loop_feedback.get("current_round_repair")
        if isinstance(loop_feedback.get("current_round_repair"), dict)
        else {}
    )
    repair_targets = (
        current_repair.get("repair_targets")
        if isinstance(current_repair.get("repair_targets"), dict)
        else {}
    )
    semantic_target = (
        repair_targets.get("algorithm_semantic_review")
        if isinstance(repair_targets.get("algorithm_semantic_review"), dict)
        else {}
    )
    baseline_memory = (
        loop_feedback.get("agent_generated_baseline_memory")
        if isinstance(loop_feedback.get("agent_generated_baseline_memory"), dict)
        else {}
    )
    baseline_review = (
        baseline_memory.get("semantic_review")
        if isinstance(baseline_memory.get("semantic_review"), dict)
        else {}
    )
    experience = (
        loop_feedback.get("experience_memory")
        if isinstance(loop_feedback.get("experience_memory"), dict)
        else {}
    )
    semantic_memory = (
        experience.get("algorithm_semantic_memory")
        if isinstance(experience.get("algorithm_semantic_memory"), dict)
        else {}
    )

    blocking_findings = [
        item
        for item in semantic_target.get("blocking_findings") or []
        if isinstance(item, dict)
    ][:8]
    required_capabilities = [
        str(item.get("repair") or "").strip()
        for item in blocking_findings
        if str(item.get("repair") or "").strip()
    ]
    required_tests = [
        str(item.get("required_test") or "").strip()
        for item in blocking_findings
        if str(item.get("required_test") or "").strip()
    ]
    for item in semantic_memory.get("required_behavioral_tests") or []:
        text = str(item).strip()
        if text and text not in required_tests:
            required_tests.append(text)

    if semantic_target:
        stage = "semantic_repair_required"
        evidence_source = "current_round_repair.algorithm_semantic_review"
    elif baseline_review:
        stage = "semantic_review_available"
        evidence_source = "agent_generated_baseline_memory.semantic_review"
    else:
        stage = "semantic_evidence_unavailable"
        evidence_source = "none"
    return {
        "stage": stage,
        "evidence_source": evidence_source,
        "semantic_review_status": baseline_review.get("status"),
        "blocking_findings": blocking_findings,
        "required_capabilities": required_capabilities[:8],
        "required_behavioral_tests": required_tests[:12],
        "rule": "No method capability is inferred from function, class, or variable names.",
    }


def required_next_operator_capabilities(method_stage: dict[str, Any]) -> list[str]:
    values = method_stage.get("required_capabilities") or []
    return [str(item) for item in values if str(item).strip()][:8]


def repair_blocks_operator_stage(current_repair: dict[str, Any]) -> bool:
    signatures = repair_failure_signatures(current_repair)
    if not signatures:
        return False
    nonblocking = {
        "no_changed_files_after_apply",
        "quick_test_plan_does_not_reference_intake_test_command",
        "priority_knowledge_cards_not_referenced",
    }
    if all(any(token in signature for token in nonblocking) for signature in signatures):
        return False
    blocking_tokens = {
        "python_compile",
        "syntaxerror",
        "indentationerror",
        "agent_generated_runtime_import",
        "active_io_parser",
        "declared_output_schema",
        "operation_level_ready_list_constructor",
        "complete_schedule_coverage_guard",
        "machine_eligibility_guard",
        "processing_duration_guard",
        "job_precedence_guard",
        "machine_non_overlap_guard",
        "standalone_cli_interface",
    }
    return any(any(token in signature for token in blocking_tokens) for signature in signatures)


def repair_failure_signatures(current_repair: dict[str, Any]) -> list[str]:
    signatures: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            signatures.append(value.strip().lower())

    for item in current_repair.get("avoid") or []:
        add(item)
    for attempt in current_repair.get("previous_attempts") or []:
        if not isinstance(attempt, dict):
            continue
        for signature in attempt.get("failure_signatures") or []:
            add(signature)
        summary = attempt.get("summary") if isinstance(attempt.get("summary"), dict) else {}
        validation = summary.get("validation_summary") if isinstance(summary.get("validation_summary"), dict) else {}
        judgment = validation.get("agentic_judgment") if isinstance(validation.get("agentic_judgment"), dict) else {}
        for issue in judgment.get("issues") or []:
            add(issue)
        for warning in (judgment.get("checks") or {}).get("proposal_audit_warnings") or []:
            add(warning)
    return signatures


def incumbent_source_text(context: dict[str, Any]) -> str:
    code_context = context.get("incumbent_code_context")
    if not isinstance(code_context, dict):
        return ""
    snippets: list[str] = []
    for item in code_context.get("files") or []:
        if isinstance(item, dict):
            snippets.append(str(item.get("snippet") or ""))
    return "\n\n".join(snippets)


def priority_knowledge_card_limit(context: dict[str, Any]) -> int:
    if operator_improvement_stage_for_worker(context).get("active"):
        return max(PRIORITY_KNOWLEDGE_CARD_LIMIT, 5)
    return PRIORITY_KNOWLEDGE_CARD_LIMIT


def _finite_objective_key(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_finite_number(item) for item in value)


def priority_context_max_chars() -> int:
    raw_value = os.getenv("ALGOFORGE_PRIORITY_CONTEXT_MAX_CHARS")
    if raw_value is None:
        return PRIORITY_CONTEXT_DEFAULT_MAX_CHARS
    try:
        requested = int(raw_value)
    except ValueError:
        return PRIORITY_CONTEXT_DEFAULT_MAX_CHARS
    return max(PRIORITY_CONTEXT_MIN_CHARS, min(PRIORITY_CONTEXT_MAX_CHARS, requested))


# === 上下文压缩：把主流程大对象裁成 prompt 可消费的动态尾部 ======================

def compact_loop_feedback_for_prompt(loop_feedback: dict[str, Any]) -> dict[str, Any]:
    """压缩 loop_feedback，保留 worker 本轮真正需要消费的信号。

    这里重点保留前几轮 proposal 结果、repair、semantic review 和 smoke gate，
    让 worker 能理解“为什么上轮被拒绝”以及“这一轮还剩什么没修”。
    """
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
                "agent_generated_unwired_helpers": (
                    ((diagnostics.get("proposal_audit") or {}).get("agent_generated_unwired_helpers") or [])[:8]
                ),
                "solver_contract_self_check_audit": compact_solver_contract_self_check_audit_for_prompt(
                    ((diagnostics.get("proposal_audit") or {}).get("solver_contract_self_check") or {})
                ),
                "algorithm_semantic_review": compact_algorithm_semantic_review_for_prompt(
                    item.get("semantic_review") or diagnostics.get("algorithm_semantic_review") or {}
                ),
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
        "algorithm_semantic_memory": compact_algorithm_semantic_memory(
            value.get("algorithm_semantic_memory") or {}
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


def compact_algorithm_semantic_memory(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    return {
        "attempt_count": value.get("attempt_count"),
        "repair_required_attempt_count": value.get("repair_required_attempt_count"),
        "warning_attempt_count": value.get("warning_attempt_count"),
        "recovered_direction_count": value.get("recovered_direction_count"),
        "recurring_categories": (value.get("recurring_categories") or [])[:6],
        "recurring_repairs": (value.get("recurring_repairs") or [])[:6],
        "required_behavioral_tests": (value.get("required_behavioral_tests") or [])[:8],
        "knowledge_paths": (value.get("knowledge_paths") or [])[:10],
        "next_prompt_rule": str(value.get("next_prompt_rule") or "")[:1200],
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
                "agent_generated_unwired_helpers": (audit.get("agent_generated_unwired_helpers") or [])[:8],
                "solver_contract_self_check_audit": compact_solver_contract_self_check_audit_for_prompt(
                    audit.get("solver_contract_self_check") or {}
                ),
                "algorithm_semantic_review": compact_algorithm_semantic_review_for_prompt(
                    attempt.get("semantic_review") or {}
                ),
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
    for key in ("algorithm_semantic_review",):
        item = value.get(key)
        if isinstance(item, dict) and item:
            compact[key] = item
    return compact


def compact_algorithm_semantic_review_for_prompt(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    findings = []
    for item in value.get("findings") or []:
        if not isinstance(item, dict):
            continue
        findings.append(
            {
                "finding_id": item.get("finding_id"),
                "category": item.get("category"),
                "severity": item.get("severity"),
                "blocking": item.get("blocking"),
                "confidence": item.get("confidence"),
                "source_path": item.get("source_path"),
                "line_start": item.get("line_start"),
                "line_end": item.get("line_end"),
                "knowledge_path": item.get("knowledge_path"),
                "knowledge_quote": str(item.get("knowledge_quote") or "")[:1200],
                "explanation": str(item.get("explanation") or "")[:1200],
                "repair": str(item.get("repair") or "")[:1200],
                "required_test": str(item.get("required_test") or "")[:900],
            }
        )
        if len(findings) >= 8:
            break
    return {
        "status": value.get("status"),
        "accepted": value.get("accepted"),
        "summary": str(value.get("summary") or "")[:800],
        "findings": findings,
        "knowledge_paths": (value.get("knowledge_paths") or [])[:12],
        "artifacts": value.get("artifacts") or {},
    }


def compact_solver_contract_self_check_audit_for_prompt(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {}
    keys = [
        "required",
        "present",
        "changed_agent_generated_solver",
        "missing_active_features",
        "missing_capabilities",
        "missing_variant_handling",
        "missing_narrative_fields",
        "capabilities_without_evidence",
        "capabilities_with_vague_evidence",
        "capabilities_without_concrete_source_evidence",
        "capabilities_with_source_mismatch",
        "narrative_without_concrete_source_evidence",
        "narrative_with_source_mismatch",
        "warnings",
    ]
    compact: dict[str, Any] = {}
    for key in keys:
        item = value.get(key)
        if isinstance(item, list):
            compact[key] = item[:12]
        elif item not in (None, "", [], {}):
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
    """截取 incumbent 代码的高价值窗口，而不是盲目头尾截断。"""
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


# === 知识卡与变体感知：给 worker 选最相关的局部方法证据 ==========================

def compact_priority_knowledge_cards(
    context: dict[str, Any],
    *,
    limit: int,
    max_chars_per_card: int = 2400,
) -> list[dict[str, Any]]:
    """为本轮挑选少量高相关知识卡。

    这里会同时考虑 active method package、当前方向、query terms 和变体特征。
    例如 SDST 未激活时，相关知识会被主动降权，避免把不应出现的变体逻辑
    混进本轮 proposal。
    """
    cards = context.get("knowledge_cards") or []
    if not isinstance(cards, list):
        return []
    typed_cards = [card for card in cards if isinstance(card, dict)]
    if not typed_cards:
        return []

    query_terms = _knowledge_query_terms(context)
    sdst_active = _context_sequence_dependent_setup_active(context)
    active_package = context.get("active_method_package") if isinstance(context.get("active_method_package"), dict) else {}
    package_asset_paths = {
        str(value).replace("\\", "/").lower()
        for value in active_package.get("assets") or []
        if str(value).strip()
    }
    loop_feedback = context.get("loop_feedback") if isinstance(context.get("loop_feedback"), dict) else {}
    direction = (
        loop_feedback.get("current_direction_plan")
        if isinstance(loop_feedback.get("current_direction_plan"), dict)
        else {}
    )
    direction_paths = {
        str(value).replace("\\", "/").lower()
        for value in direction.get("knowledge_paths") or []
        if str(value).strip()
    }

    def card_score(card: dict[str, Any]) -> int:
        path = str(card.get("path") or "").lower()
        snippet = str(card.get("snippet") or "").lower()
        haystack = f"{path}\n{snippet}"
        normalized_haystack = haystack.replace("-", "_")
        if not sdst_active and _sdst_specific_knowledge_path(path):
            return -10000
        score = 0
        if path.replace("\\", "/") in package_asset_paths:
            score += 1200
        if path.replace("\\", "/") in direction_paths:
            score += 900
        for term in query_terms:
            if term and term in normalized_haystack:
                score += 4
        if "sdst" in query_terms and "sdst" in haystack:
            score += 25
        return score

    selected: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    def append_card(card: dict[str, Any]) -> None:
        if len(selected) >= limit:
            return
        path = str(card.get("path") or "")
        if not path or path in seen_paths:
            return
        score = card_score(card)
        if score <= -1000:
            return
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

    for package_path in package_asset_paths:
        for card in typed_cards:
            if str(card.get("path") or "").replace("\\", "/").lower() == package_path:
                append_card(card)
                break
        if len(selected) >= limit:
            break

    ranked = sorted(enumerate(typed_cards), key=lambda item: (card_score(item[1]), -item[0]), reverse=True)
    for _index, card in ranked:
        path = str(card.get("path") or "")
        if not path or path in seen_paths:
            continue
        score = card_score(card)
        if score <= -1000:
            continue
        if score <= 0 and selected:
            continue
        append_card(card)
        if len(selected) >= limit:
            break
    return selected


def compact_knowledge_card_snippet(snippet: str, *, query_terms: set[str], max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(snippet) <= max_chars:
        return snippet

    lower = snippet.lower().replace("_", "-")
    needles = ["local evidence", "what to preserve", "avoid", "required behavior", "acceptance checks"]
    for term in sorted(query_terms):
        normalized = term.lower().replace("_", "-")
        if len(normalized) >= 5 and normalized not in {"agent", "generated"}:
            needles.append(normalized)
        for component in normalized.split("-"):
            if len(component) >= 5 and component not in {"agent", "generated"}:
                needles.append(component)

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
    sdst_active = _context_sequence_dependent_setup_active(context)

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

    package = context.get("active_method_package")
    if isinstance(package, dict):
        add_text(package.get("package_id"))
        add_text(package.get("title"))
        add_text(package.get("description"))
        for item in package.get("strategy_types") or []:
            add_text(item)

    feedback = context.get("loop_feedback") if isinstance(context.get("loop_feedback"), dict) else {}
    direction_plan = feedback.get("current_direction_plan") if isinstance(feedback.get("current_direction_plan"), dict) else {}
    add_text(direction_plan.get("strategy_type"))
    add_text(direction_plan.get("hypothesis"))
    add_text(direction_plan.get("method_package_id"))

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

    if sdst_active:
        terms.add("sdst")
    else:
        _remove_inactive_sdst_terms(terms)
    if any("agent_generated" in term or term == "generated" for term in terms):
        terms.add("agent_generated")
    if "local_search_operator" in terms:
        terms.add("local_search")
    return terms


def _context_sequence_dependent_setup_active(context: dict[str, Any]) -> bool:
    """从 slot、实例诊断和上下文文本综合判断 SDST 是否真的激活。"""
    if _slot_manifest_requests_sdst(context.get("slot_manifest")):
        return True
    diagnostics = context.get("instance_diagnostics") if isinstance(context.get("instance_diagnostics"), dict) else {}
    diagnostic_state = _sdst_state_from_diagnostics(diagnostics)
    if diagnostic_state is not None:
        return diagnostic_state

    active_text = json.dumps(
        {
            "task": context.get("task"),
            "evaluator_protocol": context.get("evaluator_protocol"),
            "hypothesis": context.get("hypothesis"),
            "contract_review_evidence": context.get("contract_review_evidence"),
            "slot_manifest": context.get("slot_manifest"),
        },
        ensure_ascii=False,
    ).lower()
    return _mentions_sdst_feature(active_text)


def _slot_manifest_requests_sdst(slot_manifest: Any) -> bool:
    if not isinstance(slot_manifest, dict):
        return False
    for slot in slot_manifest.get("slots") or []:
        if not isinstance(slot, dict) or not slot.get("user_confirmed"):
            continue
        slot_id = str(slot.get("slot_id") or "").strip().lower()
        tags = " ".join(str(tag) for tag in slot.get("knowledge_tags") or [])
        if _mentions_sdst_feature(f"{slot_id} {tags}"):
            return True
    return False


def _sdst_state_from_diagnostics(diagnostics: dict[str, Any]) -> bool | None:
    """优先信任实例诊断结果，而不是只靠关键词猜测变体。"""
    if not isinstance(diagnostics, dict) or not diagnostics:
        return None
    summary = diagnostics.get("summary") if isinstance(diagnostics.get("summary"), dict) else {}
    instances = [item for item in diagnostics.get("instances") or [] if isinstance(item, dict)]
    profiled_count = int(summary.get("profiled_count") or 0)
    instance_count = int(summary.get("instance_count") or 0)
    diagnostics_have_shape = (
        diagnostics.get("status") in {"available", "partial"}
        and (profiled_count > 0 or instance_count > 0 or bool(instances))
    )
    if not diagnostics_have_shape:
        return None
    setup_kinds = [str(kind).strip().lower() for kind in summary.get("setup_time_kinds") or []]
    if int(summary.get("sdst_instance_count") or 0) > 0:
        return True
    if any(kind not in {"", "none", "null"} for kind in setup_kinds):
        return True
    for item in instances:
        variant = str(item.get("variant") or "").strip().lower()
        setup_kind = str(item.get("setup_time_kind") or "").strip().lower()
        if variant == "fjsp_sdst" or setup_kind not in {"", "none", "null"}:
            return True
    return False


def _mentions_sdst_feature(text: str) -> bool:
    return bool(
        re.search(
            r"\bfjsp[-_]?sdst\b|\bsd-st\b|\bsequence[-_\s]?dependent[-_\s]?setup\b|"
            r"\bsetup[-_\s]?matrix\b|\bsetup[-_\s]?time(?:s)?\b|\boddla\d*\b",
            str(text),
            flags=re.I,
        )
    )


def _remove_inactive_sdst_terms(terms: set[str]) -> None:
    blocked = {
        "sdst",
        "fjsp_sdst",
        "sequence_dependent_setup",
        "setup_matrix",
        "setup_time",
        "setup_times",
        "setup_aware_dispatch_or_insertion",
        "sdst_io_contract",
        "oddla",
        "oddla20",
        "hudata",
    }
    for term in list(terms):
        if term in blocked or "fjsp_sdst" in term:
            terms.discard(term)


def _sdst_specific_knowledge_path(path: str) -> bool:
    normalized = str(path).replace("\\", "/").lower()
    return any(
        marker in normalized
        for marker in (
            "fjsp_sdst",
            "_sdst_",
            "sdst_hudata",
            "sdst_fattahi",
            "decoder_neighborhood.md",
        )
    )


# === Proposal 规范化与审计：把模型输出变成编排层可消费事实 ========================

def normalize_context_usage(value: Any) -> dict[str, Any]:
    """规范化模型声明的上下文使用记录，便于后续审计引用是否真实。"""
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
    """把 solver self-check 统一成可比对的结构。

    该结构不是模型自夸文本，而是后续 audit 用来对照 quality contract、
    检查 active_features / capabilities / narrative evidence 是否齐全的输入。
    """
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
    """规范化 rule/operator 假设，保留后续 lineage 分析所需字段。"""
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
    """生成 proposal 审计摘要。

    审计的目的不是再次执行代码，而是回答几个编排层问题：
    - 这轮是否真的引用了 project_intake / knowledge cards；
    - 触碰了哪些核心文件、validator、benchmark；
    - self-check 与 helper 可达性是否存在明显风险。
    """
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
    unwired_helpers = agent_generated_unwired_helper_warnings(
        proposal,
        quality_contract=quality_contract,
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
    if unwired_helpers:
        warnings.append("agent_generated_solver_unwired_helper")
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
        "agent_generated_unwired_helpers": unwired_helpers,
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
    """审计 agent-generated solver 的 self-check 是否足以支撑可追溯声明。"""
    warnings: list[str] = []
    changed_agent_generated_solver = any(_looks_like_agent_generated_solver_path(path) for path in accepted_paths)
    if not quality_contract.get("enabled") or not changed_agent_generated_solver:
        return {
            "required": False,
            "present": bool(self_check.get("present")),
            "changed_agent_generated_solver": changed_agent_generated_solver,
            "missing_active_features": [],
            "missing_capabilities": [],
            "missing_variant_handling": [],
            "missing_narrative_fields": [],
            "capabilities_without_evidence": [],
            "capabilities_without_concrete_source_evidence": [],
            "capabilities_with_source_mismatch": [],
            "narrative_without_concrete_source_evidence": [],
            "narrative_with_source_mismatch": [],
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
            "missing_variant_handling": quality_contract.get("variant_required_code_capabilities", []),
            "missing_narrative_fields": _required_solver_self_check_narrative_fields(
                quality_contract,
                include_variant=True,
            ),
            "capabilities_without_evidence": [],
            "capabilities_without_concrete_source_evidence": [],
            "capabilities_with_source_mismatch": [],
            "narrative_without_concrete_source_evidence": [],
            "narrative_with_source_mismatch": [],
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

    variant_required = [
        str(item)
        for item in quality_contract.get("variant_required_code_capabilities") or []
        if isinstance(item, str)
    ]
    missing_variant_handling = variant_required if variant_required and not self_check.get("variant_handling") else []
    if missing_variant_handling:
        warnings.append("agent_generated_solver_self_check_missing_variant_handling")

    required_narrative_fields = _required_solver_self_check_narrative_fields(
        quality_contract,
        include_variant=bool(variant_required),
    )
    missing_narrative_fields = sorted(
        field
        for field in required_narrative_fields
        if not _self_check_narrative_text(self_check.get(field)).strip()
    )
    if missing_narrative_fields:
        warnings.append("agent_generated_solver_self_check_missing_narrative_evidence")

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
    if source_evidence["narrative_without_concrete_source_evidence"]:
        warnings.append("agent_generated_solver_self_check_narrative_no_concrete_source_evidence")
    if source_evidence["narrative_source_mismatch"]:
        warnings.append("agent_generated_solver_self_check_narrative_source_mismatch")

    return {
        "required": True,
        "present": True,
        "changed_agent_generated_solver": changed_agent_generated_solver,
        "missing_active_features": missing_features,
        "missing_capabilities": missing_capabilities,
        "missing_variant_handling": missing_variant_handling,
        "missing_narrative_fields": missing_narrative_fields,
        "capabilities_without_evidence": capabilities_without_evidence,
        "capabilities_with_vague_evidence": vague_capability_evidence,
        "capabilities_without_concrete_source_evidence": source_evidence[
            "without_concrete_source_evidence"
        ],
        "capabilities_with_source_mismatch": source_evidence["source_mismatch"],
        "narrative_without_concrete_source_evidence": source_evidence[
            "narrative_without_concrete_source_evidence"
        ],
        "narrative_with_source_mismatch": source_evidence["narrative_source_mismatch"],
        "warnings": warnings,
    }


def agent_generated_unwired_helper_warnings(
    proposal: dict[str, Any],
    *,
    quality_contract: dict[str, Any],
    accepted_paths: list[str],
) -> list[str]:
    if not quality_contract.get("enabled"):
        return []
    if not any(_looks_like_agent_generated_solver_path(path) for path in accepted_paths):
        return []
    source_text = _agent_generated_full_file_source_text(proposal)
    if not source_text.strip():
        return []
    return _unwired_generated_helper_warnings(source_text)


def _agent_generated_full_file_source_text(proposal: dict[str, Any]) -> str:
    parts: list[str] = []
    for change in proposal.get("changes") or []:
        if not isinstance(change, dict):
            continue
        if str(change.get("action") or "") != "create_or_replace":
            continue
        if not _looks_like_agent_generated_solver_path(str(change.get("path") or "")):
            continue
        content = change.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def _unwired_generated_helper_warnings(source_text: str) -> list[str]:
    patterns = [
        (
            "parser",
            r"^def\s+(_*(?:parse|read|load)[A-Za-z0-9_]*(?:instance|problem|input)[A-Za-z0-9_]*)\s*\(",
        ),
        (
            "decoder",
            r"^def\s+(_*(?:decode|build|construct)[A-Za-z0-9_]*(?:schedule|solution)[A-Za-z0-9_]*)\s*\(",
        ),
        (
            "source-level self-check",
            r"^def\s+(_*(?:validate|self_check|check|assert)[A-Za-z0-9_]*(?:schedule|solution|feasible|valid)[A-Za-z0-9_]*)\s*\(",
        ),
    ]
    return [
        f"{label} `{name}` is defined but not reachable from generated solver entry flow"
        for label, name in unreachable_defined_function_helpers(source_text, patterns)
    ]


def _solver_capability_evidence_is_vague(evidence: str) -> bool:
    stripped = evidence.strip().lower()
    if not stripped:
        return False
    vague_values = {"done", "implemented", "yes", "ok", "handled", "supported", "complete"}
    if stripped in vague_values:
        return True
    return len(stripped) < 20 and not any(token in stripped for token in ("def ", "parse", "decode", "guard", "check", "main"))


def _required_solver_self_check_narrative_fields(
    quality_contract: dict[str, Any],
    *,
    include_variant: bool,
) -> list[str]:
    fields = ["representation", "decoder", "runtime_bounds", "incumbent_preservation"]
    if include_variant and quality_contract.get("variant_required_code_capabilities"):
        fields.append("variant_handling")
    return fields


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
            "narrative_without_concrete_source_evidence": [],
            "narrative_source_mismatch": [],
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
    narrative_without_concrete: list[str] = []
    narrative_source_mismatch: list[str] = []
    for field in _self_check_present_narrative_fields(self_check):
        evidence = _self_check_narrative_text(self_check.get(field))
        tokens = [
            token
            for token in _solver_capability_evidence_tokens(evidence)
            if token != field and token not in expected_capabilities
        ]
        if not tokens:
            narrative_without_concrete.append(field)
            continue
        missing = [token for token in tokens if token.lower() not in source_lower]
        if len(missing) == len(tokens):
            narrative_source_mismatch.append(field)
    return {
        "without_concrete_source_evidence": without_concrete,
        "source_mismatch": source_mismatch,
        "narrative_without_concrete_source_evidence": narrative_without_concrete,
        "narrative_source_mismatch": narrative_source_mismatch,
    }


def _self_check_present_narrative_fields(self_check: dict[str, Any]) -> list[str]:
    fields = ["representation", "decoder", "variant_handling", "runtime_bounds", "incumbent_preservation"]
    return [field for field in fields if _self_check_narrative_text(self_check.get(field)).strip()]


def _self_check_narrative_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "")


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


# === Path / slot / apply 门禁：把 proposal 安全落到 worktree =====================

def path_matches_any(path: str, roots: set[str]) -> bool:
    return any(_path_is_under(path, root) for root in roots if root)


def safe_profile_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe[:64] or "local_search_profile"


def normalize_relative_path(value: str) -> str:
    """把模型给出的路径统一收敛到 worktree 相对路径表示。"""
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
    """在增量轮次保护 incumbent，禁止把现有 solver 整体重写掉。"""
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
    """判断 incumbent 是否已失去“可继续增量修改”的合法基础。"""
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
    """执行路径门禁。

    这一步把 edit_policy、保留目录限制和基础路径净化统一成一个布尔判断，
    是 proposal 从“模型建议”变成“允许尝试 apply 的候选动作”的第一道关。
    """
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
    """把已规范化 proposal 写入 worktree。

    apply 阶段仍然会重复做路径与 slot 校验，防止上游数据在磁盘往返后被直接
    信任。若某条动作在落地时发现锚点、slot 或目标文件不满足条件，会记入
    `apply_rejections`，而不是静默吞掉。
    """
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
            # slot 替换是最严格的落地路径：必须是用户确认过的 slot，且实际
            # target_file 与 proposal 声明一致，避免模型借 slot 名义越界写别处。
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
    """解析并确认一个允许被 `replace_slot_block` 修改的上下文 slot。"""
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
    """清理 slot 替换内容，只保留 marker 之间真正应写回的代码。"""
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
    """把结构化 proposal 渲染成便于人工审阅的 Markdown 报告。"""
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
                f"- agent_generated_unwired_helpers: `{json.dumps(audit.get('agent_generated_unwired_helpers') or [], ensure_ascii=False)}`",
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
