"""人↔AI 对话闭环：人提问 → 消息总线 → AI（带通讯录+留言上下文）回复 → 回信。

设计要点：
- 消息总线是唯一事实源（任务/审计都在 laoban 内），渠道只是入口；
- 收件人是人类时只投递不触发 LLM（人回答人是 IM/当面的事）；
- AI 回复经 Runner 生成——它天然带着组织通讯录与最近留言，
  所以「听到提问」不需要额外通道。
"""
from __future__ import annotations

from ..core.employee import Employee
from ..core.store import JsonStore
from ..core.task import Task
from ..core.messenger import send as msg_send
from ..llm.gateway import LLMGateway
from .runner import Runner


def chat_reply(store: JsonStore, gateway: LLMGateway, from_id: str,
               to_id: str, content: str) -> dict:
    """人（或任意员工）向 AI 员工提问，返回 {question, reply}。

    - to 是人类：仅投递消息，reply=None；
    - to 是 AI：投递后用 Runner 生成回复并回信 from。
    """
    question = msg_send(store, from_id, to_id, content)
    target = store.load_employee(to_id)
    if not target:
        raise KeyError(f"员工不存在：{to_id}")
    if target.kind != "ai":
        return {"question": question, "reply": None}

    runner = Runner(gateway, store=store)
    reply_task = Task(
        id=f"CHAT-{question['id']}",
        title=f"回复 {from_id} 的留言",
        instruction=(
            f"同事 {from_id} 通过消息总线向你提问（见收件箱留言）。"
            "请直接、简洁地回答该提问；本回复会作为消息回给对方。"
        ),
    )
    try:
        answer = runner.run(target, reply_task).strip()
    except Exception as e:
        # 执行失败也回信：不留「提问已送达却永远等不到回复」的半失败状态
        print(f"[chat] {to_id} 回复失败：{e!r}")
        answer = (f"（{target.name} 暂时无法回复，请稍后重试或联系老板"
                  f"｜原因：{str(e)[:80]}）")
    reply_msg = msg_send(store, to_id, from_id, answer)
    return {"question": question, "reply": answer, "reply_msg": reply_msg}
