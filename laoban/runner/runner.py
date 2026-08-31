from __future__ import annotations

import re

from laoban.core.task import Task
from laoban.core.employee import Employee
from laoban.llm.gateway import LLMGateway
from laoban.llm.base import Message
from laoban.core.memory import render_experience

from .collab_tools import build_collab_tools, parse_tool_blocks

TOOL_PROTOCOL = """\
协作工具（找同事配合时使用，输出格式严格遵守）：
[TOOL] 工具名
{"参数名": "值"}
[/TOOL]

可用工具：
- send_message：给同事发消息（to, content）
- delegate_task：把子任务派给同事（assignee, title, instruction, due 可选）

规则：
- 工具块之外可以写说明文字；工具执行结果会回传给你；
- 被拒绝（❌）时换人或换方式，不要重复同样的调用；
- 任务能自己完成就不必调用工具；完成后直接输出最终交付（纯文本，无工具块）。"""

_TOOL_RE = re.compile(r"\[TOOL\]\s*(\w+)\s*\n(.*?)\n\[/TOOL\]", re.DOTALL)


class Runner:
    """执行引擎：组装 prompt（含组织通讯录）→ LLM → [TOOL] 协作循环 → 产出。

    v0.2 起支持 AI 自主协作：
    - store 注入后，system prompt 携带组织通讯录（AI 能看见谁能帮忙）；
    - LLM 输出 [TOOL] 块即发起协作（发消息/派任务），执行结果回传继续推理；
    - 权限守卫在工具层拦截，拒绝以 ❌ 反馈给 AI 重试，不炸执行循环。
    """

    def __init__(self, gateway: LLMGateway, store=None, max_tool_rounds: int = 3):
        self.gateway = gateway
        self.store = store            # None = 无协作上下文（旧版行为）
        self.max_tool_rounds = max_tool_rounds

    _INBOX_LIMIT = 5   # 注入留言上限，防 token 膨胀

    def _system(self, employee: Employee) -> str:
        system = (
            f"你是 {employee.name}（{employee.title or '员工'}）。"
            f"岗位职责：{employee.job_description.get('mission', '')}。"
            "\n过往经验（验收复盘沉淀，先吸取教训再动手）：\n"
            f"{render_experience(employee)}"
            "\n请基于任务要求产出可交付结果，严格遵守任务中的输出格式约定。"
        )
        if self.store is not None:
            from laoban.core.directory import render_directory
            from laoban.core.messenger import inbox
            directory = render_directory(self.store, exclude_id=employee.id)
            if directory:
                system += (
                    "\n\n组织通讯录（你的协作对象，含人类同事）：\n"
                    f"{directory}\n\n{TOOL_PROTOCOL}"
                )
            # 同事留言（含人类发来的指令/提问）：AI 能「听到」人说话
            box = inbox(self.store, employee.id)[:self._INBOX_LIMIT]
            if box:
                lines = [f"- {m['from']}：{m['content']}" for m in box]
                system += ("\n\n同事留言（最新在前，可能是人类同事给你的指令或提问，"
                           "执行任务时请纳入考虑）：\n" + "\n".join(lines))
        return system

    def run(self, employee: Employee, task: Task) -> str:
        messages = [
            Message(role="system", content=self._system(employee)),
            Message(role="user", content=self._user(task)),
        ]
        actions: list[str] = []   # 协作动作审计记录

        resp = self.gateway.chat_for_employee(employee.model_config, messages)
        content = resp.content

        rounds = 0
        while self.store is not None and rounds < self.max_tool_rounds:
            blocks = parse_tool_blocks(content)
            if not blocks:
                break
            tools = build_collab_tools(self.store, employee)
            results: list[str] = []
            for name, args in blocks:
                tool = tools.get(name)
                if tool is None:
                    results.append(f"❌ 未知工具：{name}（可用：{', '.join(tools)}）")
                    actions.append(f"{name} → ❌未知工具")
                    continue
                if "__raw__" in args:
                    results.append(f"❌ 工具 {name} 参数不是合法 JSON：{args['__raw__']}")
                    actions.append(f"{name} → ❌参数解析失败")
                    continue
                out = tool.execute(args)
                results.append(out)
                actions.append(f"{name} → {out}")
            messages.append(Message(role="user", content=(
                "工具执行结果：\n" + "\n".join(results) +
                "\n\n请基于以上结果继续；如仍需协作可再次调用工具；"
                "若任务已完成，直接输出最终交付（纯文本，无工具块）。")))
            resp = self.gateway.chat_for_employee(employee.model_config, messages)
            content = resp.content
            rounds += 1

        if actions:
            content += "\n\n[协作动作]\n" + "\n".join(f"- {a}" for a in actions)
        return content

    @staticmethod
    def _user(task: Task) -> str:
        user = f"任务：{task.title}"
        if task.instruction:
            user += f"\n\n任务详细要求（严格遵守，包括函数名/文件名/输出格式）：\n{task.instruction}"
        return user
