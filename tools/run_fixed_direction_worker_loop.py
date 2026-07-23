"""Run a worker loop with a previously validated Main direction plan."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from harness_agent.agents.main import (
    DirectionPlanRequest,
    EvidenceDrivenMainAgent,
    WorkerAssignmentIssue,
    WorkerAssignmentRequest,
)
from harness_agent.agents.semantic import DeepSeekAlgorithmSemanticReviewer
from harness_agent.core.models import TaskContract
from harness_agent.orchestration.loop import run_worker_loop
from harness_agent.workers.opencode_worker import OpenCodeWorker


class FixedDirectionAgent:
    """Replay one audited Main plan while retaining deterministic assignment compilation."""

    def __init__(self, direction_plan_path: Path) -> None:
        self.direction_plan = json.loads(direction_plan_path.read_text(encoding="utf-8"))
        self.assignment_compiler = EvidenceDrivenMainAgent()

    def plan_direction(self, request: DirectionPlanRequest) -> dict:
        del request
        return copy.deepcopy(self.direction_plan)

    def issue_worker_assignment(self, request: WorkerAssignmentRequest) -> WorkerAssignmentIssue:
        return self.assignment_compiler.issue_worker_assignment(request)

    def revise_worker_assignment(self, request: WorkerAssignmentRequest) -> WorkerAssignmentIssue:
        return self.assignment_compiler.revise_worker_assignment(request)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--context-packet", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--direction-plan", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", default="openai/gpt-5.4")
    parser.add_argument("--variant", default="high")
    parser.add_argument("--max-competing-workers", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--repair-attempts", type=int, default=2)
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--max-runtime-seconds", type=int, default=86400)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_worker_loop(
        contract=TaskContract.load(args.contract),
        project_root=args.project_root,
        output_dir=args.output_dir,
        context_packet_path=args.context_packet,
        worker=OpenCodeWorker(model=args.model, variant=args.variant),
        main_agent=FixedDirectionAgent(args.direction_plan),
        semantic_reviewer=DeepSeekAlgorithmSemanticReviewer(),
        experiment_id="fixed_direction_competition",
        iterations=1,
        max_steps=max(1, args.max_steps),
        max_runtime_seconds=max(1, args.max_runtime_seconds),
        apply_worker_changes=True,
        promotion_repeats=1,
        baseline_source="current_project",
        in_round_repair_attempts=max(0, args.repair_attempts),
        max_competing_workers=args.max_competing_workers,
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "baseline_key": list(result.baseline_key),
                "final_key": list(result.final_key),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
