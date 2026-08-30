"""AI 员工的协作工具：让 AI 能主动找人（发消息 / 派任务）。

工具执行不抛异常——守卫拒绝时返回 ❌ 反馈文本，让 AI 能换人重试，
而不是炸掉整个执行循环（Human-on-Exception 思想的工具层版本）。

权限闸门（全部复用既有制度管道）：
  send_message  → Messenger.send（collaboration 白名单 + 在职校验）
  delegate_task → 人类：can_assign_human_tasks + HumanInbox（结果回传发起人）
                  AI：workstation.enqueue（新任务 born-assigned 入队）
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from ..core.employee import Employee
from ..core.permission import PermissionDenied
from ..core.store import JsonStore
from ..core.messenger import send as messenger_send
from ..core.human_inbox import HumanInbox
from ..core.task import Task, ASSIGNED, utcnow
from ..core.workstation import enqueue
from .tools import Tool


def _send_message_tool(store: JsonStore, actor: Employee) -> Tool:
    def execute(args: dict[str, Any]) -> str:
        to = args.get("to", "")
        content = args.get("content", "")
        if not to or not content:
            return "❌ 参数缺失：需要 to（员工 id）和 content（内容）"
        try:
            m = messenger_send(store, actor.id, to, content,
                               task_id=args.get("task_id", ""))
        except PermissionDenied as e:
            return f"❌ 权限拒绝：{e}"
        except (KeyError, ValueError) as e:
            return f"❌ {e}"
        return f"✅ 消息已送达 {m['to']}（{m['id']}）"

    return Tool("send_message", "给同事发点对点消息（参数：to, content, task_id 可选）", execute)


def _delegate_task_tool(store: JsonStore, actor: Employee) -> Tool:
    def execute(args: dict[str, Any]) -> str:
        assignee = args.get("assignee", "")
        title = args.get("title", "")
        if not assignee:
            return "❌ 参数缺失：需要 assignee（从组织通讯录选择员工 id）"
        if not title:
            return "❌ 参数缺失：需要 title（任务标题）"
        target = store.load_employee(assignee)
        if not target:
            return f"❌ 员工不存在：{assignee}，请从组织通讯录选择有效 id"
        if target.status != "active":
            return f"❌ {assignee}（{target.name}）当前{('停职' if target.status == 'suspended' else '已解雇')}，不可承接任务"

        instruction = args.get("instruction", "")
        if target.kind == "human":
            if not actor.permissions.get("can_assign_human_tasks"):
                return ("❌ 权限拒绝：你没有 can_assign_human_tasks 权限，"
                        "不能给人类员工派活；可先 send_message 沟通或上报上级")
            ht = HumanInbox(store).create(
                task_id=args.get("task_id", ""), title=title, assignee=assignee,
                deliverable_format=args.get("deliverable_format", ""),
                due_date=args.get("due", ""), source="ai_delegated",
                created_by=actor.id)
            return (f"✅ 已派发人类待办 {ht.id} 给 {target.name}（{assignee}），"
                    "完成后结果会回传给你")
        # AI 员工：创建新任务（born-assigned）并入其工位队列
        task = Task(id=f"T-{uuid.uuid4().hex[:6]}", title=title, instruction=instruction)
        task.state = ASSIGNED
        task.flow_log.append({
            "at": utcnow(), "from": "delegation", "to": ASSIGNED,
            "actor": actor.id, "remark": f"{actor.id} 委派给 {assignee}",
        })
        store.save_task(task)
        enqueue(store, assignee, task.id)
        return (f"✅ 已创建任务 {task.id}「{title}」并派给 {target.name}"
                f"（{assignee}），已入其工位队列")

    return Tool("delegate_task",
                "把子任务派给同事（参数：assignee, title, instruction, due 可选；"
                "人类走待办收件箱、AI 走工位队列）", execute)


def build_collab_tools(store: JsonStore, actor: Employee) -> dict[str, Tool]:
    """为某员工构造协作工具集（绑定 store 与发起人身份）。"""
    return {
        "send_message": _send_message_tool(store, actor),
        "delegate_task": _delegate_task_tool(store, actor),
    }


def parse_tool_blocks(text: str) -> list[tuple[str, dict[str, Any]]]:
    """解析 LLM 输出中的 [TOOL] 块（协议与 Runner 共享）。

    格式：
        [TOOL] tool_name
        {"json": "args"}
        [/TOOL]
    坏 JSON / 未知工具名在执行时反馈，这里只做结构解析。
    """
    import re
    blocks: list[tuple[str, dict[str, Any]]] = []
    for m in re.finditer(r"\[TOOL\]\s*(\w+)\s*\n(.*?)\n\[/TOOL\]", text, re.DOTALL):
        name, raw = m.group(1), m.group(2).strip()
        try:
            args = json.loads(raw) if raw else {}
            if not isinstance(args, dict):
                args = {}
        except json.JSONDecodeError:
            args = {"__raw__": raw}  # 保留原文，执行层反馈解析失败
        blocks.append((name, args))
    return blocks
