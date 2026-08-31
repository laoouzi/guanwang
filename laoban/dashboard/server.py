from __future__ import annotations

import datetime
import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..core.store import JsonStore
from ..core.employee import Employee
from ..core.human_inbox import HumanInbox
from ..core.messenger import inbox as msg_inbox, sent as msg_sent
from ..core.workstation import (queue_of, assign_task_auto, dequeue, enqueue)
from ..core.task import (Task, ASSIGNED, DOING, REPORTING, DONE,
                         HORIZONS, HORIZON_LABELS)
from ..core.state_machine import advance, IllegalTransition, MAX_REVIEW_ROUNDS
from ..core.retro import review_and_learn
from ..core.ledger import FileLedger
from ..runner.approval_log import ApprovalLog
from . import rbac

SESSION_COOKIE = "laoban_session"


def _task_source(task: Task) -> str:
    """任务来源：assigned=被动分配（别人派给我）/ self=个人计划（自己提的）/
    unassigned=未指派。"""
    if not task.assignee:
        return "unassigned"
    if task.created_by and task.created_by == task.assignee:
        return "self"
    return "assigned"


def _plans_view(store, who: str, role: str, me) -> dict:
    """个人任务计划视图：按周期分组 + 来源 + 完成情况。

    who 为空：admin=全公司；manager=本部门成员（含自己）。
    返回：{"who": …, "horizons": [{"key","label","total","done","on_time",
    "late","tasks":[{id,title,state,source,due_at,on_time}]}], "overall": …}
    """
    from ..core.task import DONE
    from ..core.points import on_time_points

    # 圈定可见人集合（任务按 assignee / created_by 归属）
    if who:
        scope_ids = {who}
    elif role == rbac.MANAGER and me is not None:
        scope_ids = set(rbac.dept_members(store, me)) | {me.id}
    else:
        scope_ids = None   # 全公司

    tasks = store.list_tasks()
    if scope_ids is not None:
        tasks = [t for t in tasks
                 if t.assignee in scope_ids or t.created_by in scope_ids]

    groups: dict[str, list] = {h: [] for h in HORIZONS}
    groups[""] = []
    for t in tasks:
        groups.setdefault(t.plan_horizon if t.plan_horizon in HORIZONS else "", []).append(t)

    horizons_out = []
    total_all = done_all = on_time_all = late_all = 0
    for key in ("day", "week", "month", "quarter", "half_year", "year", ""):
        bucket = groups.get(key, [])
        if not bucket:
            continue   # 空周期不渲染
        done = sum(1 for t in bucket if t.state == DONE)
        on_time = sum(1 for t in bucket
                      if t.state == DONE and on_time_points(t.due_at, t.updated_at) is not None
                      and on_time_points(t.due_at, t.updated_at) > 0)
        late = sum(1 for t in bucket
                   if t.state == DONE and on_time_points(t.due_at, t.updated_at) is not None
                   and on_time_points(t.due_at, t.updated_at) < 0)
        total_all += len(bucket)
        done_all += done
        on_time_all += on_time
        late_all += late
        horizons_out.append({
            "key": key, "label": HORIZON_LABELS[key],
            "total": len(bucket), "done": done,
            "on_time": on_time, "late": late,
            "completion_rate": round(done / len(bucket), 4) if bucket else 0.0,
            "tasks": [{
                "id": t.id, "title": t.title, "state": t.state,
                "source": _task_source(t),
                "assignee": t.assignee, "created_by": t.created_by,
                "due_at": t.due_at,
                "on_time": (on_time_points(t.due_at, t.updated_at) > 0
                            if t.state == DONE
                            and on_time_points(t.due_at, t.updated_at) is not None
                            else None),
            } for t in bucket],
        })
    return {
        "who": who,
        "scope": "全公司" if scope_ids is None
                 else (f"本部门+我（{len(scope_ids)}人）" if role == rbac.MANAGER else who),
        "horizons": horizons_out,
        "overall": {
            "total": total_all, "done": done_all,
            "on_time": on_time_all, "late": late_all,
            "completion_rate": (round(done_all / total_all, 4)
                                if total_all else 0.0),
        },
    }


def _elapsed_since_assigned(task: Task) -> float:
    """派单 → 现在的耗时（秒）：取 flow_log 最近一次流转到 assigned 的时刻。

    找不到/不可解析返回 0（不虚造数据；成本口径为 0 = 未耗时）。
    """
    for entry in reversed(task.flow_log):
        if entry.get("to") == ASSIGNED and entry.get("at"):
            try:
                dt = datetime.datetime.fromisoformat(
                    entry["at"].replace("Z", "+00:00"))
            except ValueError:
                break
            now = datetime.datetime.now(datetime.timezone.utc)
            return max(0.0, now.timestamp() - dt.timestamp())
    return 0.0


def _parse_due(s: str) -> str:
    """截止时间解析：ISO 时间（2026-12-31T18:00）或日期（2026-12-31 → 当天 23:59:59）。

    返回归一化的 ISO 字符串（含时区）；无效/空返回 ""（= 不限期）。
    """
    s = (s or "").strip()
    if not s:
        return ""
    from datetime import datetime, timedelta, timezone
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # 仅日期（时分秒全 0）→ 截止到当天结束
    if dt.hour == dt.minute == dt.second == 0:
        dt += timedelta(hours=23, minutes=59, seconds=59)
    return dt.astimezone(timezone.utc).isoformat()


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

    # ---- 视图权限（RBAC-lite）----
    def _view(self) -> tuple[str, object]:
        """返回 (role, me)。免鉴权模式或未登录 → (admin, None)。

        未登录且鉴权启用时 GET 数据接口一律 401（由 _require_view 统一处理）。
        """
        if not self.auth or not self.auth.enabled():
            return rbac.ADMIN, None
        emp_id = self._session_emp()
        if not emp_id:
            return "", None
        emp = self.store.load_employee(emp_id)
        if not emp:
            return rbac.STAFF, None
        return rbac.role_of(self.store, emp), emp

    def _require_view(self):
        """鉴权启用后 GET 数据必须登录。

        返回 None = 通过；否则 (status, error, me, role) 元组的前两项。
        """
        role, me = self._view()
        if not self.auth or not self.auth.enabled():
            return None, (rbac.ADMIN, None)
        if not me:
            return (401, "请先登录后再查看数据"), (role, None)
        return None, (role, me)

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
            from . import rbac as _rbac
            body_ = json.dumps({"id": emp.id, "name": emp.name,
                                "kind": emp.kind, "title": emp.title,
                                "department": emp.department,
                                "role": _rbac.role_of(self.store, emp)},
                               ensure_ascii=False).encode()
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

        # ---- 任务操作 / 审批决策 / 编制申请（老板驾驶舱）----
        if u.path in ("/api/task/submit", "/api/task/assign",
                      "/api/task/accept", "/api/task/report",
                      "/api/task/retry",
                      "/api/approval/decide",
                      "/api/headcount/submit", "/api/headcount/decide"):
            return self._handle_operation(u.path)
        return self._error(404, f"未知路径：{u.path}")

    def _actor(self, me) -> str:
        return me.id if me is not None else "dashboard"

    def _handle_operation(self, path: str):
        """操作端点统一入口：登录校验 → 角色守卫 → 复用 core 逻辑。"""
        if self.auth and self.auth.enabled() and not self._session_emp():
            return self._error(401, "请先登录")
        role, me = self._view()
        body = self._read_body()
        try:
            if path == "/api/task/submit":
                return self._op_submit(role, me, body)
            if path == "/api/task/assign":
                return self._op_assign(role, me, body)
            if path == "/api/task/accept":
                return self._op_accept(role, me, body)
            if path == "/api/task/report":
                return self._op_report(role, me, body)
            if path == "/api/task/retry":
                return self._op_retry(role, me, body)
            if path == "/api/approval/decide":
                return self._op_approve(role, me, body)
            if path == "/api/headcount/submit":
                return self._op_headcount_submit(role, me, body)
            if path == "/api/headcount/decide":
                return self._op_headcount_decide(role, me, body)
        except KeyError as e:
            return self._error(404, str(e))
        except ValueError as e:
            return self._error(409, str(e))
        except IllegalTransition as e:
            return self._error(409, str(e))
        return self._error(404, f"未知路径：{path}")

    def _op_submit(self, role: str, me, body: dict):
        """提交任务：任何登录员工都可（免鉴权模式任何人）。"""
        title = str(body.get("title", "")).strip()
        if not title:
            return self._error(400, "缺少 title")
        due_at = _parse_due(str(body.get("due_at", "")).strip())
        if body.get("due_at") and not due_at:
            return self._error(400, "due_at 格式无效（ISO 时间，如 2026-12-31T18:00:00）")
        horizon = str(body.get("plan_horizon", "")).strip()
        if horizon and horizon not in HORIZONS:
            return self._error(400, f"plan_horizon 无效（可选：{'/'.join(HORIZONS)}）")
        task = Task(id=f"T-{uuid.uuid4().hex[:6]}", title=title,
                    instruction=str(body.get("instruction", "")).strip(),
                    due_at=due_at, plan_horizon=horizon,
                    created_by=self._actor(me))
        self.store.save_task(task)
        return self._json({"id": task.id, "title": task.title,
                           "state": task.state, "due_at": task.due_at,
                           "plan_horizon": task.plan_horizon,
                           "message": f"任务已提交：{task.id}"})

    def _op_assign(self, role: str, me, body: dict):
        """派发：admin 全公司；manager 仅本部门成员；staff 拒绝。"""
        task_id = str(body.get("id", "")).strip()
        to = str(body.get("to", "")).strip()
        if not (task_id and to):
            return self._error(400, "缺少 id / to")
        if role not in (rbac.ADMIN, rbac.MANAGER):
            return self._error(403, "仅管理员或部门负责人可派单")
        if role == rbac.MANAGER and to not in rbac.dept_members(self.store, me):
            return self._error(403, "只能派发给本部门成员")
        task = assign_task_auto(self.store, task_id, to, actor=self._actor(me))
        self.ledger.record_step(to)
        return self._json({"id": task.id, "state": task.state,
                           "message": f"任务已派发给 {to}（已入工位队列）"})

    def _learn(self, assignee: str, emp, task, score: int, comment: str):
        """复盘并原子回写经验：LLM 在锁外算（避免长持锁），
        锁内只把教训合并进最新员工对象（防并发派单被旧对象覆盖）。"""
        exp = review_and_learn(self.store, emp, task,
                               score=score, comment=comment,
                               gateway=self.gateway)
        if exp:
            self.store.update_employee(
                assignee,
                lambda e: e.memory.setdefault("experiences", []).append(exp))
        return exp

    def _op_accept(self, role: str, me, body: dict):
        """验收：DOING/REPORTING → DONE；低分且未超返工上限 → 驳回返工回炉。

        结案：评分回写记忆 + 账本记账 + 出队；返工：留队列等重做 +
        复盘教训 + 驳回扣分（不记完成——没通过就不算交付）。
        """
        from ..core.points import (points_for_acceptance, on_time_points,
                                   PENALTY_REJECTION, LOW_SCORE)
        task_id = str(body.get("id", "")).strip()
        if not task_id:
            return self._error(400, "缺少 id")
        try:
            score = int(body.get("score", 0))
        except (TypeError, ValueError):
            return self._error(400, "score 必须是 1-5 的整数")
        if not 1 <= score <= 5:
            return self._error(400, "score 必须在 1-5")
        comment = str(body.get("comment", "")).strip()
        task = self.store.load_task(task_id)
        if not task:
            return self._error(404, f"任务不存在：{task_id}")

        # 找承接人：优先持久的 assignee 字段；旧数据兜底扫工位队列
        assignee = task.assignee
        if not assignee:
            for e in self.store.list_employees():
                if task_id in e.workspace.get("queue", []):
                    assignee = e.id
                    break
        if role == rbac.MANAGER and assignee and \
                assignee not in rbac.dept_members(self.store, me):
            return self._error(403, "只能验收本部门成员的任务")

        if task.state not in (DOING, REPORTING):
            return self._error(409, f"当前状态 {task.state} 不可验收（需 doing/reporting）")
        actor = self._actor(me)
        on_time = None   # 时效判定结果（None=无限期；无承接人时保持 None）
        if task.state == DOING:
            advance(task, REPORTING, actor=actor, remark="验收前汇报（看板）")

        # 低分 = 驳回：未超返工上限且承接人在职 → 回炉重做；否则强制结案
        emp = self.store.load_employee(assignee) if assignee else None
        rework = (score <= LOW_SCORE
                  and task.review_round < MAX_REVIEW_ROUNDS
                  and emp is not None and emp.status == "active")
        if rework:
            advance(task, ASSIGNED, actor=actor,
                    remark=f"驳回返工（评分 {score}/5）{('：' + comment) if comment else ''}")
        else:
            remark = (f"验收通过（评分 {score}/5）" if score > LOW_SCORE
                      else f"驳回超限，强制结案（评分 {score}/5）")
            advance(task, DONE, actor=actor,
                    remark=f"{remark}{('：' + comment) if comment else ''}")
        self.store.save_task(task)

        review = None
        promotion = None
        if assignee:
            if rework:
                # 返工：回队列等重做（幂等）+ 复盘教训 + 驳回扣分
                enqueue(self.store, assignee, task_id)
                if emp:
                    review = self._learn(assignee, emp, task, score, comment)
                self.ledger.record_rejection(assignee)
                self.ledger.record_points(
                    assignee, -PENALTY_REJECTION,
                    reason=f"验收驳回（{score}/5）：{task.title}", kind="rejection")
                self.ledger.record_step(assignee)
            else:
                dequeue(self.store, assignee, task_id)
                # 复盘回写：评语为空或低分时自动生成教训（有 LLM 走 AI 复盘，
                # 否则模板降级），下次执行经 render_experience 注入生效
                if emp:
                    review = self._learn(assignee, emp, task, score, comment)
                # 记账：完成（含交付落档的成本/耗时）+ 奖励积分
                delivery = next((p for p in reversed(task.progress_log)
                                 if p.get("deliverable")), {})
                cost = float(delivery.get("cost", 0.0) or 0.0)
                elapsed = float(delivery.get("elapsed", 0.0) or 0.0)
                # 时效判定：完成时间（状态机落 updated_at）vs 截止时间
                timing_pts = on_time_points(task.due_at, task.updated_at)
                on_time = None if timing_pts is None else timing_pts > 0
                self.ledger.record_completion(assignee, task_id=task_id,
                                               cost=cost, elapsed=elapsed,
                                               score=score, on_time=on_time)
                pts = points_for_acceptance(score)
                reason = f"验收通过（{score}/5）：{task.title}"
                kind = "acceptance"
                if score <= LOW_SCORE:
                    # 超限强制结案：仍记驳回账 + 扣分（与复盘阈值一致）
                    self.ledger.record_rejection(assignee)
                    pts = -PENALTY_REJECTION
                    reason = f"验收驳回（{score}/5）：{task.title}"
                    kind = "rejection"
                elif timing_pts is not None:
                    # 时效奖惩并入本次积分（通过才奖；驳回已经扣足）
                    pts += timing_pts
                    tag = "按时" if timing_pts > 0 else "超时"
                    reason += f"（{tag}）"
                self.ledger.record_points(assignee, pts, reason=reason, kind=kind)
                self.ledger.record_step(assignee)
                # 晋升通道（积分入账后判定，本次验收即时生效）：
                # AI 升自主等级 / 人类年度评估升管理权限（老板审批）
                if emp:
                    from ..core.promotion import maybe_request_promotion
                    promotion = maybe_request_promotion(
                        self.store, emp,
                        role=rbac.role_of(self.store, emp),
                        ledger=self.ledger)
        if rework:
            message = (f"已驳回返工（评分 {score}/5）：任务回到 {assignee} 队列重做"
                       f"（第 {task.review_round}/{MAX_REVIEW_ROUNDS} 轮）")
        else:
            timing_note = "" if on_time is None else ("（按时完成）" if on_time
                                                      else "（超时完成）")
            message = f"任务已完成（评分 {score}/5）{timing_note}"
        return self._json({"id": task.id, "state": task.state,
                           "assignee": assignee,
                           "message": message,
                           "review": review,
                           "promotion": promotion,
                           "points": self.ledger.points(assignee) if assignee else None})

    def _op_retry(self, role: str, me, body: dict):
        """死单复活：BLOCKED → ASSIGNED 重试（复用返工轮次上限）。

        执行失败不等于任务作废：老板一键重试，任务重入承接人队列
        由 worker 再跑（AI）或本人再做（人类）。超限任务彻底作废，
        需重新提交。
        """
        from ..core.task import BLOCKED
        if role == rbac.STAFF:
            return self._error(403, "员工不可重试任务（找老板或部门负责人）")
        task_id = str(body.get("id", "")).strip()
        if not task_id:
            return self._error(400, "缺少 id")
        task = self.store.load_task(task_id)
        if not task:
            return self._error(404, f"任务不存在：{task_id}")
        if task.state != BLOCKED:
            return self._error(409, f"当前状态 {task.state} 不可重试（需 blocked）")
        assignee = task.assignee
        if not assignee:
            return self._error(409, "任务未指派承接人，无法重试")
        emp = self.store.load_employee(assignee)
        if not emp:
            return self._error(404, f"承接人不存在：{assignee}")
        if emp.status != "active":
            return self._error(409, f"承接人 {assignee} 非在职，先复工或改派他人")
        if role == rbac.MANAGER and assignee not in rbac.dept_members(self.store, me):
            return self._error(403, "只能重试本部门成员的任务")
        actor = self._actor(me)
        try:
            advance(task, ASSIGNED, actor=actor,
                    remark=f"死单复活（重试，原因已清）")
        except IllegalTransition as e:
            return self._error(409, str(e))
        task.block_reason = ""
        self.store.save_task(task)
        enqueue(self.store, assignee, task_id)
        return self._json({"id": task.id, "state": task.state,
                           "assignee": assignee,
                           "message": f"已重试：任务重入 {assignee} 队列"
                                      f"（第 {task.review_round}/{MAX_REVIEW_ROUNDS} 轮）"})

    def _op_report(self, role: str, me, body: dict):
        """人类员工汇报交付：ASSIGNED → DOING → REPORTING（等验收）。

        AI 任务由 worker 自动执行，不走此口；人类任务由本人汇报
        （manager 可代报本部门成员，admin 任意）。成本按派单 → 汇报的
        耗时折算（时薪口径），验收时入账。
        """
        task_id = str(body.get("id", "")).strip()
        deliverable = str(body.get("deliverable", "")).strip()
        if not task_id:
            return self._error(400, "缺少 id")
        if not deliverable:
            return self._error(400, "缺少 deliverable（交付说明）")
        task = self.store.load_task(task_id)
        if not task:
            return self._error(404, f"任务不存在：{task_id}")
        if task.state != ASSIGNED:
            return self._error(409, f"当前状态 {task.state} 不可汇报（需 assigned）")
        assignee = task.assignee
        if not assignee:
            return self._error(409, "任务未指派承接人，先派单")
        emp = self.store.load_employee(assignee)
        if not emp:
            return self._error(404, f"承接人不存在：{assignee}")
        if emp.kind == "ai":
            return self._error(409, "AI 任务由系统自动执行，无需人工汇报")
        # 权限：本人汇报；manager 可代报本部门（含自己）；admin 任意
        if role == rbac.STAFF and (me is None or assignee != me.id):
            return self._error(403, "只能汇报自己的任务")
        if role == rbac.MANAGER and me is not None \
                and assignee not in rbac.dept_members(self.store, me):
            return self._error(403, "只能代报本部门成员的任务")

        actor = self._actor(me)
        elapsed = _elapsed_since_assigned(task)
        advance(task, DOING, actor=actor, remark="开工（人类汇报）")
        advance(task, REPORTING, actor=actor,
                remark=f"交付（人类汇报，{len(deliverable)} 字）")
        from ..core.points import accept_cost
        task.progress_log.append({
            "deliverable": deliverable,
            "by": assignee,
            "at": task.updated_at,
            "elapsed": round(elapsed, 1),
            "usage_tokens": 0,
            "cost": round(accept_cost(emp, elapsed_sec=elapsed), 6),
        })
        self.store.save_task(task)
        self.ledger.record_step(assignee)
        return self._json({"id": task.id, "state": task.state,
                           "assignee": assignee,
                           "message": "已汇报交付，待验收（评分入账后完成）"})

    def _op_approve(self, role: str, me, body: dict):
        """审批决策：仅 admin。落审批日志 + 账本记人类介入。"""
        if role != rbac.ADMIN:
            return self._error(403, "仅管理员可审批")
        log_id = str(body.get("id", "")).strip()
        if not log_id:
            return self._error(400, "缺少 id")
        approved = bool(body.get("approved", False))
        opinion = str(body.get("opinion", "")).strip()
        log = ApprovalLog(self.store)
        try:
            entry = next(e for e in log.list_logs()
                         if e.id == log_id and e.request.get("status") == "pending")
        except StopIteration:
            return self._error(404, f"待审批单不存在或已处理：{log_id}")
        log.log_decision(log_id, approver=self._actor(me),
                         approved=approved, opinion=opinion)
        # 晋升申请通过 → 立即生效（AI 升自主等级 / 人类升管理权限）
        message = "已通过" if approved else "已驳回"
        if approved and entry.request.get("type") == "晋升申请":
            from ..core.promotion import apply_promotion
            result = apply_promotion(self.store, entry.request)
            if result:
                suffix = ("（低风险操作免审批）"
                          if "autonomy_level" in result else "")
                message = f"已通过：{result['message']}{suffix}"
        requester = entry.request.get("requester", "")
        if requester:
            self.ledger.record_human_intervention(requester, "approval")
            self.ledger.record_step(requester)
        return self._json({"id": log_id,
                           "status": "approved" if approved else "rejected",
                           "message": message})

    def _op_headcount_submit(self, role: str, me, body: dict):
        """提交编制申请：manager/admin（staff 无部门管理权，不可提）。"""
        if role == rbac.STAFF:
            return self._error(403, "仅部门负责人及以上可提交编制申请")
        from ..recruitment import submit_headcount_request, HIRE_TYPES
        reason = str(body.get("reason", "")).strip()
        if not reason:
            return self._error(400, "缺少 reason（申请理由）")
        hire_type = str(body.get("hire_type", "new_ai")).strip()
        if hire_type not in HIRE_TYPES:
            return self._error(400, f"hire_type 必须是 {HIRE_TYPES} 之一")
        try:
            headcount = int(body.get("headcount", 1))
            cost = float(body.get("cost", 0.0) or 0.0)
        except (TypeError, ValueError):
            return self._error(400, "headcount/cost 必须是数字")
        req = submit_headcount_request(
            self.store, requester=self._actor(me), reason=reason,
            headcount=headcount, role=str(body.get("role", "")).strip(),
            cost=cost, hire_type=hire_type,
            department=str(body.get("department", "")).strip(),
            source_emp_id=str(body.get("source_emp_id", "")).strip())
        return self._json({"id": req["id"], "status": "pending",
                           "message": f"编制申请已提交：{req['id']}（等老板审批）"})

    def _op_headcount_decide(self, role: str, me, body: dict):
        """编制申请决策：仅 admin。通过即入职（HR 自动执行），驳回记理由。"""
        if role != rbac.ADMIN:
            return self._error(403, "仅管理员可决策编制申请")
        from ..recruitment import approve_headcount, reject_headcount
        req_id = str(body.get("id", "")).strip()
        if not req_id:
            return self._error(400, "缺少 id")
        approved = bool(body.get("approved", False))
        reason = str(body.get("reason", "")).strip()
        if approved:
            emp = approve_headcount(self.store, req_id,
                                    approver=self._actor(me))
            return self._json({
                "id": req_id, "status": "approved", "hired_emp_id": emp.id,
                "message": f"已通过并完成入职：{emp.name}（{emp.id}）"})
        req = reject_headcount(self.store, req_id, approver=self._actor(me),
                               reason=reason)
        return self._json({"id": req_id, "status": "rejected",
                           "message": f"已驳回{('：' + reason) if reason else ''}"})

    def _report(self, role: str, me) -> list[dict]:
        """部门级复盘报告：绩效 + 教训沉淀（按部门聚合）。

        可见范围与绩效面板一致：admin 全公司；manager 本部门；staff 仅本人。
        """
        stats_all = self.ledger.stats_all()
        depts: dict[str, dict] = {}
        for e in self.store.list_employees():
            if role == rbac.MANAGER and me is not None \
                    and e.department != me.department:
                continue
            if role == rbac.STAFF and (me is None or e.id != me.id):
                continue
            st = stats_all.get(e.id, {})
            exps = e.memory.get("experiences", [])
            lessons = [x for x in exps if x.get("outcome") == "failure"]
            wins = [x for x in exps if x.get("outcome") != "failure"]
            d = depts.setdefault(e.department or "（未分配）", {
                "department": e.department or "（未分配）",
                "completion_count": 0, "lessons": 0, "auto_reviews": 0,
                "rejections": 0, "members": [],
            })
            d["completion_count"] += st.get("completion_count", 0)
            d["lessons"] += len(lessons)
            d["auto_reviews"] += sum(1 for x in exps if x.get("auto"))
            d["rejections"] += st.get("rejection_count", 0)
            d["members"].append({
                "id": e.id, "name": e.name, "kind": e.kind,
                "role": rbac.role_of(self.store, e),
                "completion_count": st.get("completion_count", 0),
                "lessons": len(lessons), "wins": len(wins),
                "autonomy_level": e.permissions.get("autonomy_level", "supervised"),
                "latest_lesson": (lessons[-1].get("learned", "") if lessons else ""),
            })
        return sorted(depts.values(), key=lambda d: d["department"])

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
                                   "kind": emp.kind, "title": emp.title,
                                   "department": emp.department,
                                   "role": rbac.role_of(self.store, emp)})
            return self._json({"id": "", "name": "免鉴权模式",
                               "kind": "", "title": "未设口令，无需登录",
                               "role": rbac.ADMIN})

        # ---- 以下数据接口统一走视图权限（HTML 页面本身免登录，否则登录页打不开）----
        if u.path.startswith("/api/"):
            denied, (role, me) = self._require_view()
            if denied:
                return self._error(denied[0], denied[1])
        else:
            role, me = self._view()

        if u.path == "/api/tasks":
            return self._json([t.to_dict() for t in
                               rbac.visible_tasks(self.store, me, role)])
        if u.path == "/api/employees":
            return self._json(rbac.visible_employees(self.store, me, role))
        if u.path == "/api/org":
            return self._json(self._org(role, me))
        if u.path == "/api/human-tasks":
            q = parse_qs(u.query)
            who = q.get("who", [""])[0]
            date = q.get("date", [datetime.date.today().isoformat()])[0]
            if not rbac.can_view_human_tasks(self.store, me, role, who):
                return self._error(403, "只能查看本人（或你管理部门成员）的待办")
            inbox = HumanInbox(self.store)
            return self._json([ht.to_dict() for ht in inbox.daily_list(assignee=who, date=date)])
        if u.path == "/api/human-results":
            # 人→人闭环：查看发起人收到的回传结果
            q = parse_qs(u.query)
            who = q.get("who", [""])[0]
            if not rbac.can_view_results(self.store, me, role, who):
                return self._error(403, "只能查看自己发起的回传结果")
            inbox = HumanInbox(self.store)
            return self._json([ht.to_dict() for ht in inbox.results_for(who)])
        if u.path == "/api/messages":
            who = self._who_required(u)
            if who is None:
                return
            if not rbac.can_view_messages(self.store, me, role, who):
                return self._error(403, "只能查看自己的收发件箱")
            return self._json({
                "inbox": msg_inbox(self.store, who),
                "sent": msg_sent(self.store, who),
            })
        if u.path == "/api/queue":
            who = self._who_required(u)
            if who is None:
                return
            if not rbac.can_view_queue(self.store, me, role, who):
                return self._error(403, "只能查看本人（或你管理部门成员）的队列")
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
        if u.path == "/api/plans":
            # 个人任务计划：按周期（日/周/月/季/半年/年）分组，含被动分配/个人计划
            # 与完成情况。可见范围：admin 任何人（缺省全公司汇总）；manager 本部门；
            # staff 仅本人。
            q = parse_qs(u.query)
            who = q.get("who", [""])[0]
            if role == rbac.STAFF:
                who = me.id if me is not None else who
            elif who and not rbac._can_view_dept_scoped(self.store, me, role, who):
                return self._error(403, "只能查看本人（或你管理部门成员）的计划")
            plans = _plans_view(self.store, who=who, role=role, me=me)
            return self._json(plans)
        if u.path == "/api/approvals":
            # 审批单：admin 全部；manager/staff 仅自己发起的
            status = parse_qs(u.query).get("status", [""])[0]
            logs = ApprovalLog(self.store).list_logs(status=status)
            if role != rbac.ADMIN and me is not None:
                logs = [e for e in logs if e.request.get("requester") == me.id]
            return self._json([{
                "id": e.id, "type": e.request.get("type", ""),
                "risk": e.request.get("risk", ""),
                "requester": e.request.get("requester", ""),
                "summary": e.request.get("summary", ""),
                "status": e.request.get("status", "pending"),
                "approver": e.approver, "opinion": e.opinion,
            } for e in logs])
        if u.path == "/api/headcount":
            # 编制申请：admin 全部；manager/staff 仅自己提交的
            from ..recruitment import list_requests
            reqs = list_requests(self.store)
            if role != rbac.ADMIN and me is not None:
                reqs = [r for r in reqs if r.get("requester") == me.id]
            return self._json(reqs)
        if u.path == "/api/report":
            return self._json(self._report(role, me))
        if u.path == "/api/points":
            # 积分/ROI 榜：可见范围同绩效面板（admin 全量 / manager 本部门 / staff 本人）
            from ..core.points import leaderboard
            board = leaderboard(self.store, self.ledger)
            if role == rbac.MANAGER and me is not None:
                members = rbac.dept_members(self.store, me)
                for k in ("ai", "human", "roi"):
                    board[k] = [r for r in board[k] if r["id"] in members
                                or r["id"] == me.id]
            elif role == rbac.STAFF:
                for k in ("ai", "human", "roi"):
                    board[k] = [r for r in board[k]
                                if me is not None and r["id"] == me.id]
            # 财务报告汇总（对可见范围口径计算：总积分/总成本/整体 ROI）
            rows = board["ai"] + board["human"]
            total_pts = round(sum(r["points"] for r in rows), 2)
            total_cost = round(sum(r["total_cost"] for r in rows), 4)
            board["summary"] = {
                "total_points": total_pts,
                "total_cost": total_cost,
                "roi": round(total_pts / total_cost, 2) if total_cost > 0 else None,
                "scope": "全公司" if role == rbac.ADMIN else
                         (me.department if me is not None and role == rbac.MANAGER
                          else (me.name if me is not None else "")),
            }
            return self._json(board)
        if u.path == "/api/finance":
            # CFO 周报（归档读取）：财务数据敏感，仅老板（admin）可见。
            # 返回 current=最新一期（含环比 compare 与 budget_advice），
            # history=各期公司级摘要（升序）。
            from ..core.finance import load_reports
            if role != rbac.ADMIN:
                return self._error(403, "财务周报仅老板可见")
            reports = load_reports(self.store)
            history = [{
                "key": r.get("period", {}).get("key", ""),
                "points": r.get("company", {}).get("points", 0),
                "cost": r.get("company", {}).get("cost", 0),
                "roi": r.get("company", {}).get("roi"),
                "completion_count": r.get("company", {}).get("completion_count", 0),
            } for r in reports]
            return self._json({
                "current": reports[-1] if reports else None,
                "history": history,
            })
        if u.path == "/api/perf":
            # 绩效面板：admin 全公司；manager 本部门；staff 仅本人
            stats_all = self.ledger.stats_all()
            if role == rbac.ADMIN:
                visible = set(stats_all)
            elif me is not None:
                allowed = rbac.dept_members(self.store, me) if role == rbac.MANAGER else {me.id}
                visible = set(stats_all) & allowed
            else:
                visible = set(stats_all)
            return self._json({
                emp_id: stats_all[emp_id] for emp_id in sorted(visible)})
        # 默认返回看板 HTML
        html = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _org(self, role: str = "", me=None) -> list[dict]:
        """组织架构视图：员工按部门分组（AI 与人类同部门）。

        可见员工与字段脱敏同花名册口径：staff 仅本部门，manager 跨部门脱敏，
        admin 全量。
        """
        me = me or Employee(id="", name="")
        role = role or rbac.ADMIN
        members = rbac.dept_members(self.store, me) if me.id else set()
        departments: dict[str, dict] = {}
        for e in self.store.list_employees():
            if role == rbac.MANAGER:
                d = e.to_dict()
            elif role == rbac.STAFF and e.id not in members:
                continue
            else:
                d = e.to_dict()
            if role != rbac.ADMIN:
                full = (e.id == me.id) or (role == rbac.MANAGER
                                           and e.department == me.department)
                d = rbac.mask_employee(d, full)
            dept_id = e.department or "（未分配）"
            g = departments.setdefault(dept_id, {"id": dept_id, "employees": []})
            g["employees"].append({
                "id": d["id"], "name": d["name"], "kind": d["kind"],
                "title": d["title"], "status": d["status"],
                "queue": d.get("workspace", {}).get("queue", []),
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
            "ledger": FileLedger(store),   # 持久化绩效账本（验收/审批记账）
        })
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.port = self.httpd.server_address[1]

    def serve_forever(self):
        self.httpd.serve_forever()

    def shutdown(self):
        self.httpd.shutdown()
