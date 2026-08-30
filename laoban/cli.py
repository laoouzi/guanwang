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
    tc = todo_sub.add_parser("done", help="完成人类待办")
    tc.add_argument("--root", default=".laoban")
    tc.add_argument("--id", required=True)
    tc.add_argument("--result", default="")

    today = sub.add_parser("today", help="人类员工当日任务清单")
    today.add_argument("--root", default=".laoban")
    today.add_argument("--who", required=True, help="员工 id")
    today.add_argument("--date", default="", help="日期 YYYY-MM-DD（默认今天）")

    dash = sub.add_parser("dashboard", help="启动 Web 看板（默认 127.0.0.1:7891）")
    dash.add_argument("--root", default=".laoban")
    dash.add_argument("--port", type=int, default=7891)

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
                              assignee=args.assignee, due_date=args.due, source=args.source)
            print(f"人类待办已创建：{ht.id} {ht.title} → {ht.assignee}")
            return 0
        if args.todo_command == "done":
            inbox.complete(args.id, result=args.result)
            print(f"人类待办已完成：{args.id}")
            return 0

    if cmd == "today":
        st = JsonStore(args.root)
        inbox = HumanInbox(st)
        date = args.date or datetime.date.today().isoformat()
        mine = inbox.daily_list(assignee=args.who, date=date)
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

    return 1
