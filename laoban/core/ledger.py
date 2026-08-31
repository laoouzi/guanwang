from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .store import JsonStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(s: str) -> datetime | None:
    """ISO 时间解析（无时区按 UTC）；不可解析返回 None。"""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _quality(comps: list[dict]) -> tuple[float, int, int, float | None]:
    """完成条目的质量/时效指标：平均评分、按时数、限期数、按时率。

    分母只含设了截止的任务（无限期不计入）；无评分记录时均分为 0。
    """
    scored = [c["score"] for c in comps if c.get("score")]
    avg_score = (sum(scored) / len(scored)) if scored else 0.0
    with_due = [c for c in comps if c.get("on_time") is not None]
    on_time_count = sum(1 for c in with_due if c["on_time"])
    on_time_rate = (on_time_count / len(with_due)) if with_due else None
    return avg_score, on_time_count, len(with_due), on_time_rate


class Ledger:
    """绩效账本：完成数 / 平均耗时 / 总成本 / 驳回率 / 人类介入率 / 奖励积分
    / 平均验收评分 / 按时完成率。条目带时间戳（at），支持周期过滤（周报用）。"""

    def __init__(self):
        self._completions: dict[str, list[dict[str, float]]] = defaultdict(list)
        self._rejections: dict[str, int] = defaultdict(int)
        self._steps: dict[str, int] = defaultdict(int)
        self._interventions: dict[str, int] = defaultdict(int)
        self._points: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def record_completion(self, emp_id: str, task_id: str = "", cost: float = 0.0,
                          elapsed: float = 0.0, score: float = 0.0,
                          on_time: bool | None = None, at: str = "") -> None:
        """完成记账：score=验收评分（0 表示未记）；on_time=None 表示无限期任务；
        at=记账时刻（ISO，缺省现在；旧数据无 at 不参与周期统计）。"""
        self._completions[emp_id].append({
            "cost": cost, "elapsed": elapsed,
            "score": score, "on_time": on_time,
            "at": at or _now_iso(),
        })
        self._persist()

    def record_rejection(self, emp_id: str) -> None:
        self._rejections[emp_id] += 1
        self._persist()

    def record_points(self, emp_id: str, delta: float, reason: str = "",
                      kind: str = "", at: str = "") -> None:
        """记积分（正=奖励，负=扣分），每笔含原因可审计。

        kind：rejection=驳回扣分 / acceptance=验收（含时效）；供周期统计分类。
        """
        self._points[emp_id].append({
            "delta": delta, "reason": reason, "kind": kind,
            "at": at or _now_iso(),
        })
        self._persist()

    def points(self, emp_id: str) -> float:
        return sum(p["delta"] for p in self._points.get(emp_id, []))

    def points_log(self, emp_id: str) -> list[dict[str, Any]]:
        return list(self._points.get(emp_id, []))

    def record_step(self, emp_id: str) -> None:
        self._steps[emp_id] += 1
        self._persist()

    def record_human_intervention(self, emp_id: str, kind: str) -> None:
        self._interventions[emp_id] += 1
        self._persist()

    def _persist(self) -> None:
        """记账后钩子：内存版空操作；FileLedger 覆写为落盘。"""
        pass

    def stats_between(self, emp_id: str, start: str, end: str) -> dict[str, Any]:
        """周期统计（财务周报用）：只统计 at ∈ [start, end] 的条目（含边界）。

        旧数据（无 at 字段）无法归属周期，不计入——历史累计看 stats()。
        """
        s, e = _parse_ts(start), _parse_ts(end)
        if s is None or e is None:
            raise ValueError("stats_between 需要 ISO 时间边界")

        def _in(entry) -> bool:
            at = _parse_ts(entry.get("at", ""))
            return at is not None and s <= at <= e

        comps = [c for c in self._completions.get(emp_id, []) if _in(c)]
        pts_entries = [p for p in self._points.get(emp_id, []) if _in(p)]
        total_cost = sum(c["cost"] for c in comps)
        avg_score, on_time_count, due_count, on_time_rate = _quality(comps)
        points = sum(p["delta"] for p in pts_entries)
        rejections = sum(1 for p in pts_entries if p.get("kind") == "rejection")
        return {
            "completion_count": len(comps),
            "total_cost": total_cost,
            "avg_score": round(avg_score, 2),
            "on_time_count": on_time_count,
            "due_count": due_count,
            "on_time_rate": (round(on_time_rate, 4)
                             if on_time_rate is not None else None),
            "points": round(points, 2),
            "rejection_count": rejections,
        }

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
        avg_score, on_time_count, due_count, on_time_rate = _quality(comps)
        return {
            "completion_count": len(comps),
            "total_cost": total_cost,
            "avg_elapsed": avg_elapsed,
            "rejection_rate": rejection_rate,
            "rejection_count": rejections,
            "human_intervention_rate": intervention_rate,
            "points": self.points(emp_id),
            "avg_score": round(avg_score, 2),
            "on_time_count": on_time_count,
            "due_count": due_count,
            "on_time_rate": (round(on_time_rate, 4)
                             if on_time_rate is not None else None),
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
        self._points = defaultdict(list, {k: v for k, v in d.get("points", {}).items()})

    def _persist(self) -> None:
        """每笔记账后原子写盘（由基类钩子调用）。"""
        d = {
            "completions": dict(self._completions),
            "rejections": dict(self._rejections),
            "steps": dict(self._steps),
            "interventions": dict(self._interventions),
            "points": dict(self._points),
        }
        fd, tmp = tempfile.mkstemp(dir=self.store.root, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def stats_all(self) -> dict[str, dict[str, Any]]:
        """全部有记录员工的统计（看板绩效面板用）。"""
        ids = (set(self._completions) | set(self._rejections)
               | set(self._steps) | set(self._interventions)
               | set(self._points))
        return {emp_id: self.stats(emp_id) for emp_id in ids}
