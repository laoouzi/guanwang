from __future__ import annotations

from laoban.core.task import Task
from laoban.core.employee import Employee
from laoban.llm.gateway import LLMGateway
from laoban.llm.base import Message
from laoban.core.memory import recall


class Runner:
    """执行引擎：组装 prompt → LLM → 产出。v0.1 工具循环按需调用。"""

    def __init__(self, gateway: LLMGateway):
        self.gateway = gateway

    def run(self, employee: Employee, task: Task) -> str:
        system = (
            f"你是 {employee.name}（{employee.title or '员工'}）。"
            f"岗位职责：{employee.job_description.get('mission', '')}。"
            f"经验记忆：{recall(employee)}。"
            "请基于任务标题产出可交付结果。"
        )
        messages = [
            Message(role="system", content=system),
            Message(role="user", content=f"任务：{task.title}"),
        ]
        resp = self.gateway.chat(employee.id, messages)
        return resp.content
