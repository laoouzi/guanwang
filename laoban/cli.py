from __future__ import annotations

import argparse
import datetime
import json
import uuid
from pathlib import Path

from .core.store import JsonStore
from .core.employee import Employee
from .core.task import Task
from .core.human_inbox import HumanInbox


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="laoban", description="像经营公司一样管理 AI 员工")
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="初始化公司目录")
    init.add_argument("--root", default=".laoban", help="数据目录（默认 .laoban）")

    hire = sub.add_parser("hire", help="招聘员工（AI 或人类）")
    hire.add_argument("--root", default=".laoban")
    hire.add_argument("--name", required=True)
    hire.add_argument("--title", default="")
    hire.add_argument("--kind", default="ai", choices=["ai", "human"],
                      help="ai=AI 员工 / human=人类员工（入部门树，与 AI 同组织协作）")
    hire.add_argument("--department", default="")
    hire.add_argument("--reports-to", default="")
    hire.add_argument("--id", default="")

    emp_list = sub.add_parser("employees", help="列出员工")
    emp_list.add_argument("--root", default=".laoban")

    task = sub.add_parser("task", help="任务操作")
    task_sub = task.add_subparsers(dest="task_command", required=True)
    ts = task_sub.add_parser("submit", help="提交任务")
    ts.add_argument("--root", default=".laoban")
    ts.add_argument("--title", required=True)
    tst = task_sub.add_parser("status", help="查看任务状态")
    tst.add_argument("--root", default=".laoban")
    tst.add_argument("--id", default="")

    todo = sub.add_parser("todo", help="人类待办操作")
    todo_sub = todo.add_subparsers(dest="todo_command", required=True)
    ta = todo_sub.add_parser("add", help="新增人类待办（AI 派发或手动）")
    ta.add_argument("--root", default=".laoban")
    ta.add_argument("--assignee", required=True, help="承接人员工 id")
    ta.add_argument("--title", required=True)
    ta.add_argument("--task-id", default="", help="关联的任务 id")
    ta.add_argument("--due", default="", help="截止日期 YYYY-MM-DD（空=不限期）")
    ta.add_argument("--source", default="ai_delegated",
                    choices=["ai_delegated", "self", "boss"])
    ta.add_argument("--from", dest="from_id", default="boss",
                    help="发起人 id（完成结果回传给该员工，人→人闭环）")
    tc = todo_sub.add_parser("done", help="完成人类待办（结果自动回传发起人）")
    tc.add_argument("--root", default=".laoban")
    tc.add_argument("--id", required=True)
    tc.add_argument("--result", default="")
    tr = todo_sub.add_parser("results", help="查看我发起的任务已回传的结果")
    tr.add_argument("--root", default=".laoban")
    tr.add_argument("--who", required=True, help="发起人员工 id")

    today = sub.add_parser("today", help="人类员工当日任务清单")
    today.add_argument("--root", default=".laoban")
    today.add_argument("--who", required=True, help="员工 id")
    today.add_argument("--date", default="", help="日期 YYYY-MM-DD（默认今天）")

    dash = sub.add_parser("dashboard", help="启动 Web 看板（默认 127.0.0.1:7891）")
    dash.add_argument("--root", default=".laoban")
    dash.add_argument("--port", type=int, default=7891)

    demo = sub.add_parser("demo", help="演示模式（MockLLM，无需 API Key）")

    acc = sub.add_parser("acceptance", help="D2 标准验收套件（3 类任务自动判定）")
    acc_sub = acc.add_subparsers(dest="acc_command", required=True)
    acc_run = acc_sub.add_parser("run", help="运行验收套件")
    acc_run.add_argument("--root", default=".laoban")
    acc_run.add_argument("--suite", default="standard", help="standard | 任务 ID 逗号分隔")
    acc_run.add_argument("--provider", default="",
                         help="真实 provider 名（deepseek/qwen/openai/ollama）；默认自动发现环境变量，无则回退演示模式")

    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as e:
        # argparse 对未知命令/--help 直接 SystemExit，统一转为返回码
        return e.code if isinstance(e.code, int) else 1
    cmd = args.command

    if cmd == "init":
        JsonStore(args.root)
        print(f"已初始化公司目录：{args.root}")
        return 0

    if cmd == "hire":
        st = JsonStore(args.root)
        emp_id = args.id or f"emp-{args.name}"
        emp = Employee(id=emp_id, name=args.name, kind=args.kind, title=args.title,
                       department=args.department, reports_to=args.reports_to,
                       workspace={"dir": f"workspaces/{emp_id}/"})
        st.save_employee(emp)
        kind_label = "人类员工" if emp.kind == "human" else "AI 员工"
        print(f"已入职（{kind_label}）：{emp.name}（{emp.id}）"
              + (f"，部门：{emp.department}" if emp.department else ""))
        return 0

    if cmd == "employees":
        st = JsonStore(args.root)
        for e in st.list_employees():
            kind_label = "人类" if e.kind == "human" else "AI"
            print(f"{e.id}\t{kind_label}\t{e.name}\t{e.title}\t{e.department}")
        return 0

    if cmd == "task":
        st = JsonStore(args.root)
        if args.task_command == "submit":
            tid = f"T-{uuid.uuid4().hex[:6]}"
            st.save_task(Task(id=tid, title=args.title))
            print(f"任务已提交：{tid} {args.title}")
            return 0
        if args.task_command == "status":
            for t in st.list_tasks():
                if args.id and t.id != args.id:
                    continue
                print(f"{t.id}\t{t.state}\t{t.title}")
            return 0

    if cmd == "todo":
        st = JsonStore(args.root)
        inbox = HumanInbox(st)
        if args.todo_command == "add":
            ht = inbox.create(task_id=args.task_id, title=args.title,
                              assignee=args.assignee, due_date=args.due, source=args.source,
                              created_by=args.from_id)
            print(f"人类待办已创建：{ht.id} {ht.title} → {ht.assignee}"
                  f"（发起人 {ht.created_by}，完成后结果回传）")
            return 0
        if args.todo_command == "done":
            inbox.complete(args.id, result=args.result)
            print(f"人类待办已完成：{args.id}")
            return 0
        if args.todo_command == "results":
            rs = inbox.results_for(args.who)
            if not rs:
                print(f"{args.who} 暂无回传结果")
                return 0
            print(f"📩 {args.who} 收到的回传结果（{len(rs)} 条）：")
            for ht in rs:
                print(f"  {ht.id} {ht.title} ← {ht.assignee}")
                if ht.result:
                    print(f"    结果：{ht.result}")
            return 0

    if cmd == "today":
        st = JsonStore(args.root)
        inbox = HumanInbox(st)
        date = args.date or datetime.date.today().isoformat()
        mine = inbox.daily_list(assignee=args.who, date=date)
        results = inbox.results_for(args.who)
        if results:
            print(f"📩 {args.who} 有 {len(results)} 条回传结果待查看"
                  f"（laoban todo results --who {args.who}）")
        if not mine:
            print(f"{args.who} 在 {date} 没有待办")
            return 0
        print(f"📋 {args.who} 的 {date} 任务清单：")
        for ht in mine:
            src = {"ai_delegated": "AI 派发", "self": "自建", "boss": "老板指派"}.get(ht.source, ht.source)
            due = f"，截止 {ht.due_date}" if ht.due_date else ""
            print(f"  [{src}] {ht.title}（{ht.id}{due}）")
        return 0

    if cmd == "dashboard":
        from .dashboard.server import DashboardServer
        st = JsonStore(args.root)
        server = DashboardServer(st, port=args.port)
        print(f"看板已启动：http://127.0.0.1:{server.port}/ （Ctrl+C 退出）")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n看板已停止")
        return 0

    if cmd == "demo":
        from .demo import run_demo
        return run_demo()

    if cmd == "acceptance":
        from .acceptance import run_acceptance, STANDARD_SUITE
        from .llm.gateway import LLMGateway
        from .llm.mock import MockLLM
        from .llm.openai_compatible import register_from_env
        gw = LLMGateway()
        # 真实模式：环境变量自动发现；无则回退演示模式（MockLLM）
        real = register_from_env(gw)
        if args.provider:
            provider = args.provider
            if provider not in real:
                print(f"⚠️ 未检测到 provider [{provider}] 的环境变量配置，仍将尝试调用")
        elif real:
            provider = real[0]
        else:
            provider = None
        if provider is None:
            for pid in ("receptionist", "pm", "reviewer", "worker"):
                gw.register_mock(pid, MockLLM(responses=[f"[{pid}] 验收产出", "[准奏] OK"]))
            print("演示模式（MockLLM）——设置 LAOBAN_DEEPSEEK_API_KEY 等环境变量可切换真实 LLM\n")
        else:
            print(f"真实模式：provider = {provider}\n")
        suite = STANDARD_SUITE
        if args.suite != "standard":
            wanted = {x.strip() for x in args.suite.split(",") if x.strip()}
            suite = tuple(t for t in STANDARD_SUITE if t["id"] in wanted)
        results = run_acceptance(gw, suite=suite, root_dir=args.root, provider=provider)
        passed = sum(1 for r in results if r["passed"])
        for r in results:
            mark = "✅" if r["passed"] else "❌"
            print(f"{mark} [{r['category']}] {r['task_id']} — {r['reason']}"
                  f"（评审：{'通过' if r['review_passed'] else '封驳'}）")
        print(f"\n验收结果：{passed}/{len(results)} 通过"
              + (" ✅ 达到 D2 目标（≥ 2/3）" if passed >= 2 and len(results) >= 2 else ""))
        return 0 if passed >= 2 or len(results) < 2 else 2

    return 1
