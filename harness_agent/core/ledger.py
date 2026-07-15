"""追加式实验账本，保存每次评测和晋升决策。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExperimentRecord:
    """一个 instance/seed/round 原子实验的不可变事实记录。"""

    experiment_id: str
    task_id: str
    round_index: int
    instance_id: str
    seed: int
    status: str
    valid: bool
    objective_key: tuple[float, ...]
    metrics: dict[str, Any]
    paths: dict[str, str]
    error: str | None = None


class ExperimentLedger:
    """SQLite 实验账本；负责持久化事实，不参与候选优劣判断。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            create table if not exists experiments (
                experiment_id text primary key,
                task_id text not null,
                round_index integer not null,
                instance_id text not null,
                seed integer not null,
                status text not null,
                valid integer not null,
                objective_key_json text not null,
                metrics_json text not null,
                paths_json text not null,
                error text
            )
            """
        )
        self.conn.commit()

    def record(self, record: ExperimentRecord) -> None:
        self.conn.execute(
            """
            insert or replace into experiments (
                experiment_id,
                task_id,
                round_index,
                instance_id,
                seed,
                status,
                valid,
                objective_key_json,
                metrics_json,
                paths_json,
                error
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.experiment_id,
                record.task_id,
                record.round_index,
                record.instance_id,
                record.seed,
                record.status,
                1 if record.valid else 0,
                json.dumps(record.objective_key, ensure_ascii=False),
                json.dumps(record.metrics, ensure_ascii=False),
                json.dumps(record.paths, ensure_ascii=False),
                record.error,
            ),
        )
        self.conn.commit()

    def list_records(self) -> list[ExperimentRecord]:
        rows = self.conn.execute(
            """
            select experiment_id, task_id, round_index, instance_id, seed, status,
                   valid, objective_key_json, metrics_json, paths_json, error
            from experiments
            order by round_index, instance_id, seed
            """
        ).fetchall()
        return [
            ExperimentRecord(
                experiment_id=row[0],
                task_id=row[1],
                round_index=int(row[2]),
                instance_id=row[3],
                seed=int(row[4]),
                status=row[5],
                valid=bool(row[6]),
                objective_key=tuple(float(item) for item in json.loads(row[7])),
                metrics=json.loads(row[8]),
                paths=json.loads(row[9]),
                error=row[10],
            )
            for row in rows
        ]
