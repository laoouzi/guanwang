from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from typing import Any

from .store import JsonStore


class Ledger:
    """绩效账本：完成数 / 平均耗时 / 总成本 / 驳回率 / 人类介入率。"""

    def __init__(self):
        self._completions: dict[str, list[dict[str, float]]] = defaultdict(list)
        self._rejections: dict[str, int] = defaultdict(int)
        self._steps: dict[str, int] = defaultdict(int)
        self._interventions: dict[str, int] = defaultdict(int)

    def record_completion(self, emp_id: str, task_id: str = "", cost: float = 0.0, elapsed: float = 0.0) -> None:
        self._completions[emp_id].append({"cost": cost, "elapsed": elapsed})

    def record_rejection(self, emp_id: str) -> None:
        self._rejections[emp_id] += 1

    def record_step(self, emp_id: str) -> None:
        self._steps[emp_id] += 1

    def record_human_intervention(self, emp_id: str, kind: str) -> None:
        self._interventions[emp_id] += 1

    def stats(self, emp_id: str) -> dict[str, Any]:
        comps = self._completions.get(emp_id, [])
        total_cost = sum(c["cost"] for c in comps)
        avg_elapsed = (sum(c["elapsed"] for c in comps) / len(comps)) if comps else 0.0
        rejections = self._rejections.get(emp_id, 0)
        # 驳回率 = 驳回次数 /（完成次数 + 驳回次数）
        total_reviews = len(comps) + rejections
        rejection_rate = (rejections / total_reviews) if total_reviews else 0.0
        steps = self._steps.get(emp_id, 0)
        interventions = self._interventions.get(emp_id, 0)
        intervention_rate = (interventions / steps) if steps else 0.0
        return {
            "completion_count": len(comps),
            "total_cost": total_cost,
            "avg_elapsed": avg_elapsed,
            "rejection_rate": rejection_rate,
            "human_intervention_rate": intervention_rate,
        }


class FileLedger(Ledger):
    """落盘账本：每笔记账原子写 <root>/ledger.json，重启不丢。

    用于真实任务流（看板验收 / 审批决策 / 状态推进时记账）；
    父类 Ledger 保持纯内存（演示与测试用）。
    """

    def __init__(self, store: JsonStore):
        super().__init__()
        self.store = store
        self.path = store.root / "ledger.json"
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self._completions = defaultdict(list, {k: v for k, v in d.get("completions", {}).items()})
        self._rejections = defaultdict(int, d.get("rejections", {}))
        self._steps = defaultdict(int, d.get("steps", {}))
        self._interventions = defaultdict(int, d.get("interventions", {}))

    def _save(self) -> None:
        d = {
            "completions": dict(self._completions),
            "rejections": dict(self._rejections),
            "steps": dict(self._steps),
            "interventions": dict(self._interventions),
        }
        fd, tmp = tempfile.mkstemp(dir=self.store.root, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def record_completion(self, emp_id: str, task_id: str = "", cost: float = 0.0, elapsed: float = 0.0) -> None:
        super().record_completion(emp_id, task_id, cost, elapsed)
        self._save()

    def record_rejection(self, emp_id: str) -> None:
        super().record_rejection(emp_id)
        self._save()

    def record_step(self, emp_id: str) -> None:
        super().record_step(emp_id)
        self._save()

    def record_human_intervention(self, emp_id: str, kind: str) -> None:
        super().record_human_intervention(emp_id, kind)
        self._save()

    def stats_all(self) -> dict[str, dict[str, Any]]:
        """全部有记录员工的统计（看板绩效面板用）。"""
        ids = (set(self._completions) | set(self._rejections)
               | set(self._steps) | set(self._interventions))
        return {emp_id: self.stats(emp_id) for emp_id in ids}
