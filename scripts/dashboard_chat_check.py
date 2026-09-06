"""D-4 浏览器验证：CLI dashboard 接 LLM 网关后，人↔AI 聊天闭环。

验证路径与生产完全一致（不走测试桩）：
  1. CLI 建公司 + org load 入职员工；
  2. 本地起 OpenAI 兼容假 LLM 服务（LAOBAN_OLLAMA_BASE_URL 自动发现 → provider "ollama"）；
  3. `python -m laoban dashboard` 启动看板（真实 CLI 路径 + 员工路由别名）；
  4. Playwright 驱动聊天框：Enter 发送 → AI 回信 → 消息总线落库 → 人收投递提示。
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

ROOT = tempfile.mkdtemp(prefix="laoban-dash-chat-")
DASH_PORT = 7893
LLM_PORT = 18998
FAKE_REPLY = "数据样本在共享盘 /data/v2 目录，FAKE-LLM-OK。"

llm_calls: list[dict] = []   # 记录假 LLM 收到的请求（用于断言调用次数）


class _FakeLLMHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(n)) if n else {}
        llm_calls.append(payload)
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
    # 1. 假 LLM 服务
    llm = ThreadingHTTPServer(("127.0.0.1", LLM_PORT), _FakeLLMHandler)
    threading.Thread(target=llm.serve_forever, daemon=True).start()

    # 2. CLI 建公司 + 入职
    env = dict(os.environ, LAOBAN_OLLAMA_BASE_URL=f"http://127.0.0.1:{LLM_PORT}/v1",
               PYTHONIOENCODING="utf-8")
    py = sys.executable
    for cmd in ([py, "-m", "laoban", "init", "--root", ROOT],
                [py, "-m", "laoban", "org", "load", "--root", ROOT]):
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd="/workspace")
        if r.returncode != 0:
            print(r.stdout, r.stderr)
            return 1

    # 3. 真实 CLI 启动看板
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
        print(f"\n看板启动日志：\n{dash.stdout.read()[:800] if dash.stdout else ''}")
        shutil.rmtree(ROOT, ignore_errors=True)


def _playwright_check() -> int:
    from playwright.sync_api import sync_playwright

    ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{DASH_PORT}/")
        page.wait_for_load_state("networkidle")

        # 0. 聊天区渲染 + sendChat 函数存在（D-3 前端）
        assert page.locator("#chatLog").count() == 1
        has_fn = page.evaluate("typeof sendChat")
        if has_fn != "function":
            print(f"[FAIL] sendChat 未定义：{has_fn}")
            ok = False
        else:
            print("[OK] 聊天 UI 渲染，sendChat() 已定义")

        # 1. 人 → AI：Enter 发送，AI 回信
        page.fill("#chatFrom", "emp-chen")
        page.fill("#chatTo", "dev")
        page.fill("#chatContent", "数据样本放哪了？")
        page.press("#chatContent", "Enter")
        page.wait_for_selector(".chat-line.ai", timeout=10000)
        reply_text = page.locator(".chat-line.ai").inner_text()
        if "FAKE-LLM-OK" in reply_text and "共享盘" in reply_text:
            print("[OK] 人→AI 聊天：Enter 发送，AI 回信渲染在对话流")
        else:
            print(f"[FAIL] AI 回信异常：{reply_text!r}")
            ok = False
        if page.locator(".chat-line.me").count() == 1 and \
                "数据样本放哪了？" in page.locator(".chat-line.me").inner_text():
            print("[OK] 提问气泡渲染在对话流（me）")
        else:
            print("[FAIL] 提问气泡缺失")
            ok = False
        if page.locator(".chat-line.busy").count() == 0:
            print("[OK] 思考中占位已清除")
        else:
            print("[FAIL] 思考中占位未清除")
            ok = False

        # 2. 消息总线落库（双向）
        bus = page.evaluate("""async () => {
            const dev = await (await fetch('/api/messages?who=dev')).json();
            const chen = await (await fetch('/api/messages?who=emp-chen')).json();
            return {dev, chen};
        }""")
        if any("数据样本放哪了？" in m["content"] for m in bus["dev"]["inbox"]) and \
                any(FAKE_REPLY in m["content"] for m in bus["chen"]["inbox"]):
            print("[OK] 消息总线双向落库：提问入 dev 收件箱，回信入陈工收件箱")
        else:
            print("[FAIL] 消息总线落库异常")
            ok = False

        # 3. 人 → 人：只投递不触发 LLM
        before = len(llm_calls)
        page.fill("#chatFrom", "emp-chen")
        page.fill("#chatTo", "emp-xiaoli")
        page.fill("#chatContent", "请复核异常值清单")
        page.click("button:has-text('发送')")
        page.wait_for_timeout(800)
        note = page.locator(".chat-line.ai").last.inner_text()
        if "已投递" in note and len(llm_calls) == before:
            print("[OK] 人→人：只投递（收件人是人类，不触发 LLM）")
        else:
            print(f"[FAIL] 人→人投递异常：note={note!r} llm_calls={len(llm_calls)-before}")
            ok = False

        # 4. LLM 真实被调 1 次（走 ollama provider → 假服务）
        if len(llm_calls) == 1:
            print("[OK] LLM 网关路由：员工 provider 名统一路由到真实 LLM（调 1 次）")
        else:
            print(f"[FAIL] LLM 调用次数异常：{len(llm_calls)}")
            ok = False

        page.screenshot(path="/workspace/scripts/dashboard_chat_preview.png", full_page=True)
        browser.close()

    print("[DONE] 截图：scripts/dashboard_chat_preview.png")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
