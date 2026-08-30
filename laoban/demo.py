from __future__ import annotations

import tempfile
from datetime import date

from .core.store import JsonStore
from .core.task import Task, PENDING, TRIAGE, PLANNING, REVIEW, ASSIGNED, DOING, WAITING_HUMAN, REPORTING, DONE
from .core.state_machine import advance
from .core.employee import Employee
from .core.human_inbox import HumanInbox
from .core.ledger import Ledger
from .core.feedback import write_back_experience
from .bootstrap import bootstrap_org
from .llm.gateway import LLMGateway
from .llm.mock import MockLLM


def run_demo() -> int:
    print("═══ laoban 演示模式（MockLLM，无需 API Key）═══\n")

    root = tempfile.mkdtemp(prefix="laoban-demo-")
    store = JsonStore(root)
    gw = LLMGateway()
    for pid in ("hr", "legal", "it", "receptionist", "pm", "reviewer", "dev"):
        gw.register_mock(pid, MockLLM(responses=[f"[{pid}] 已完成本职工作"]))

    # ── 1. 启动模式：三元老入职，产出组织设计建议 ──
    print("【1】启动模式：三元老（HR / 法务 / IT）入职")
    plan = bootstrap_org(store, gw, business="做跨境电商工具")
    print(f"    组织设计方案：{plan['组织设计方案']}")
    for pid in ("hr", "legal", "it"):
        print(f"    {pid} 建议：{plan[pid]}")

    # ── 2. 双轨招聘：业务团队（AI 员工 + 人类员工同部门）──
    print("\n【2】组建业务团队：AI 与人类员工同部门协作")
    store.save_employee(Employee(id="receptionist", name="小助", title="前台分拣", department="ops_dept"))
    store.save_employee(Employee(id="pm", name="老谋", title="项目经理", department="ops_dept"))
    store.save_employee(Employee(id="reviewer", name="严审", title="评审员", department="legal_dept"))
    store.save_employee(Employee(id="dev", name="阿码", title="开发工程师", department="dev_dept"))
    store.save_employee(Employee(id="emp-chen", name="陈工", kind="human",
                                 title="数据核查员", department="dev_dept"))
    for e in store.list_employees():
        kind = "人类" if e.kind == "human" else "AI"
        print(f"    [{kind}] {e.name}（{e.id}）· {e.title} · {e.department}")

    # ── 3. 任务流水线：状态机全程 + 人机协作 ──
    print("\n【3】任务流水线：提交 → 分拣 → 拆解 → 评审 → 派发 → 执行")
    task = Task(id="DEMO-1", title="演示任务：写一个数据清洗函数")
    store.save_task(task)
    ledger = Ledger()
    inbox = HumanInbox(store)

    for state, actor in [(TRIAGE, "receptionist"), (PLANNING, "pm"), (REVIEW, "reviewer"), (ASSIGNED, "pm")]:
        advance(task, state, actor=actor)
        store.save_task(task)
        ledger.record_step("dev")
        print(f"    {task.state:<14} ← {actor}")

    # DOING：AI 执行中发现超出能力 → 转人类待办
    advance(task, DOING, actor="dev")
    store.save_task(task)
    ledger.record_step("dev")
    print(f"    {task.state:<14} ← dev 执行中")
    ht = inbox.create(task_id=task.id, title="配合 AI 核查三份样本数据的异常值",
                      assignee="emp-chen", due_date=date.today().isoformat())
    advance(task, WAITING_HUMAN, actor="dev", remark=f"超出 AI 能力，转人类待办 {ht.id}")
    store.save_task(task)
    ledger.record_human_intervention("dev", "human_task")
    print(f"    {task.state:<14} ← dev 派发人类待办给 陈工")

    # 人类员工当日任务清单
    print(f"\n【4】陈工的今日任务清单（{date.today()}）：")
    for h in inbox.daily_list(assignee="emp-chen", date=date.today().isoformat()):
        print(f"    [AI 派发] {h.title}（截止 {h.due_date}）")
    inbox.complete(ht.id, result="发现 12 条异常记录，已标注")
    print("    ✔ 陈工完成待办，流程恢复")

    # 人→人闭环：陈工把复核工作派给同部门的小李，小李完成后结果回传陈工
    store.save_employee(Employee(id="emp-xiaoli", name="小李", kind="human",
                                 title="初级核查员", department="dev_dept"))
    ht2 = inbox.create(task_id=task.id, title="复核 12 条异常记录", assignee="emp-xiaoli",
                       source="self", created_by="emp-chen")
    print(f"\n【4.5】人→人派活：陈工 → 小李（{ht2.id}）")
    inbox.complete(ht2.id, result="12 条异常全部人工复核确认，其中 2 条误报")
    for r in inbox.results_for("emp-chen"):
        print(f"    📩 陈工收到回传：{r.title} ← 小李 —— {r.result}")

    # 流程收尾
    for state, actor in [(DOING, "emp-chen"), (REPORTING, "dev"), (DONE, "boss")]:
        advance(task, state, actor=actor)
        store.save_task(task)
        ledger.record_step("dev")
        print(f"    {task.state:<14} ← {actor}")

    # ── 5. 绩效账本 + 经验回写 ──
    ledger.record_completion("dev", task_id=task.id, cost=0.42, elapsed=180)
    dev = store.load_employee("dev")
    write_back_experience(dev, task_type="feature", score=5, comment="人类核查数据后交付更快")
    store.save_employee(dev)

    print("\n【5】绩效与经验")
    s = ledger.stats("dev")
    print(f"    阿码：完成 {s['completion_count']} 单 · 成本 ${s['total_cost']:.2f}"
          f" · 驳回率 {s['rejection_rate']:.0%} · 人类介入率 {s['human_intervention_rate']:.0%}")
    print(f"    经验回写：{dev.memory['experiences'][-1]['outcome']} —— {dev.memory['experiences'][-1]['learned']}")

    print("\n═══ ✅ 演示完成：任务已走完全流程（含人机协作）═══")
    print(f"    数据目录：{root}")
    print("    下一步：laoban init && laoban hire ... 启动你自己的公司")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_demo())
