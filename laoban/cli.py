from __future__ import annotations

import argparse
import datetime
import json
import os
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

    org = sub.add_parser("org", help="组织配置（v0.2：org.json 驱动部门/岗位/权限）")
    org_sub = org.add_subparsers(dest="org_command", required=True)
    oi = org_sub.add_parser("init-config", help="生成 org.json 模板（默认 .laoban/org.json）")
    oi.add_argument("--root", default=".laoban")
    oi.add_argument("--force", action="store_true", help="覆盖已有配置")
    os_show = org_sub.add_parser("show", help="查看组织配置（org.json 优先，否则内置模板）")
    os_show.add_argument("--root", default=".laoban")
    os_show.add_argument("--file", default="", help="指定 org.json 路径")
    ol = org_sub.add_parser("load", help="按组织配置批量入职员工")
    ol.add_argument("--root", default=".laoban")
    ol.add_argument("--file", default="", help="指定 org.json 路径")
    ol.add_argument("--founders-only", action="store_true", help="仅入职创始人角色")
    ol.add_argument("--team-only", action="store_true", help="仅入职非创始人角色")

    emp_list = sub.add_parser("employees", help="列出员工")
    emp_list.add_argument("--root", default=".laoban")

    emp = sub.add_parser("employee", help="员工生命周期（停职/上岗/解雇）")
    emp_sub = emp.add_subparsers(dest="emp_command", required=True)
    for name, help_ in (("suspend", "停职（active→suspended，可恢复）"),
                        ("activate", "上岗（suspended→active）"),
                        ("terminate", "解雇（→terminated，不可逆）")):
        s = emp_sub.add_parser(name, help=help_)
        s.add_argument("--root", default=".laoban")
        s.add_argument("--id", required=True, help="员工 id")

    msg = sub.add_parser("msg", help="员工点对点消息（collaboration 权限内）")
    msg_sub = msg.add_subparsers(dest="msg_command", required=True)
    ms = msg_sub.add_parser("send", help="发消息")
    ms.add_argument("--root", default=".laoban")
    ms.add_argument("--from", dest="from_id", required=True)
    ms.add_argument("--to", required=True)
    ms.add_argument("--content", required=True)
    ms.add_argument("--task-id", default="", help="关联任务 id（可选）")
    mi = msg_sub.add_parser("inbox", help="收件箱（最新在前）")
    mi.add_argument("--root", default=".laoban")
    mi.add_argument("--who", required=True)
    msn = msg_sub.add_parser("sent", help="发件箱")
    msn.add_argument("--root", default=".laoban")
    msn.add_argument("--who", required=True)

    task = sub.add_parser("task", help="任务操作")
    task_sub = task.add_subparsers(dest="task_command", required=True)
    ts = task_sub.add_parser("submit", help="提交任务")
    ts.add_argument("--root", default=".laoban")
    ts.add_argument("--title", required=True)
    tst = task_sub.add_parser("status", help="查看任务状态")
    tst.add_argument("--root", default=".laoban")
    tst.add_argument("--id", default="")

    auth_p = sub.add_parser("auth", help="员工口令鉴权（看板登录）")
    auth_sub = auth_p.add_subparsers(dest="auth_command", required=True)
    ap = auth_sub.add_parser("passwd", help="给员工设口令（设过任何一个即启用看板登录）")
    ap.add_argument("--root", default=".laoban")
    ap.add_argument("--who", required=True, help="员工 id")
    ap.add_argument("--password", default="", help="口令（留空则交互输入，不回显）")
    ar = auth_sub.add_parser("remove", help="清除员工口令")
    ar.add_argument("--root", default=".laoban")
    ar.add_argument("--who", required=True, help="员工 id")
    al = auth_sub.add_parser("list", help="查看已设口令的员工")
    al.add_argument("--root", default=".laoban")

    im_p = sub.add_parser("im", help="IM 渠道接入（飞书等 ↔ 消息总线）")
    im_sub = im_p.add_subparsers(dest="im_command", required=True)
    ib = im_sub.add_parser("bind", help="绑定 IM 账号 ↔ 员工 id")
    ib.add_argument("--root", default=".laoban")
    ib.add_argument("--platform", default="feishu", help="IM 平台（默认 feishu）")
    ib.add_argument("--im-user", required=True, help="IM 用户标识（飞书 open_id，如 ou_xxx）")
    ib.add_argument("--employee", required=True, help="员工 id")
    iu = im_sub.add_parser("unbind", help="解绑 IM 账号")
    iu.add_argument("--root", default=".laoban")
    iu.add_argument("--platform", default="feishu")
    iu.add_argument("--im-user", required=True)
    il = im_sub.add_parser("list", help="查看绑定表")
    il.add_argument("--root", default=".laoban")

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

    tassign = task_sub.add_parser("assign", help="派发任务给员工（入工位队列）")
    tassign.add_argument("--root", default=".laoban")
    tassign.add_argument("--id", required=True, help="任务 id")
    tassign.add_argument("--to", required=True, help="承接员工 id")
    tassign.add_argument("--actor", default="boss", help="派发人（默认 boss）")

    queue = sub.add_parser("queue", help="查看员工工位任务队列")
    queue.add_argument("--root", default=".laoban")
    queue.add_argument("--who", required=True, help="员工 id")

    today = sub.add_parser("today", help="人类员工当日任务清单")
    today.add_argument("--root", default=".laoban")
    today.add_argument("--who", required=True, help="员工 id")
    today.add_argument("--date", default="", help="日期 YYYY-MM-DD（默认今天）")

    dash = sub.add_parser("dashboard", help="启动 Web 看板（默认 127.0.0.1:7891）")
    dash.add_argument("--root", default=".laoban")
    dash.add_argument("--port", type=int, default=7891)
    dash.add_argument("--provider", default="",
                      help="聊天 LLM provider（deepseek/qwen/openai/kimi/ollama）；"
                           "默认自动发现 LAOBAN_* 环境变量，无则聊天不可用")
    dash.add_argument("--no-worker", action="store_true",
                      help="关闭自动运转引擎（派单后不自动执行，纯人工推状态）")
    dash.add_argument("--worker-interval", type=float, default=2.0,
                      help="自动运转引擎扫描间隔秒数（默认 2.0）")

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
                       hired_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       workspace={"dir": f"workspaces/{emp_id}/"})
        st.save_employee(emp)
        kind_label = "人类员工" if emp.kind == "human" else "AI 员工"
        print(f"已入职（{kind_label}）：{emp.name}（{emp.id}）"
              + (f"，部门：{emp.department}" if emp.department else ""))
        return 0

    if cmd == "org":
        from .org import (DEFAULT_TEMPLATE, init_config, instantiate, load_org,
                          resolve_org_path, summary)
        if args.org_command == "init-config":
            try:
                p = init_config(Path(args.root) / "org.json", force=args.force)
            except FileExistsError as e:
                print(f"⚠️ {e}")
                return 1
            print(f"组织配置已生成：{p}")
            print("编辑部门/岗位/权限后运行 laoban org load 应用到公司")
            return 0
        if args.org_command == "show":
            path = resolve_org_path(args.file or None, args.root)
            org = load_org(path)
            src = "内置默认模板" if path == DEFAULT_TEMPLATE else "用户配置"
            print(f"（{src}）{path}")
            print(summary(org))
            return 0
        if args.org_command == "load":
            path = resolve_org_path(args.file or None, args.root)
            org = load_org(path)
            which = ("founders" if args.founders_only
                     else "team" if args.team_only else "all")
            st = JsonStore(args.root)
            emps = instantiate(st, org, which=which)
            label = {"founders": "创始人", "team": "业务团队", "all": "全部"}[which]
            print(f"已按配置入职 {len(emps)} 名员工（{label}）")
            for e in emps:
                kind = "人类" if e.kind == "human" else "AI"
                print(f"  {e.id}\t[{kind}]\t{e.name}\t{e.title}\t{e.department}")
            return 0

    if cmd == "employees":
        st = JsonStore(args.root)
        for e in st.list_employees():
            kind_label = "人类" if e.kind == "human" else "AI"
            print(f"{e.id}\t{kind_label}\t{e.name}\t{e.title}\t{e.department}\t{e.status}")
        return 0

    if cmd == "employee":
        from .core.lifecycle import activate_employee, suspend_employee, terminate_employee
        ops = {"suspend": suspend_employee, "activate": activate_employee,
               "terminate": terminate_employee}
        labels = {"suspend": "已停职", "activate": "已上岗", "terminate": "已解雇（不可逆）"}
        st = JsonStore(args.root)
        try:
            emp = ops[args.emp_command](st, args.id)
        except (KeyError, ValueError) as e:
            print(f"⚠️ {e}")
            return 1
        print(f"{labels[args.emp_command]}：{emp.name}（{emp.id}）")
        return 0

    if cmd == "msg":
        from .core.messenger import inbox as msg_inbox, sent as msg_sent, send as msg_send
        from .core.permission import PermissionDenied
        st = JsonStore(args.root)
        if args.msg_command == "send":
            try:
                m = msg_send(st, args.from_id, args.to, args.content,
                             task_id=args.task_id)
            except (KeyError, ValueError, PermissionDenied) as e:
                print(f"⚠️ {e}")
                return 1
            print(f"消息已发送：{m['id']} {args.from_id} → {args.to}"
                  + (f"（任务 {m['task_id']}）" if m["task_id"] else ""))
            return 0
        if args.msg_command == "inbox":
            box = msg_inbox(st, args.who)
            if not box:
                print(f"{args.who} 收件箱为空")
                return 0
            print(f"📥 {args.who} 的收件箱（{len(box)} 条，最新在前）：")
            for m in box:
                task_part = f"（任务 {m['task_id']}）" if m.get("task_id") else ""
                print(f"  {m['id']} {m['from']} → {m['to']}{task_part}")
                print(f"    {m['content']}")
            return 0
        if args.msg_command == "sent":
            out = msg_sent(st, args.who)
            if not out:
                print(f"{args.who} 发件箱为空")
                return 0
            print(f"📤 {args.who} 的发件箱（{len(out)} 条，最新在前）：")
            for m in out:
                print(f"  {m['id']} {m['from']} → {m['to']}：{m['content']}")
            return 0

    if cmd == "queue":
        from .core.workstation import queue_of
        st = JsonStore(args.root)
        try:
            q = queue_of(st, args.who)
        except KeyError as e:
            print(f"⚠️ {e}")
            return 1
        if not q:
            print(f"{args.who} 工位队列为空")
            return 0
        print(f"🗂 {args.who} 的工位任务队列（{len(q)} 个）：")
        for tid in q:
            t = st.load_task(tid)
            title = t.title if t else "（任务档案缺失）"
            print(f"  {tid}\t{title}")
        return 0

    if cmd == "task":
        st = JsonStore(args.root)
        if args.task_command == "submit":
            tid = f"T-{uuid.uuid4().hex[:6]}"
            st.save_task(Task(id=tid, title=args.title))
            print(f"任务已提交：{tid} {args.title}")
            return 0
        if args.task_command == "assign":
            from .core.state_machine import IllegalTransition
            from .core.workstation import assign_task_auto
            try:
                t = assign_task_auto(st, args.id, args.to, actor=args.actor)
            except (KeyError, ValueError, IllegalTransition) as e:
                print(f"⚠️ {e}")
                return 1
            print(f"任务已派发：{t.id} {t.title} → {args.to}（已入工位队列）")
            return 0
        if args.task_command == "status":
            for t in st.list_tasks():
                if args.id and t.id != args.id:
                    continue
                print(f"{t.id}\t{t.state}\t{t.title}")
            return 0

    if cmd == "auth":
        from .core.auth import AuthStore
        st = JsonStore(args.root)
        au = AuthStore(st.root)
        if args.auth_command == "passwd":
            emp = st.load_employee(args.who)
            if not emp:
                print(f"⚠️ 员工不存在：{args.who}")
                return 1
            pw = args.password
            if not pw:
                import getpass
                pw = getpass.getpass(f"为 {emp.name}（{emp.id}）设置口令：")
                confirm = getpass.getpass("再输一次确认：")
                if pw != confirm:
                    print("⚠️ 两次输入不一致，未保存")
                    return 1
            if not pw:
                print("⚠️ 口令不能为空")
                return 1
            au.set_password(args.who, pw)
            mode = "看板已启用登录" if au.enabled() else ""
            print(f"口令已设置：{emp.name}（{emp.id}）{mode}")
            return 0
        if args.auth_command == "remove":
            ok = au.remove(args.who)
            if ok:
                note = "（全部口令已清除，看板回到免鉴权模式）" if not au.enabled() else ""
                print(f"口令已清除：{args.who} {note}")
            else:
                print(f"⚠️ 该员工未设口令：{args.who}")
            return 0 if ok else 1
        if args.auth_command == "list":
            accounts = au.list_accounts()
            if not accounts:
                print("未设任何口令（看板免鉴权模式；laoban auth passwd 启用登录）")
            else:
                print(f"已设口令的员工（{len(accounts)} 名，看板需登录）：")
                for eid in accounts:
                    emp = st.load_employee(eid)
                    label = emp.name if emp else "（档案缺失）"
                    print(f"  {eid}\t{label}")
            return 0

    if cmd == "im":
        from .im.binding import Bindings
        st = JsonStore(args.root)
        bd = Bindings(st.root)
        if args.im_command == "bind":
            emp = st.load_employee(args.employee)
            if not emp:
                print(f"⚠️ 员工不存在：{args.employee}")
                return 1
            bd.bind(args.platform, args.im_user, args.employee)
            print(f"已绑定：{args.platform}:{args.im_user} ↔ {emp.name}（{emp.id}）")
            return 0
        if args.im_command == "unbind":
            ok = bd.unbind(args.platform, args.im_user)
            print("已解绑" if ok else f"⚠️ 未找到绑定：{args.platform}:{args.im_user}")
            return 0 if ok else 1
        if args.im_command == "list":
            items = bd.list()
            if not items:
                print("绑定表为空（laoban im bind 添加）")
                return 0
            print(f"IM 绑定表（{len(items)} 条）：")
            for b in items:
                print(f"  {b['platform']}\t{b['im_user']}\t→ {b['employee']}")
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
        from .core.auth import AuthStore
        from .dashboard.server import DashboardServer
        from .llm.gateway import LLMGateway
        from .llm.openai_compatible import register_from_env
        st = JsonStore(args.root)
        gw = LLMGateway()
        real = register_from_env(gw)
        provider = args.provider or (real[0] if real else "")
        if provider and provider not in gw.list_providers():
            print(f"⚠️ provider [{provider}] 未注册（可用：{', '.join(gw.list_providers()) or '无'}），聊天不可用")
            provider = ""
        if provider:
            # 员工 model_config.provider 可能是 org.json 里的演示名（dev/pm/...），
            # 统一把真实 LLM 挂到这些名下，聊天按员工自己的配置路由
            real_llm = gw.get_provider(provider)
            for e in st.list_employees():
                if e.kind != "ai":
                    continue
                pid = e.model_config.get("provider", "mock")
                if pid not in gw.list_providers():
                    gw.register_provider(pid, real_llm)
            print(f"聊天已启用：provider = {provider}（AI 员工统一路由，支持人↔AI 对话）")
        else:
            # 演示模式：无 LLM Key 也给 AI 员工挂 MockLLM，worker 照常自动执行
            # （与 acceptance 命令的回退一致）——否则派单后 AI 任务永远卡在
            # assigned 且无提示，首次体验者无从排查
            from .llm.mock import MockLLM
            for e in st.list_employees():
                if e.kind != "ai":
                    continue
                pid = e.model_config.get("provider", "mock")
                if pid not in gw.list_providers():
                    gw.register_provider(pid, MockLLM())
            print("演示模式（MockLLM）：未检测到 LLM Key，AI 员工按脚本自动执行"
                  "——设置 LAOBAN_MOONSHOT_API_KEY 等环境变量可切换真实 LLM")
        # 飞书接入：事件回调 URL 填 http://<主机>:<端口>/api/im/webhook/feishu
        feishu_hook = None
        from .im.binding import Bindings
        from .im.feishu import FeishuWebhook, feishu_from_env
        fs = feishu_from_env()
        if fs is not None:
            feishu_hook = FeishuWebhook(
                st, gw, fs, Bindings(st.root),
                default_to=os.environ.get("LAOBAN_IM_DEFAULT_TO", "").strip(),
                encrypt_key=os.environ.get(
                    "LAOBAN_FEISHU_ENCRYPT_KEY", "").strip())
            print(f"飞书接入已启用：事件回调 URL = http://<本机地址>:{args.port}/api/im/webhook/feishu"
                  "（需公网可达或内网穿透）")
        else:
            print("未配置飞书（LAOBAN_FEISHU_APP_ID / LAOBAN_FEISHU_APP_SECRET），IM 渠道未启用")
        server = DashboardServer(st, port=args.port, gateway=gw,
                                 feishu=feishu_hook, auth=AuthStore(st.root))
        # 自动运转引擎：有 LLM 即默认启动（--no-worker 关闭）
        worker = None
        if gw is not None and not args.no_worker:
            from .runner.worker import WorkerLoop
            worker = WorkerLoop(st, gw, interval=args.worker_interval)
            worker.start()
            print(f"自动运转已启用：派单后 AI 自动执行 → 汇报，等你在看板验收"
                  f"（每 {args.worker_interval}s 扫一次队列；--no-worker 关闭）")
        print(f"看板已启动：http://127.0.0.1:{server.port}/ （Ctrl+C 退出）")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n看板已停止")
        finally:
            if worker:
                worker.stop()
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
            gen = "LLM 产出" if r.get("llm_generated") else "⚠️ 兜底生成（非 LLM 真实产出）"
            print(f"{mark} [{r['category']}] {r['task_id']} — {r['reason']}"
                  f"（评审：{'通过' if r['review_passed'] else '封驳'}；{gen}）")
        print(f"\n验收结果：{passed}/{len(results)} 通过"
              + (" ✅ 达到 D2 目标（≥ 2/3）" if passed >= 2 and len(results) >= 2 else ""))
        return 0 if passed >= 2 or len(results) < 2 else 2

    return 1
