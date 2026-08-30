from __future__ import annotations

from .core.store import JsonStore
from .llm.gateway import LLMGateway
from .llm.base import Message
from .org import load_org, instantiate, iter_roles


def _founders_from(org: dict) -> list[dict]:
    return [
        {"id": r["id"], "name": r["name"], "title": r.get("title", ""),
         "department": d["id"]}
        for d, r in iter_roles(org) if r.get("founder")
    ]


# 兼容导出：从默认模板派生（单一事实来源）
FOUNDERS = _founders_from(load_org())


def bootstrap_org(store: JsonStore, gateway: LLMGateway, business: str,
                  org: dict | None = None) -> dict:
    """启动模式：按组织配置入职创始人，各自基于业务构想产出组织设计建议。

    配置来源：store 数据目录下 org.json（用户定制）优先，否则内置默认模板。
    创始人即角色标 `founder: true` 的岗位（默认 HR/法务/IT 三元老），
    后续业务部门由组织设计方案审批后生成（v0.1 简化：仅产出建议文本，
    部门落地由双轨招聘承接）。
    """
    if org is None:
        from .org import load_org_for_store
        org = load_org_for_store(store)
    founders = instantiate(store, org, which="founders")
    result = {"组织设计方案": f"基于业务「{business}」的创始人初步设计", "business": business}
    for emp in founders:
        provider = emp.model_config.get("provider", emp.id)
        resp = gateway.chat(provider, [
            Message(role="system", content=f"你是{emp.title or emp.name}"),
            Message(role="user", content=f"业务构想：{business}，请给出你的领域建议"),
        ])
        result[emp.id] = resp.content
    return result
