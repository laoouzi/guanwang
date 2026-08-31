"""奖励积分机制：人类与 AI 统一记分，横向对比「谁干活好干活多」。

记分规则（集中在此，加规则只改一处）：
- 验收通过：+POINTS_PER_TASK × (score/5)（满分验收 +10，3 分 +6）；
- 验收驳回（低分被打回）：-PENALTY_REJECTION；
- ROI = 积分 / 累计成本（元）：统一公平对比（AI 成本低是真实优势）。

榜单设计（避免不公平对比）：
- 人类榜 / AI 榜分开（AI 吞任务速度天然碾压，绝对分榜失真）；
- ROI 统一榜：跨族群公平（每元成本换多少业绩）。
"""
from __future__ import annotations

from .employee import Employee
from .store import JsonStore

POINTS_PER_TASK = 10.0     # 满分验收的基础分
PENALTY_REJECTION = 5.0    # 驳回扣分
LOW_SCORE = 2              # score <= 2 视为驳回（与复盘阈值一致）


def points_for_acceptance(score: int) -> float:
    """验收通过积分：满分 10，按评分线性折算（0.5 步进）。"""
    return round(POINTS_PER_TASK * max(0, min(5, score)) / 5 * 2) / 2


def accept_cost(emp: Employee, elapsed_sec: float = 0.0,
                usage_tokens: int = 0) -> float:
    """一次交付的成本（元）：
    - AI：token 用量 × 单价（compensation.cost_per_1k_tokens）；
    - 人类：任务耗时 × 时薪（月薪 / 22 天 / 8 小时）。
    未配置薪资/单价时为 0（不虚造数据）。
    """
    if emp.kind == "ai":
        rate = float(emp.compensation.get("cost_per_1k_tokens", 0.0) or 0.0)
        return usage_tokens / 1000.0 * rate
    salary = float(emp.compensation.get("salary_monthly", 0.0) or 0.0)
    if salary <= 0 or elapsed_sec <= 0:
        return 0.0
    hourly = salary / 22.0 / 8.0
    return hourly * (elapsed_sec / 3600.0)


def leaderboard(store: JsonStore, ledger) -> dict:
    """三榜输出：ai / human 分榜（按积分降序）+ roi 统一榜。

    ROI = 积分 / 累计成本；成本为 0（未配置）者不进 ROI 榜。
    """
    stats = ledger.stats_all()
    ai_rows: list[dict] = []
    human_rows: list[dict] = []
    roi_rows: list[dict] = []
    for e in store.list_employees():
        if e.status == "terminated":
            continue
        st = stats.get(e.id, {})
        pts = st.get("points", 0.0)
        cost = st.get("total_cost", 0.0)
        row = {
            "id": e.id, "name": e.name, "kind": e.kind,
            "department": e.department,
            "points": pts,
            "completion_count": st.get("completion_count", 0),
            "rejection_count": st.get("rejection_count", 0),
            "total_cost": round(cost, 4),
        }
        (ai_rows if e.kind == "ai" else human_rows).append(row)
        if cost > 0:
            row_roi = dict(row)
            row_roi["roi"] = round(pts / cost, 2)   # 每元成本产出的积分
            roi_rows.append(row_roi)
    key = lambda r: (-r["points"], -r["completion_count"])
    return {
        "ai": sorted(ai_rows, key=key),
        "human": sorted(human_rows, key=key),
        "roi": sorted(roi_rows, key=lambda r: -r["roi"]),
        "rules": {
            "points_per_task": POINTS_PER_TASK,
            "penalty_rejection": PENALTY_REJECTION,
            "note": "验收通过 +10×(评分/5)；驳回 -5；"
                    "ROI = 积分/累计成本（AI 按 token 单价、人类按时薪折算）",
        },
    }
