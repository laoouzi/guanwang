from __future__ import annotations

from .core.store import JsonStore
from .core.employee import Employee
from .llm.gateway import LLMGateway
from .llm.base import Message

FOUNDERS = [
    {"id": "hr", "name": "HR 专家", "title": "组织设计", "department": "hr_dept"},
    {"id": "legal", "name": "法务专家", "title": "合规把关", "department": "legal_dept"},
    {"id": "it", "name": "IT 专家", "title": "工具与权限", "department": "it_dept"},
]


def bootstrap_org(store: JsonStore, gateway: LLMGateway, business: str) -> dict:
    """启动模式：入职三元老，各自基于业务构想产出组织设计建议。

    三元老即部门负责人占位（HR/法务/IT），后续业务部门由组织设计
    方案审批后生成（v0.1 简化：仅产出建议文本，部门落地由双轨招聘承接）。
    """
    for f in FOUNDERS:
        store.save_employee(Employee(
            id=f["id"], name=f["name"], title=f["title"], department=f["department"],
            source="founder",
            model_config={"provider": f["id"], "model": "mock"},
        ))
    result = {"组织设计方案": f"基于业务「{business}」的三元老初步设计", "business": business}
    for f in FOUNDERS:
        resp = gateway.chat(f["id"], [
            Message(role="system", content=f"你是{f['title']}"),
            Message(role="user", content=f"业务构想：{business}，请给出你的领域建议"),
        ])
        result[f["id"]] = resp.content
    return result
