from __future__ import annotations

from collections import defaultdict
from typing import Any


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
