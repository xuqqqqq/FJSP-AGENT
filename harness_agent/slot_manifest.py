from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CodeSlotSpec:
    slot_id: str
    title: str
    target_file: str
    marker_start: str
    marker_end: str
    slot_kind: str
    language: str
    purpose: str
    inputs: list[str]
    outputs: list[str]
    invariants: list[str]
    allowed_edits: list[str]
    forbidden_edits: list[str]
    validation_commands: list[str] = field(default_factory=list)
    knowledge_tags: list[str] = field(default_factory=list)
    user_confirmed: bool = False

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SlotManifest:
    schema_version: int
    problem_family: str
    status: str
    slots: list[CodeSlotSpec]
    confirmation_required: bool = True
    notes: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "problem_family": self.problem_family,
            "status": self.status,
            "confirmation_required": self.confirmation_required,
            "notes": self.notes,
            "slots": [slot.to_payload() for slot in self.slots],
        }


def default_standard_fjsp_slot_manifest(*, confirmed: bool = False) -> SlotManifest:
    slots = [
        CodeSlotSpec(
            slot_id="awls_zi_policy",
            title="AWLS 自适应 zi 权重策略",
            target_file="examples/awls_evolved_slots.py",
            marker_start="# EVOLVE_START",
            marker_end="# EVOLVE_END",
            slot_kind="function_body",
            language="python",
            purpose="控制 AWLS 邻域动作打分中的 zi 数值扰动策略。",
            inputs=[
                "values['base']：固定 AWLS 外壳传入的基础 zi 分数",
                "values['weight']：操作级自适应权重",
                "values['cooldown']：操作冷却/时间信号",
                "values['rr'], values['gamma'], values['cooling']",
                "values['is_critical'], values['forward'], values['backward']",
                "values['duration'], values['machine_load'], values['position']",
            ],
            outputs=["返回有限的非负 float；外层 wrapper 会裁剪不安全数值。"],
            invariants=[
                "函数名必须保持 evolved_zi(values)。",
                "禁止 import、subprocess、文件 IO、随机数、网络访问或读取评测器。",
                "不得改变 solver 的输入/输出 schema。",
            ],
            allowed_edits=[
                "只改写 EVOLVE 标记内部的 evolved_zi 函数体。",
                "允许使用算术、本地变量、values.get(...)、if/else 和白名单数值函数。",
            ],
            forbidden_edits=[
                "禁止修改 parser、evaluator 或 benchmark 文件。",
                "禁止修改解 JSON schema。",
                "禁止修改 AWLS 图结构/状态数据结构。",
            ],
            validation_commands=[
                "python -m compileall examples/awls_evolved_slots.py examples/standard_fjsp_awls_solver.py",
                "python examples/standard_fjsp_awls_solver.py --input examples/fjsp.brandimarte.Mk01.m6j10c3.txt --output outputs/slot_smoke.json --zi-policy slot --time-limit-sec 1",
            ],
            knowledge_tags=["awls", "zi", "adaptive_weight", "move_scoring"],
            user_confirmed=confirmed,
        ),
        CodeSlotSpec(
            slot_id="local_search_neighborhood_actions",
            title="局部搜索邻域动作生成",
            target_file="examples/standard_fjsp_local_search_solver.py",
            marker_start="# SLOT neighborhood_actions START",
            marker_end="# SLOT neighborhood_actions END",
            slot_kind="marked_block",
            language="python",
            purpose="为已解码的标准 FJSP 排程生成可验证的候选改进动作。",
            inputs=[
                "instance：固定 StandardFjspInstance",
                "state：当前机器分配和机器序列",
                "decoded：当前排程、makespan、前驱后继和拓扑顺序",
                "rng：带 seed 的随机源",
                "neighbor_limit：候选动作数量上限",
            ],
            outputs=["返回有界的 Move 对象列表，必须兼容 apply_move/decode_state。"],
            invariants=[
                "不得改变 Move 字段或 SearchState/DecodedState schema。",
                "所有动作必须仍可被 decode_state 和 validate_standard_schedule 检查。",
                "不得修改 evaluator、parser 或 IO 契约。",
            ],
            allowed_edits=[
                "允许在标记槽内新增或调整 move generator。",
                "允许使用上下文中已有的关键路径/关键块、机器负载、空闲间隙和候选机器信号。",
            ],
            forbidden_edits=[
                "禁止改变 benchmark/evaluator 语义。",
                "禁止改变命令行参数或解输出 schema。",
                "禁止创建无界候选列表或非确定性外部副作用。",
            ],
            validation_commands=[
                "python -m compileall examples/standard_fjsp_local_search_solver.py",
                "python examples/standard_fjsp_local_search_solver.py --input examples/fjsp.brandimarte.Mk01.m6j10c3.txt --output outputs/neighborhood_slot_smoke.json --time-limit-sec 1",
            ],
            knowledge_tags=["critical_path", "critical_block", "neighborhood", "machine_reassignment"],
            user_confirmed=confirmed,
        ),
    ]
    return SlotManifest(
        schema_version=1,
        problem_family="standard_fjsp",
        status="confirmed" if confirmed else "draft_requires_user_confirmation",
        confirmation_required=not confirmed,
        notes=[
            "代码槽是带明确输入、输出和不变量的功能编辑区域。",
            "LLM 获准修改代码槽之前，必须先经过用户确认。",
            "除非确认新的 IO 契约，否则 evaluator/parser/metric 语义保持固定。",
        ],
        slots=slots,
    )


def load_slot_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def selected_standard_fjsp_slot_manifest(*, selected_slot_ids: list[str]) -> SlotManifest:
    selected = {str(slot_id) for slot_id in selected_slot_ids if str(slot_id).strip()}
    if not selected:
        raise ValueError("at least one selected slot_id is required")
    manifest = default_standard_fjsp_slot_manifest(confirmed=False)
    known = {slot.slot_id for slot in manifest.slots}
    unknown = sorted(selected - known)
    if unknown:
        raise ValueError(f"unknown standard_fjsp slot_id(s): {', '.join(unknown)}")
    slots = [
        CodeSlotSpec(
            **{
                **slot.to_payload(),
                "user_confirmed": slot.slot_id in selected,
            }
        )
        for slot in manifest.slots
    ]
    return SlotManifest(
        schema_version=manifest.schema_version,
        problem_family=manifest.problem_family,
        status="confirmed",
        confirmation_required=False,
        notes=manifest.notes
        + [
            "Only selected slots have user_confirmed=true; unselected slots remain locked.",
        ],
        slots=slots,
    )


def write_default_slot_manifest(*, problem_family: str, output: Path, confirmed: bool = False) -> Path:
    normalized_family = str(problem_family).strip().lower()
    if normalized_family not in {"fjsp", "standard_fjsp"}:
        raise ValueError(f"no default slot manifest is available for problem family: {problem_family}")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = default_standard_fjsp_slot_manifest(confirmed=confirmed).to_payload()
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def write_selected_slot_manifest(*, problem_family: str, output: Path, selected_slot_ids: list[str]) -> Path:
    normalized_family = str(problem_family).strip().lower()
    if normalized_family not in {"fjsp", "standard_fjsp"}:
        raise ValueError(f"no default slot manifest is available for problem family: {problem_family}")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = selected_standard_fjsp_slot_manifest(selected_slot_ids=selected_slot_ids).to_payload()
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output
