"""IM-4 端到端验证：飞书事件 → 看板 webhook → 消息总线 + AI 回信 → 推回飞书。

验证路径与生产一致（全部走真实 CLI）：
  1. laoban init / org load / im bind（CLI 维护绑定表）；
  2. 本地假飞书服务（tenant_access_token + im/v1/messages 两个端点）+ 假 LLM；
  3. `python -m laoban dashboard` 启动（LAOBAN_FEISHU_* env 自动发现）；
  4. POST url_verification（飞书控制台配置回调时的握手）；
  5. POST 消息事件 → 后台线程回信 → 轮询假飞书服务收到的推送；
  6. 断言：token 获取走 Bearer、回信内容、消息总线双向落库、人→人中转。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = tempfile.mkdtemp(prefix="laoban-feishu-e2e-")
DASH_PORT, FEISHU_PORT, LLM_PORT = 7896, 18997, 18999
FAKE_REPLY = "数据在共享盘 /data/v2，FEISHU-E2E-OK。"

sent_messages: list[dict] = []     # 假飞书收到的 im/v1/messages 请求
token_requests: list[dict] = []    # 假飞书收到的 token 请求


class _FakeFeishuHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(n)) if n else {}
        if self.path == "/open-apis/auth/v3/tenant_access_token/internal":
            token_requests.append(payload)
            body = {"code": 0, "tenant_access_token": "t-xyz", "expire": 7200}
        elif self.path.startswith("/open-apis/im/v1/messages"):
            payload["auth"] = self.headers.get("Authorization", "")
            sent_messages.append(payload)
            body = {"code": 0, "data": {"message_id": "om_fake"}}
        else:
            body = {"code": 0}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class _FakeLLMHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        if n:
            self.rfile.read(n)
        data = json.dumps({"choices": [{"message": {"content": FAKE_REPLY}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def _post(url: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _wait_http(url: str, timeout: float = 15.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def _msg_event(open_id: str, text: str, event_id: str) -> dict:
    return {
        "schema": "2.0",
        "header": {"event_id": event_id, "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": open_id}},
            "message": {"chat_id": "oc_1", "message_id": "om_1",
                        "message_type": "text",
                        "content": json.dumps({"text": text})},
        },
    }


def main() -> int:
    feishu = ThreadingHTTPServer(("127.0.0.1", FEISHU_PORT), _FakeFeishuHandler)
    llm = ThreadingHTTPServer(("127.0.0.1", LLM_PORT), _FakeLLMHandler)
    for s in (feishu, llm):
        threading.Thread(target=s.serve_forever, daemon=True).start()

    py = sys.executable
    env = dict(
        os.environ,
        LAOBAN_FEISHU_APP_ID="cli_test_app",
        LAOBAN_FEISHU_APP_SECRET="cli_test_secret",
        LAOBAN_FEISHU_BASE_URL=f"http://127.0.0.1:{FEISHU_PORT}",
        LAOBAN_OLLAMA_BASE_URL=f"http://127.0.0.1:{LLM_PORT}/v1",
        PYTHONIOENCODING="utf-8")

    # 1. CLI 建公司 / 入职 / 绑定
    cmds = (
        ["-m", "laoban", "init", "--root", ROOT],
        ["-m", "laoban", "org", "load", "--root", ROOT],
        ["-m", "laoban", "im", "bind", "--root", ROOT, "--platform", "feishu",
         "--im-user", "ou_chen", "--employee", "emp-chen"],
        ["-m", "laoban", "im", "bind", "--root", ROOT, "--platform", "feishu",
         "--im-user", "ou_xiaoli", "--employee", "emp-xiaoli"],
        ["-m", "laoban", "im", "list", "--root", ROOT],
    )
    for c in cmds:
        r = subprocess.run([py] + c, capture_output=True, text=True, env=env,
                           cwd="/workspace")
        if r.returncode != 0:
            print("CLI 失败：", c, r.stdout, r.stderr)
            return 1
    print("[OK] CLI：init / org load / im bind × 2 / im list")

    # 2. 真实 CLI 启动看板（飞书 + LLM 均 env 自动发现）
    dash = subprocess.Popen(
        [py, "-m", "laoban", "dashboard", "--root", ROOT, "--port", str(DASH_PORT)],
        env=env, cwd="/workspace", stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)
    ok = True
    try:
        if not _wait_http(f"http://127.0.0.1:{DASH_PORT}/"):
            print("看板未在超时内启动")
            return 1
        base = f"http://127.0.0.1:{DASH_PORT}/api/im/webhook/feishu"

        # 3. url_verification（飞书控制台配置回调的握手）
        status, data = _post(base, {"type": "url_verification", "challenge": "c-e2e"})
        if status == 200 and data.get("challenge") == "c-e2e":
            print("[OK] url_verification 握手：challenge 原样返回")
        else:
            print(f"[FAIL] 握手失败：{status} {data}")
            ok = False

        # 4. 人 → AI：事件 → 后台回信 → 推回飞书
        status, _ = _post(base, _msg_event("ou_chen", "dev: 数据放哪了？", "evt-e2e-1"))
        if status != 200:
            print(f"[FAIL] 消息事件 HTTP {status}")
            ok = False
        deadline = time.time() + 15
        while time.time() < deadline and not sent_messages:
            time.sleep(0.2)
        if not sent_messages:
            print("[FAIL] 假飞书未收到回信推送")
            ok = False
        else:
            m = sent_messages[0]
            recv_ok = m.get("receive_id") == "ou_chen"
            content = json.loads(m.get("content", "{}")).get("text", "")
            auth_ok = m.get("auth") == "Bearer t-xyz"
            if recv_ok and "FEISHU-E2E-OK" in content:
                print("[OK] 人→AI：事件 ACK → 后台回信 → 飞书消息推回提问者")
            else:
                print(f"[FAIL] 推送内容异常：{m}")
                ok = False
            if auth_ok and token_requests:
                print("[OK] 出站认证：先取 tenant_access_token，再 Bearer 发消息")
            else:
                print(f"[FAIL] 认证异常：auth={m.get('auth')} token_req={len(token_requests)}")
                ok = False

        # 5. 人 → 人：消息总线中转 + 推送对方 IM
        status, _ = _post(base, _msg_event(
            "ou_chen", "emp-xiaoli: 请复核异常值清单", "evt-e2e-2"))
        deadline = time.time() + 10
        while time.time() < deadline and len(sent_messages) < 3:
            time.sleep(0.2)
        relay = [m for m in sent_messages
                 if m.get("receive_id") == "ou_xiaoli"]
        confirm = [m for m in sent_messages if m.get("receive_id") == "ou_chen"
                   and "已投递" in json.loads(m.get("content", "{}")).get("text", "")]
        if relay and confirm:
            print("[OK] 人→人：经消息总线中转，推送到对方 IM + 发送者确认")
        else:
            print(f"[FAIL] 人→人中转异常：relay={len(relay)} confirm={len(confirm)}")
            ok = False

        # 6. 消息总线双向落库（经 CLI inbox 查证）
        r = subprocess.run([py, "-m", "laoban", "msg", "inbox", "--root", ROOT,
                            "--who", "dev"], capture_output=True, text=True,
                           env=env, cwd="/workspace")
        r2 = subprocess.run([py, "-m", "laoban", "msg", "inbox", "--root", ROOT,
                             "--who", "emp-chen"], capture_output=True, text=True,
                            env=env, cwd="/workspace")
        if "数据放哪了？" in r.stdout and "FEISHU-E2E-OK" in r2.stdout:
            print("[OK] 消息总线落库：提问入 dev 收件箱，回信入 emp-chen 收件箱（CLI 可查）")
        else:
            print(f"[FAIL] 总线落库异常：dev 有提问={('数据放哪了？' in r.stdout)} "
                  f"chen 有回信={('FEISHU-E2E-OK' in r2.stdout)}")
            ok = False

        # 7. 重复事件去重
        n_before = len(sent_messages)
        _post(base, _msg_event("ou_chen", "dev: 数据放哪了？", "evt-e2e-1"))
        time.sleep(1.0)
        if len(sent_messages) == n_before:
            print("[OK] 事件去重：同一 event_id 重试不再触发回信")
        else:
            print(f"[FAIL] 去重失效：{n_before} → {len(sent_messages)}")
            ok = False

        return 0 if ok else 1
    finally:
        dash.terminate()
        try:
            dash.wait(timeout=5)
        except subprocess.TimeoutExpired:
            dash.kill()
        feishu.shutdown()
        llm.shutdown()
        log = dash.stdout.read() if dash.stdout else ""
        print(f"\n看板启动日志（前 400 字）：\n{log[:400]}")
        shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
