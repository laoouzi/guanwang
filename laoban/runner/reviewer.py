from __future__ import annotations

from dataclasses import dataclass

from ..core.task import Task
from ..core.employee import Employee
from ..llm.base import Message
from ..llm.gateway import LLMGateway

DEFAULT_CHECKLIST = [
    "方案完整性：是否覆盖需求要点",
    "子任务拆解合理性：粒度是否可执行",
    "安全合规风险：是否有越权/数据外发/违规内容",
    "验收标准明确性：能否客观判定完成",
]


@dataclass
class ReviewDecision:
    approved: bool
    reason: str


class Reviewer:
    """评审员：用检查清单驱动 LLM 输出「准奏/封驳」判断（合规检查单层）。"""

    def __init__(self, gateway: LLMGateway, checklist: list[str] | None = None):
        self.gateway = gateway
        self.checklist = checklist or DEFAULT_CHECKLIST

    def review(self, employee: Employee, task: Task, plan: str) -> ReviewDecision:
        checklist_text = "\n".join(f"- {c}" for c in self.checklist)
        system = (
            "你是评审员。逐项审查任务产出，输出「准奏」或「封驳」及理由，"
            f"审查清单：\n{checklist_text}\n"
            "评审基准：以【任务明确列出的要求】为准；任务没有要求的内容"
            "（如未要求的接口文档要素、额外章节）不构成封驳理由。\n"
            "注意：审查对象是任务的最终产出（可能是代码/数据/文档交付物，"
            "不一定是规划方案）。交付物类产出重点核对是否满足任务要求与"
            "格式约定（如文件齐全、统计正确、章节达标），不要用「子任务拆解」"
            "的规划标准去衡量交付物；只有存在实质缺陷（缺失、错误、违规）才封驳。"
        )
        req = task.instruction or task.title
        messages = [
            Message(role="system", content=system),
            Message(role="user",
                    content=f"任务：{task.title}\n任务要求：\n{req}\n\n产出：\n{plan}"),
        ]
        resp = self.gateway.chat_for_employee(employee.model_config, messages)
        content = resp.content
        approved = ("封驳" not in content) and ("驳回" not in content)
        return ReviewDecision(approved=approved, reason=content)
