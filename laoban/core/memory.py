from __future__ import annotations

from typing import Any

from .employee import Employee


def record_experience(emp: Employee, task_type: str, outcome: str, learned: str) -> None:
    emp.memory["experiences"].append({
        "task_type": task_type, "outcome": outcome, "learned": learned,
    })


def add_note(emp: Employee, text: str) -> None:
    emp.memory["notes"].append(text)


def recall(emp: Employee) -> dict[str, Any]:
    return emp.memory


def render_experience(emp: Employee, limit: int = 5) -> str:
    """结构化渲染经验（供 Runner system prompt 注入，替代 dict repr）。

    低分（failure）教训排前——必须吸取的；success 经验跟后；
    各取最近 limit 条防 token 膨胀；learned 为空的跳过。
    """
    exps = emp.memory.get("experiences", [])
    if not exps:
        return "暂无"
    failures = [e for e in exps if e.get("outcome") == "failure"]
    others = [e for e in exps if e.get("outcome") != "failure"]
    lines = []
    for e in failures[-limit:] + others[-limit:]:
        learned = (e.get("learned") or "").strip()
        if not learned:
            continue
        tag = "教训" if e.get("outcome") == "failure" else "经验"
        lines.append(f"- [{tag}] {learned}")
    return "\n".join(lines) if lines else "暂无"
