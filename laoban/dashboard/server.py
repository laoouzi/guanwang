from __future__ import annotations

import datetime
import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..core.store import JsonStore
from ..core.human_inbox import HumanInbox
from ..core.messenger import inbox as msg_inbox, sent as msg_sent
from ..core.workstation import queue_of

SESSION_COOKIE = "laoban_session"


class _Handler(BaseHTTPRequestHandler):
    store: JsonStore = None  # 由工厂注入
    gateway = None           # 可选：聊天端点需要 LLM 网关
    feishu = None            # 可选：飞书事件回调（IM 渠道入口）
    auth = None              # 可选：口令库（设过任何口令即启用登录）
    sessions: dict = None    # 会话表 token → emp_id（DashboardServer 注入）

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str):
        return self._json({"error": message}, status)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return {}

    # ---- 会话（登录后 Cookie 携带 token）----
    def _session_emp(self) -> str | None:
        """当前会话对应的员工 id；未登录返回 None。"""
        if not self.sessions:
            return None
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == SESSION_COOKIE and v:
                return self.sessions.get(v)
        return None

    def _require_own_identity(self, from_id: str):
        """鉴权启用后：必须登录，且只能以自己的员工身份发送。

        返回 None = 校验通过；否则返回 (status, error)。
        """
        if not self.auth or not self.auth.enabled():
            return None   # 免鉴权模式（未设任何口令）
        me = self._session_emp()
        if not me:
            return (401, "请先登录（POST /api/login）")
        if from_id != me:
            return (403, f"只能以自己的身份发送（当前登录：{me}）")
        return None

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/login":
            body = self._read_body()
            emp_id = body.get("id", "")
            password = body.get("password", "")
            if not (emp_id and password):
                return self._error(400, "缺少 id / password")
            if not (self.auth and self.auth.enabled()):
                return self._error(409, "未设任何口令（免鉴权模式，无需登录）")
            emp = self.store.load_employee(emp_id)
            if not emp:
                return self._error(404, f"员工不存在：{emp_id}")
            if not self.auth.verify(emp_id, password):
                return self._error(401, "员工 id 或口令错误")
            token = uuid.uuid4().hex
            self.sessions[token] = emp_id
            body_ = json.dumps({"id": emp.id, "name": emp.name,
                                "kind": emp.kind}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie",
                             f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; "
                             "SameSite=Strict")
            self.send_header("Content-Length", str(len(body_)))
            self.end_headers()
            self.wfile.write(body_)
            return
        if u.path == "/api/logout":
            raw = self.headers.get("Cookie", "")
            for part in raw.split(";"):
                k, _, v = part.strip().partition("=")
                if k == SESSION_COOKIE and v:
                    self.sessions.pop(v, None)
            body_ = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie",
                             f"{SESSION_COOKIE}=; Path=/; Max-Age=0")
            self.send_header("Content-Length", str(len(body_)))
            self.end_headers()
            self.wfile.write(body_)
            return
        if u.path == "/api/chat":
            if self.gateway is None:
                return self._error(503, "聊天需要 LLM 网关（未配置）")
            body = self._read_body()
            from_id = body.get("from", "")
            to_id = body.get("to", "")
            content = body.get("content", "")
            if not (from_id and to_id and content):
                return self._error(400, "缺少 from / to / content")
            denied = self._require_own_identity(from_id)
            if denied:
                return self._error(denied[0], denied[1])
            from ..runner.chat import chat_reply
            from ..core.permission import PermissionDenied
            from ..llm.openai_compatible import ProviderError
            try:
                result = chat_reply(self.store, self.gateway,
                                    from_id, to_id, content)
            except KeyError as e:
                return self._error(404, str(e))
            except PermissionDenied as e:
                return self._error(403, str(e))
            except ValueError as e:
                return self._error(409, str(e))
            except ProviderError as e:
                return self._error(502, f"LLM 调用失败：{e}")
            return self._json({
                "question": result["question"],
                "reply": result["reply"],
            })
        if u.path == "/api/im/webhook/feishu":
            if self.feishu is None:
                return self._error(503, "未配置飞书接入（LAOBAN_FEISHU_APP_ID / LAOBAN_FEISHU_APP_SECRET）")
            status, payload = self.feishu.handle(self._read_body())
            return self._json(payload, status)
        return self._error(404, f"未知路径：{u.path}")

    def _who_required(self, u) -> str | None:
        who = parse_qs(u.query).get("who", [""])[0]
        if not who:
            self._error(400, "缺少 who 参数")
            return None
        return who

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/me":
            if self.auth and self.auth.enabled():
                me = self._session_emp()
                if not me:
                    return self._error(401, "未登录")
                emp = self.store.load_employee(me)
                if not emp:
                    return self._error(401, "会话员工已不存在")
                return self._json({"id": emp.id, "name": emp.name,
                                   "kind": emp.kind, "title": emp.title})
            return self._json({"id": "", "name": "免鉴权模式",
                               "kind": "", "title": "未设口令，无需登录"})
        if u.path == "/api/tasks":
            return self._json([t.to_dict() for t in self.store.list_tasks()])
        if u.path == "/api/employees":
            return self._json([e.to_dict() for e in self.store.list_employees()])
        if u.path == "/api/org":
            return self._json(self._org())
        if u.path == "/api/human-tasks":
            q = parse_qs(u.query)
            who = q.get("who", [""])[0]
            date = q.get("date", [datetime.date.today().isoformat()])[0]
            inbox = HumanInbox(self.store)
            return self._json([ht.to_dict() for ht in inbox.daily_list(assignee=who, date=date)])
        if u.path == "/api/human-results":
            # 人→人闭环：查看发起人收到的回传结果
            q = parse_qs(u.query)
            who = q.get("who", [""])[0]
            inbox = HumanInbox(self.store)
            return self._json([ht.to_dict() for ht in inbox.results_for(who)])
        if u.path == "/api/messages":
            who = self._who_required(u)
            if who is None:
                return
            return self._json({
                "inbox": msg_inbox(self.store, who),
                "sent": msg_sent(self.store, who),
            })
        if u.path == "/api/queue":
            who = self._who_required(u)
            if who is None:
                return
            try:
                task_ids = queue_of(self.store, who)
            except KeyError:
                return self._error(404, f"员工不存在：{who}")
            tasks = {t.id: t for t in self.store.list_tasks()}
            return self._json([
                {"id": tid, "title": tasks[tid].title, "state": tasks[tid].state}
                if tid in tasks else {"id": tid, "title": "（任务档案缺失）", "state": ""}
                for tid in task_ids
            ])
        # 默认返回看板 HTML
        html = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _org(self) -> list[dict]:
        """组织架构视图：员工按部门分组（AI 与人类同部门）。"""
        departments: dict[str, dict] = {}
        for e in self.store.list_employees():
            dept_id = e.department or "（未分配）"
            d = departments.setdefault(dept_id, {"id": dept_id, "employees": []})
            d["employees"].append({
                "id": e.id, "name": e.name, "kind": e.kind,
                "title": e.title, "status": e.status,
                "queue": e.workspace.get("queue", []),
            })
        return list(departments.values())

    def log_message(self, *args):
        pass


class DashboardServer:
    def __init__(self, store: JsonStore, port: int = 7891, gateway=None,
                 feishu=None, auth=None):
        handler = type("H", (_Handler,), {
            "store": store, "gateway": gateway, "feishu": feishu,
            "auth": auth, "sessions": {},
        })
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.port = self.httpd.server_address[1]

    def serve_forever(self):
        self.httpd.serve_forever()

    def shutdown(self):
        self.httpd.shutdown()
