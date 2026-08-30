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
            "请基于任务要求产出可交付结果，严格遵守任务中的输出格式约定。"
        )
        user = f"任务：{task.title}"
        if task.instruction:
            user += f"\n\n任务详细要求（严格遵守，包括函数名/文件名/输出格式）：\n{task.instruction}"
        messages = [
            Message(role="system", content=system),
            Message(role="user", content=user),
        ]
        resp = self.gateway.chat_for_employee(employee.model_config, messages)
        return resp.content
