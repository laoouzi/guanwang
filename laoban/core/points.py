"""奖励积分机制：人类与 AI 统一记分，横向对比「谁干活好干活多」。

记分规则（集中在此，加规则只改一处）：
- 验收通过：+POINTS_PER_TASK × (score/5)（满分验收 +10，3 分 +6）；
- 验收驳回（低分被打回）：-PENALTY_REJECTION；
- 时效奖惩：有截止的任务，按时 +BONUS_ON_TIME、超时 -PENALTY_LATE
  （无限期任务不奖不罚，避免虚设截止刷分）；
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
BONUS_ON_TIME = 2.0        # 按时完成奖励（有截止的任务）
PENALTY_LATE = 2.0         # 超时完成扣分（有截止的任务）


def configure(config: dict | None) -> dict:
    """用 org.json 的 points 段覆盖默认积分规则（进程级生效，幂等可重复调）。

    支持键：points_per_task / penalty_rejection / low_score /
    bonus_on_time / penalty_late（缺省沿用当前值）。返回生效后的规则快照。
    模块内计分函数读全局常量，configure 后立即生效。
    """
    global POINTS_PER_TASK, PENALTY_REJECTION, LOW_SCORE
    global BONUS_ON_TIME, PENALTY_LATE
    if isinstance(config, dict) and config:
        POINTS_PER_TASK = float(config.get("points_per_task", POINTS_PER_TASK))
        PENALTY_REJECTION = float(config.get("penalty_rejection", PENALTY_REJECTION))
        LOW_SCORE = int(config.get("low_score", LOW_SCORE))
        BONUS_ON_TIME = float(config.get("bonus_on_time", BONUS_ON_TIME))
        PENALTY_LATE = float(config.get("penalty_late", PENALTY_LATE))
    return {
        "points_per_task": POINTS_PER_TASK,
        "penalty_rejection": PENALTY_REJECTION,
        "low_score": LOW_SCORE,
        "bonus_on_time": BONUS_ON_TIME,
        "penalty_late": PENALTY_LATE,
    }


def points_for_acceptance(score: int) -> float:
    """验收通过积分：满分 10，按评分线性折算（0.5 步进）。"""
    return round(POINTS_PER_TASK * max(0, min(5, score)) / 5 * 2) / 2


def on_time_points(due_at: str, completed_at: str) -> float | None:
    """时效积分：有截止的任务按时 +2、超时 -2；无限期返回 None（不奖不罚）。

    日期字符串不可解析时按无限期处理（宁可漏奖不可误罚）。
    """
    from datetime import datetime, timezone

    def _parse(s: str):
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    due, done = _parse(due_at), _parse(completed_at)
    if due is None or done is None:
        return None
    return BONUS_ON_TIME if done <= due else -PENALTY_LATE


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
            "avg_score": st.get("avg_score", 0.0),
            "on_time_rate": st.get("on_time_rate", None),
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
            "low_score": LOW_SCORE,
            "bonus_on_time": BONUS_ON_TIME,
            "penalty_late": PENALTY_LATE,
            "note": f"验收通过 +{POINTS_PER_TASK:g}×(评分/5)；"
                    f"评分≤{LOW_SCORE} 驳回 -{PENALTY_REJECTION:g}；"
                    f"有截止任务按时 +{BONUS_ON_TIME:g}、超时 -{PENALTY_LATE:g}；"
                    "ROI = 积分/累计成本（AI 按 token 单价、人类按时薪折算）",
        },
    }
