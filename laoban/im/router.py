"""渠道路由：任意 IM 渠道进来的消息 → 消息总线（+ AI 回信/人投递）→ 推回 IM。

渠道无关：入口渠道只需提供（platform, im_user, text）与推送回调
push(im_user, text)；具体怎么推（飞书 API/其他 IM）由渠道适配层负责。
消息总线是唯一事实源：即使推送失败，消息与回信也已落库。
"""
from __future__ import annotations

import re

from ..core.store import JsonStore
from ..llm.gateway import LLMGateway
from .binding import Bindings
from ..runner.chat import chat_reply

# 「同事id: 内容」——含字母的 token 视为收件人意图（纯数字如 "9:00" 不误判）
_TARGET_RE = re.compile(r"^\s*([\w\-]+)\s*[:：]\s*(.+)$", re.S)


def parse_target(text: str, store: JsonStore) -> tuple[str | None, str]:
    """解析「同事id: 内容」。

    - token 是在职员工 id → (token, 内容)；
    - token 纯数字（如 "9:00 开会"）→ 不是收件人，整条视为内容；
    - token 含字母但不是员工 id（如 "ghost: hi"）→ 仍按收件人意图返回，
      由后续 chat_reply 报「员工不存在」，帮用户发现 id 写错。
    """
    m = _TARGET_RE.match(text.strip())
    if m:
        tok, rest = m.group(1), m.group(2).strip()
        if store.load_employee(tok):
            return tok, rest
        if not tok.isdigit():
            return tok, rest
    return None, text.strip()


def _safe_push(push, platform: str, im_user: str, text: str) -> bool:
    try:
        push(im_user, text)
        return True
    except Exception as e:
        print(f"[IM:{platform}] 推送失败（{im_user}）：{e!r}")
        return False


def route_inbound(store: JsonStore, gateway: LLMGateway | None,
                  bindings: Bindings, platform: str, im_user: str, text: str,
                  push, default_to: str = "") -> dict:
    """处理一条入站 IM 消息，返回审计摘要。

    - 发送者未绑定 → 推送绑定指引；
    - 收件人是 AI：chat_reply（走消息总线 + Runner 上下文）→ 回信推回发送者 IM；
    - 收件人是人类：仅投递消息总线；若对方也绑定本渠道，同步推送其 IM；
    - 权限/不存在/无网关等错误以 ⚠️ 文案推回发送者，不炸渠道线程。
    """
    sender = bindings.lookup(platform, im_user)
    if not sender:
        _safe_push(push, platform, im_user,
                   f"⚠️ 你的 IM 账号未绑定员工 id。管理员执行："
                   f"laoban im bind --platform {platform} --im-user {im_user} "
                   f"--employee <员工id>")
        return {"summary": f"未绑定：{platform}:{im_user}"}

    target, content = parse_target(text, store)
    if target is None:
        target = default_to or ""
    if not target:
        _safe_push(push, platform, im_user,
                   "请指定收件同事，格式「同事id: 内容」，如「dev: 你好」；"
                   "可用 id 运行 laoban employees 查看")
        return {"summary": f"{sender} 未指定收件人"}

    if gateway is None:
        t = store.load_employee(target)
        if t and t.kind == "ai":
            _safe_push(push, platform, im_user,
                       "⚠️ 聊天需要 LLM 网关（未配置 LAOBAN_*_API_KEY）")
            return {"summary": f"{sender} → {target}：无网关，未回信"}

    try:
        result = chat_reply(store, gateway, sender, target, content)
    except KeyError as e:
        _safe_push(push, platform, im_user, f"⚠️ {e}")
        return {"summary": f"{sender} → {target}：不存在"}
    except ValueError as e:
        _safe_push(push, platform, im_user, f"⚠️ {e}")
        return {"summary": f"{sender} → {target}：状态异常"}
    except Exception as e:  # PermissionDenied / ProviderError 等
        _safe_push(push, platform, im_user, f"⚠️ {e}")
        return {"summary": f"{sender} → {target}：{type(e).__name__}"}

    reply = result["reply"]
    if reply is not None:
        ok = _safe_push(push, platform, im_user, reply)
        return {"summary": f"{sender} → {target}：已回信"
                + ("" if ok else "（回信推送失败，已落消息总线）")}

    # 收件人是人类：只投递；有绑定则同步推送对方 IM（人↔人经总线中转）
    tgt_im = bindings.lookup_by_employee(platform, target)
    if tgt_im:
        relay_ok = _safe_push(push, platform, tgt_im, f"[{sender}] {content}")
        note = "已投递并推送到对方 IM" if relay_ok else "已投递（对方 IM 推送失败）"
    else:
        note = f"已投递到 {target} 的收件箱（对方未绑定 IM，通过看板/CLI 查看）"
    _safe_push(push, platform, im_user, f"✅ {note}")
    return {"summary": f"{sender} → {target}：{note}"}
