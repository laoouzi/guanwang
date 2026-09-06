"""CFO 财务周报：按周聚合成本 / 积分 / ROI，归档落盘 + 环比 + 预算建议。

数据口径：
- 周期为 ISO 周（周一 00:00 UTC ~ 周日 23:59:59 UTC）；
- 统计基于 ledger 周期过滤（stats_between）：本周验收完成的任务
  （成本/评分/按时率）与本周积分流水（含驳回）；
- 环比 = 对比归档中上一周的报告（points / cost / roi 三个指标）；
- 预算建议：有 LLM 网关且有 CFO 员工时由 CFO 模型生成（≤120 字），
  否则模板降级（周报永不缺席，不因外部依赖失效）。

归档：<root>/finance_reports.json，按周期升序追加；同周重复生成会覆盖
（幂等由 maybe_generate_weekly_report 控制：本周已有报告则跳过）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .employee import Employee
from .ledger import Ledger
from .store import JsonStore

ADVICE_MAX_LEN = 200          # LLM 建议截断长度
ARCHIVE_NAME = "finance_reports.json"


# ---- 周期工具 ----

def week_key(dt: datetime) -> str:
    """ISO 周 key：如 2026-W35（按 ISO 周历，周一为一周之始）。"""
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def week_bounds(dt: datetime) -> tuple[datetime, datetime]:
    """ISO 周边界：周一 00:00:00 ~ 周日 23:59:59（UTC）。"""
    dt = dt.astimezone(timezone.utc)
    monday = dt - timedelta(days=dt.weekday())
    start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return start, end


def prev_week_key(key: str) -> str:
    """上一周 key：归到该周周一，减 1 天（上周日）再算 ISO 周。

    不能直接用周内任意锚点减 1 天——锚点若落在周日，减 1 天仍在同一周。
    """
    y, w = key.split("-W")
    dt = datetime(int(y), 1, 4, tzinfo=timezone.utc)   # 1月4日必在第1周
    dt += timedelta(weeks=int(w) - 1)
    monday = dt - timedelta(days=dt.weekday())
    return week_key(monday - timedelta(days=1))


# ---- 归档 ----

def _archive_path(store: JsonStore) -> Path:
    return store.root / ARCHIVE_NAME


def load_reports(store: JsonStore) -> list[dict[str, Any]]:
    p = _archive_path(store)
    if not p.exists():
        return []
    try:
        reports = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return reports if isinstance(reports, list) else []


def _save_reports(store: JsonStore, reports: list[dict[str, Any]]) -> None:
    reports.sort(key=lambda r: r.get("period", {}).get("key", ""))
    store._atomic_write(_archive_path(store), reports)


# ---- 报告生成 ----

def generate_cost_report(store: JsonStore, ledger: Ledger,
                         now: datetime | None = None,
                         gateway=None) -> dict[str, Any]:
    """生成当前周的成本报告并归档（同周覆盖，幂等由上层控制）。"""
    now = now or datetime.now(timezone.utc)
    start, end = week_bounds(now)
    s_iso, e_iso = start.isoformat(), end.isoformat()
    key = week_key(now)

    by_dept: dict[str, list[dict[str, Any]]] = {}
    company = {"points": 0.0, "cost": 0.0, "completion_count": 0,
               "rejection_count": 0, "on_time": 0, "due": 0}
    for e in store.list_employees():
        if e.status == "terminated":
            continue
        st = ledger.stats_between(e.id, s_iso, e_iso)
        if not (st["completion_count"] or st["points"]):
            continue   # 本周无产出者不上榜
        row = {
            "id": e.id, "name": e.name, "kind": e.kind,
            "points": st["points"], "cost": round(st["total_cost"], 4),
            "completion_count": st["completion_count"],
            "rejection_count": st["rejection_count"],
            "avg_score": st["avg_score"],
            "on_time_count": st["on_time_count"],
            "due_count": st["due_count"],
            "on_time_rate": st["on_time_rate"],
        }
        if row["cost"] > 0:
            row["roi"] = round(row["points"] / row["cost"], 2)
        by_dept.setdefault(e.department or "—", []).append(row)
        company["points"] += row["points"]
        company["cost"] += row["cost"]
        company["completion_count"] += row["completion_count"]
        company["rejection_count"] += row["rejection_count"]
        company["on_time"] += row["on_time_count"]
        company["due"] += row["due_count"]
    company["points"] = round(company["points"], 2)
    company["cost"] = round(company["cost"], 4)
    company["roi"] = (round(company["points"] / company["cost"], 2)
                      if company["cost"] > 0 else None)

    departments = []
    for dept, members in sorted(by_dept.items()):
        pts = round(sum(m["points"] for m in members), 2)
        cost = round(sum(m["cost"] for m in members), 4)
        departments.append({
            "department": dept,
            "points": pts, "cost": cost,
            "roi": round(pts / cost, 2) if cost > 0 else None,
            "completion_count": sum(m["completion_count"] for m in members),
            "rejection_count": sum(m["rejection_count"] for m in members),
            "members": sorted(members, key=lambda m: -m["points"]),
        })

    cfo = _find_cfo(store)
    report = {
        "period": {"key": key, "start": s_iso, "end": e_iso},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": cfo.id if cfo else "auto",
        "company": company,
        "departments": departments,
        "budget_advice": _budget_advice(company, departments, cfo, gateway),
    }

    # 归档（同周覆盖）+ 环比
    reports = [r for r in load_reports(store)
               if r.get("period", {}).get("key") != key]
    reports.append(report)
    _save_reports(store, reports)
    report["compare"] = _compare_with_prev(store, report)
    return report


def _compare_with_prev(store: JsonStore, report: dict) -> dict | None:
    """环比：对比归档中上一周的公司级指标（缺失返回 None）。"""
    prev = _find_report(store, prev_week_key(report["period"]["key"]))
    if prev is None:
        return None
    cur, old = report["company"], prev.get("company", {})
    out: dict[str, Any] = {"period": prev["period"]["key"]}
    for k in ("points", "cost", "completion_count"):
        base = float(old.get(k, 0) or 0)
        out[k] = {"current": cur[k], "previous": base,
                  "delta": round(float(cur[k]) - base, 2)}
        if base > 0:
            out[k]["pct"] = round((float(cur[k]) - base) / base * 100, 1)
    return out


def _find_report(store: JsonStore, key: str) -> dict | None:
    for r in load_reports(store):
        if r.get("period", {}).get("key") == key:
            return r
    return None


def maybe_generate_weekly_report(store: JsonStore, ledger: Ledger,
                                 now: datetime | None = None,
                                 gateway=None) -> dict | None:
    """周度自动触发（幂等）：本周已有归档报告则跳过。

    生成后通过 messenger 通知老板（老板存在且 CFO 在职时）。
    """
    now = now or datetime.now(timezone.utc)
    key = week_key(now)
    if _find_report(store, key) is not None:
        return None
    report = generate_cost_report(store, ledger, now=now, gateway=gateway)
    _notify_boss(store, report)
    return report


# ---- 建议生成（LLM 优先，模板兜底） ----

_ADVICE_PROMPT = (
    "你是公司财务专家（CFO），基于本周各部门产出数据给出预算建议。\n"
    "本周数据：积分 {points}，成本 {cost} 元，ROI {roi}，"
    "完成 {completions} 项，驳回 {rejections} 次。\n"
    "部门明细：{departments}\n"
    "请输出不超过 120 字的预算建议：哪个部门该加投入/该收缩，"
    "下一周成本预算怎么定。直接给结论，不要客套。"
)


def _budget_advice(company: dict, departments: list[dict],
                   cfo: Employee | None, gateway) -> str:
    # LLM 建议：有 CFO + 有网关（调用失败降级模板，周报不缺席）
    if gateway is not None and cfo is not None:
        try:
            from ..llm.base import Message
            dept_lines = "; ".join(
                f"{d['department']}:积分{d['points']}/成本{d['cost']}元/"
                f"ROI{d.get('roi', '—')}" for d in departments) or "无部门产出"
            msgs = [Message(role="user", content=_ADVICE_PROMPT.format(
                points=company["points"], cost=company["cost"],
                roi=company.get("roi", "—"),
                completions=company["completion_count"],
                rejections=company["rejection_count"],
                departments=dept_lines[:600]))]
            text = gateway.chat_for_employee(cfo.model_config, msgs).content.strip()
            if text:
                return text[:ADVICE_MAX_LEN]
        except Exception:
            pass

    # 模板建议：最高/最低 ROI 部门 + 成本口径
    with_roi = [d for d in departments if d.get("roi") is not None]
    parts = []
    if with_roi:
        top = max(with_roi, key=lambda d: d["roi"])
        low = min(with_roi, key=lambda d: d["roi"])
        parts.append(f"产出效率最高：{top['department']}（每元 {top['roi']} 积分），"
                     f"建议优先加投入")
        if low["department"] != top["department"]:
            parts.append(f"最低：{low['department']}（每元 {low['roi']} 积分），"
                         f"建议核查任务分配或成本单价")
    if company["rejection_count"]:
        parts.append(f"本周驳回 {company['rejection_count']} 次，"
                     "验收质量需关注")
    if not parts:
        return "本周暂无产出数据，无预算调整建议。"
    advice = "；".join(parts) + "。"
    return advice[:ADVICE_MAX_LEN]


# ---- 角色/通知 ----

def _find_cfo(store: JsonStore) -> Employee | None:
    """找财务专家：财务部（fin_dept）在职员工，或 job_description 提成本核算。"""
    for e in store.list_employees():
        if e.status != "active":
            continue
        mission = str(e.job_description.get("mission", ""))
        if e.department == "fin_dept" or "成本" in mission or "预算" in mission:
            return e
    return None


def _find_boss(store: JsonStore) -> Employee | None:
    """找老板：permissions.role=admin 的在职员工。"""
    for e in store.list_employees():
        if e.status == "active" and e.permissions.get("role") == "admin":
            return e
    return None


def _notify_boss(store: JsonStore, report: dict) -> None:
    cfo = _find_cfo(store)
    boss = _find_boss(store)
    if not (cfo and boss):
        return
    from .messenger import send
    c = report["company"]
    roi = c.get("roi", "—")
    text = (f"【CFO 周报 {report['period']['key']}】积分 {c['points']} · "
            f"成本 {c['cost']} 元 · ROI {roi} · 完成 {c['completion_count']} 项"
            f"（驳回 {c['rejection_count']}）。建议：{report['budget_advice']}")
    try:
        send(store, cfo.id, boss.id, text)
    except Exception:
        pass   # 通知失败不影响周报归档
