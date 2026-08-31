from __future__ import annotations

import tempfile
from datetime import date

from .core.store import JsonStore
from .core.task import Task, PENDING, TRIAGE, PLANNING, REVIEW, ASSIGNED, DOING, WAITING_HUMAN, REPORTING, DONE
from .core.state_machine import advance
from .core.human_inbox import HumanInbox
from .core.ledger import Ledger
from .core.feedback import write_back_experience
from .core.lifecycle import suspend_employee, activate_employee
from .core.messenger import send as msg_send, inbox as msg_inbox
from .core.workstation import enqueue, dequeue, queue_of
from .bootstrap import bootstrap_org
from .org import load_org, instantiate, iter_roles
from .llm.gateway import LLMGateway
from .llm.mock import MockLLM
from .runner.runner import Runner


def run_demo() -> int:
    print("═══ laoban 演示模式（MockLLM，无需 API Key）═══\n")

    root = tempfile.mkdtemp(prefix="laoban-demo-")
    store = JsonStore(root)
    org = load_org()          # v0.2：组织结构来自 org.json 配置
    gw = LLMGateway()
    for _dept, role in iter_roles(org):
        if role.get("kind", "ai") == "ai":
            pid = role.get("model", {}).get("provider", role["id"])
            gw.register_mock(pid, MockLLM(responses=[f"[{pid}] 已完成本职工作"]))

    # ── 1. 启动模式：创始人入职（org.json 中 founder: true 的角色）──
    print("【1】启动模式：四元老（HR / 法务 / IT / 财务）入职")
    plan = bootstrap_org(store, gw, business="做跨境电商工具")
    print(f"    组织设计方案：{plan['组织设计方案']}")
    for pid in ("hr", "legal", "it", "cfo"):
        print(f"    {pid} 建议：{plan[pid]}")

    # ── 2. 双轨招聘：业务团队按 org.json 配置入职（AI 与人类同部门）──
    print("\n【2】组建业务团队（org.json 配置驱动）：AI 与人类员工同部门协作")
    team = instantiate(store, org, which="team")
    print(f"    已按配置入职 {len(team)} 名员工")
    for e in store.list_employees():
        kind = "人类" if e.kind == "human" else "AI"
        print(f"    [{kind}] {e.name}（{e.id}）· {e.title} · {e.department}")

    # ── 2.5 员工生命周期 + 点对点消息 ──
    print("\n【2.5】员工生命周期与点对点消息")
    suspend_employee(store, "emp-xiaoli")
    print("    小李停职（suspended）→ 派单守卫将拒绝向其派发任务")
    activate_employee(store, "emp-xiaoli")
    print("    小李上岗（active）→ 恢复接单资格")
    m = msg_send(store, "pm", "dev", "请优先处理接下来的演示任务", task_id="DEMO-1")
    print(f"    📨 pm → dev 消息已发送（{m['id']}），dev 收件箱 {len(msg_inbox(store, 'dev'))} 条")

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
        if state == ASSIGNED:
            enqueue(store, "dev", task.id)
            print(f"    → 任务入队 dev 工位（当前队列 {queue_of(store, 'dev')}）")

    # DOING：AI 执行中通过工具循环自主决定找人类同事配合
    advance(task, DOING, actor="dev")
    store.save_task(task)
    ledger.record_step("dev")
    print(f"    {task.state:<14} ← dev 执行中")
    print("    ─ dev 的视野：组织通讯录（含人类同事）+ 协作工具 [TOOL] 协议")
    gw.register_mock("dev", MockLLM(responses=[
        "这批数据需要人工抽查异常值，通讯录里陈工（emp-chen）是数据核查员，派给他：\n\n"
        "[TOOL] delegate_task\n"
        '{"assignee": "emp-chen", "title": "配合 AI 核查三份样本数据的异常值", '
        '"instruction": "核对后回传结论", "due": "' + date.today().isoformat() + '"}\n'
        "[/TOOL]\n",
        "clean_data() 初版完成，等陈工的人工抽查结论回来后定稿。",
    ]))
    runner = Runner(gw, store=store)
    output = runner.run(store.load_employee("dev"), task)
    for line in output.splitlines():
        if line.startswith("- "):
            print(f"    ─ dev 的协作动作：{line}")
    ht = [h for h in inbox.list_pending() if h.assignee == "emp-chen"][-1]
    advance(task, WAITING_HUMAN, actor="dev", remark=f"AI 自主转人类待办 {ht.id}")
    store.save_task(task)
    ledger.record_human_intervention("dev", "human_task")
    print(f"    {task.state:<14} ← dev 自主派发人类待办 {ht.id} 给 陈工")

    # 人类员工当日任务清单
    print(f"\n【4】陈工的今日任务清单（{date.today()}）：")
    for h in inbox.daily_list(assignee="emp-chen", date=date.today().isoformat()):
        print(f"    [AI 派发] {h.title}（截止 {h.due_date}）")
    inbox.complete(ht.id, result="发现 12 条异常记录，已标注")
    print("    ✔ 陈工完成待办，流程恢复")

    # 人→人闭环：陈工把复核工作派给同部门的小李，小李完成后结果回传陈工
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
        if state == DONE:
            dequeue(store, "dev", task.id)
            print(f"    → 任务出队 dev 工位（剩余队列 {queue_of(store, 'dev')}）")

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
