"""鉴权端到端验证：CLI 设口令 → 看板登录 → 身份锁定 → 聊天 → 登出。

验证路径与生产完全一致（不走测试桩）：
  1. CLI 建公司 + org load 入职员工；
  2. `laoban auth passwd` 设口令（PBKDF2 落盘，auth.json 不含明文）；
  3. 本地起 OpenAI 兼容假 LLM 服务；
  4. `python -m laoban dashboard` 启动看板（鉴权启用）；
  5. Playwright 驱动：未登录聊天被 401 拒 → 登录 → 身份锁定 → 聊天闭环 →
     伪造他人身份 403 → 登出恢复。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = tempfile.mkdtemp(prefix="laoban-dash-auth-")
DASH_PORT = 7894
LLM_PORT = 18999
PW = "pw-chen-e2e"
FAKE_REPLY = "已登录鉴权回信 AUTH-FAKE-LLM-OK。"


class _FakeLLMHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        if n:
            self.rfile.read(n)
        body = json.dumps({
            "choices": [{"message": {"content": FAKE_REPLY}}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _wait_http(url: str, timeout: float = 15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main() -> int:
    llm = ThreadingHTTPServer(("127.0.0.1", LLM_PORT), _FakeLLMHandler)
    threading.Thread(target=llm.serve_forever, daemon=True).start()

    env = dict(os.environ, LAOBAN_OLLAMA_BASE_URL=f"http://127.0.0.1:{LLM_PORT}/v1",
               PYTHONIOENCODING="utf-8")
    py = sys.executable
    steps = [
        [py, "-m", "laoban", "init", "--root", ROOT],
        [py, "-m", "laoban", "org", "load", "--root", ROOT],
        [py, "-m", "laoban", "auth", "passwd", "--root", ROOT,
         "--who", "emp-chen", "--password", PW],
        [py, "-m", "laoban", "auth", "list", "--root", ROOT],
    ]
    for cmd in steps:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd="/workspace")
        if r.returncode != 0:
            print("命令失败：", " ".join(cmd[2:]))
            print(r.stdout, r.stderr)
            return 1

    # 口令库落盘检查：PBKDF2 存盐+哈希，绝不含明文口令
    auth_file = os.path.join(ROOT, "auth.json")
    with open(auth_file, encoding="utf-8") as f:
        raw = f.read()
    if PW not in raw and "salt" in raw and "hash" in raw and "iterations" in raw:
        print("[OK] auth.json 只存 PBKDF2 盐+哈希，无明文口令")
    else:
        print("[FAIL] auth.json 内容异常（含明文或缺字段）")
        return 1

    dash = subprocess.Popen(
        [py, "-m", "laoban", "dashboard", "--root", ROOT, "--port", str(DASH_PORT)],
        env=env, cwd="/workspace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True)
    try:
        if not _wait_http(f"http://127.0.0.1:{DASH_PORT}/"):
            print("看板未在超时内启动")
            return 1
        return _playwright_check()
    finally:
        dash.terminate()
        try:
            dash.wait(timeout=5)
        except subprocess.TimeoutExpired:
            dash.kill()
        llm.shutdown()
        print(f"\n看板启动日志：\n{dash.stdout.read()[:600] if dash.stdout else ''}")
        shutil.rmtree(ROOT, ignore_errors=True)


def _playwright_check() -> int:
    from playwright.sync_api import sync_playwright

    ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{DASH_PORT}/")
        page.wait_for_load_state("networkidle")

        # 1. 未登录：/api/chat 被 401 拒（鉴权已启用）
        status = page.evaluate("""async () => {
            const r = await fetch('/api/chat', {
              method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({from: 'emp-chen', to: 'dev', content: 'hi'})});
            return r.status;
        }""")
        if status == 401:
            print("[OK] 未登录聊天被拒（401），鉴权已启用")
        else:
            print(f"[FAIL] 未登录聊天未被拒：HTTP {status}")
            ok = False

        # 2. 错口令登录失败
        page.fill("#loginId", "emp-chen")
        page.fill("#loginPw", "wrong-password")
        page.click("#loginBar button")
        page.wait_for_timeout(300)
        if "已登录" not in (page.locator("#meInfo").inner_text() or ""):
            print("[OK] 错误口令登录失败")
        else:
            print("[FAIL] 错误口令竟然登录成功")
            ok = False

        # 3. 正确口令登录 → 身份显示 + chatFrom 锁定
        page.fill("#loginPw", PW)
        page.click("#loginBar button")
        page.wait_for_timeout(300)
        me = page.locator("#meInfo").inner_text() or ""
        if "已登录" in me and "emp-chen" in me:
            print("[OK] 登录成功：身份栏显示 emp-chen")
        else:
            print(f"[FAIL] 登录后身份栏异常：{me!r}")
            ok = False
        if page.locator("#chatFrom").is_disabled() and \
                page.input_value("#chatFrom") == "emp-chen":
            print("[OK] 登录后 chatFrom 锁定为本人身份（防手改冒充）")
        else:
            print("[FAIL] chatFrom 未锁定")
            ok = False

        # 4. 以本人身份聊天 → AI 回信（真实 LLM 路径）
        page.fill("#chatTo", "dev")
        page.fill("#chatContent", "登录后问一句：在吗？")
        page.press("#chatContent", "Enter")
        page.wait_for_selector(".chat-line.ai", timeout=10000)
        if "AUTH-FAKE-LLM-OK" in page.locator(".chat-line.ai").last.inner_text():
            print("[OK] 登录后人↔AI 聊天闭环（真实网关回信）")
        else:
            print(f"[FAIL] 登录后聊天异常：{page.locator('.chat-line.ai').last.inner_text()!r}")
            ok = False

        # 5. 伪造他人身份（绕过 UI 直接 fetch）→ 403
        status = page.evaluate("""async () => {
            const r = await fetch('/api/chat', {
              method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({from: 'emp-xiaoli', to: 'dev', content: '冒充'})});
            return r.status;
        }""")
        if status == 403:
            print("[OK] 冒充他人身份被拒（403）")
        else:
            print(f"[FAIL] 冒充未被拒：HTTP {status}")
            ok = False

        # 6. 登出 → 身份清空、chatFrom 解锁
        page.click("#meInfo a")
        page.wait_for_timeout(300)
        me = page.locator("#meInfo").inner_text() or ""
        if not me and not page.locator("#chatFrom").is_disabled():
            print("[OK] 登出：身份清空，chatFrom 恢复可编辑")
        else:
            print(f"[FAIL] 登出异常：me={me!r}")
            ok = False

        page.screenshot(path="/workspace/scripts/dashboard_auth_preview.png", full_page=True)
        browser.close()

    print("[DONE] 截图：scripts/dashboard_auth_preview.png")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
