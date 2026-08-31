"""UI 重设计专项验证：起看板 → 断言账房美学元素真实渲染 + 零 JS 报错 + 截图。"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = tempfile.mkdtemp(prefix="laoban-ui-check-")
PORT = 7931
LLM_PORT = 18999

ORG = {
    "company": "界面演示公司",
    "business": "ui",
    "departments": [
        {"id": "dev_dept", "name": "研发部", "roles": [
            {"id": "dev", "kind": "ai", "name": "阿码", "title": "开发工程师"},
        ]},
        {"id": "hq", "name": "总办", "roles": [
            {"id": "boss", "name": "老板", "kind": "human", "title": "CEO",
             "permissions": {"role": "admin"}},
        ]},
    ],
}


class _H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        if n:
            self.rfile.read(n)
        body = json.dumps({"choices": [{"message": {"content": "界面验证交付完成。"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _wait_http(url, timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main() -> int:
    llm = ThreadingHTTPServer(("127.0.0.1", LLM_PORT), _H)
    threading.Thread(target=llm.serve_forever, daemon=True).start()
    env = dict(os.environ, LAOBAN_OLLAMA_BASE_URL=f"http://127.0.0.1:{LLM_PORT}/v1",
               PYTHONIOENCODING="utf-8")
    with open(os.path.join(ROOT, "org.json"), "w", encoding="utf-8") as f:
        json.dump(ORG, f, ensure_ascii=False, indent=2)
    for args in (["init", "--root", ROOT], ["org", "load", "--root", ROOT],
                 ["auth", "passwd", "--root", ROOT, "--who", "boss", "--password", "pw"]):
        r = subprocess.run([sys.executable, "-m", "laoban", *args],
                           capture_output=True, text=True, env=env, cwd="/workspace")
        if r.returncode != 0:
            print("CLI 失败：", args, r.stderr)
            return 1
    dash = subprocess.Popen(
        [sys.executable, "-m", "laoban", "dashboard", "--root", ROOT,
         "--port", str(PORT), "--worker-interval", "1.0"],
        env=env, cwd="/workspace", stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)
    try:
        if not _wait_http(f"http://127.0.0.1:{PORT}/"):
            print("看板未启动")
            return 1
        return _check()
    finally:
        dash.terminate()
        try:
            dash.wait(timeout=5)
        except subprocess.TimeoutExpired:
            dash.kill()
        llm.shutdown()
        import shutil
        shutil.rmtree(ROOT, ignore_errors=True)


def _check() -> int:
    from playwright.sync_api import sync_playwright
    ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text)
                if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.goto(f"http://127.0.0.1:{PORT}/")
        page.wait_for_load_state("networkidle")

        def assert_(name, cond):
            nonlocal ok
            print(("[OK] " if cond else "[FAIL] ") + name)
            if not cond:
                ok = False

        assert_("顶栏（墨条 + 印章品牌）", page.locator("header.topbar .brand").count() == 1)
        assert_("锚点导航 10 项", page.locator("nav.anchors a").count() == 10)
        assert_("报头经营台账 + 账历时钟", page.locator(".masthead h1").inner_text() == "经营台账"
                and "周期" in page.locator("#ledgerClock").inner_text())
        assert_("账页卡 17 区", page.locator("section.sheet").count() == 17)
        bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        assert_(f"纸色背景（实际 {bg}）", bg != "rgb(250, 250, 250)")
        assert_("表格账线（th 上下墨线）", page.evaluate(
            "getComputedStyle(document.querySelector('#tasks thead th')).borderTopStyle") == "solid")
        assert_("等宽数字表格", page.evaluate(
            "getComputedStyle(document.querySelector('#tasks')).fontVariantNumeric") == "tabular-nums")
        assert_("次级按钮墨底（派发）", page.evaluate(
            "getComputedStyle(document.querySelector('#assignRow button')).backgroundColor") == "rgb(35, 32, 26)")
        assert_("朱砂按钮（提交任务）", page.evaluate(
            "getComputedStyle(document.querySelector('#cockpit button.vermilion')).backgroundColor") == "rgb(178, 58, 38)")
        assert_("页面载入动画已应用", page.evaluate(
            "!!getComputedStyle(document.querySelector('section.sheet')).animationName"))

        # 登录 + 走一单，验证操作反馈（朱批条）与数据渲染
        page.fill("#loginId", "boss")
        page.fill("#loginPw", "pw")
        page.click("#loginBar button")
        page.wait_for_timeout(500)
        page.fill("#newTaskTitle", "界面走查任务")
        page.click("button:has-text('提交任务')")
        page.wait_for_timeout(400)
        tid = page.input_value("#assignId")
        page.fill("#assignTo", "dev")
        page.click("button:has-text('派发')")
        page.wait_for_timeout(400)
        assert_("朱批反馈条渲染", "已派发给 dev" in page.locator("#opMsg").inner_text())
        time.sleep(3)   # worker 自动执行
        page.reload()
        page.wait_for_load_state("networkidle")
        page.fill("#loginId", "boss")
        page.fill("#loginPw", "pw")
        page.click("#loginBar button")
        page.wait_for_timeout(600)
        page.fill("#acceptId", tid)
        page.select_option("#acceptScore", "5")
        page.click("button:has-text('验收')")
        page.wait_for_timeout(800)
        assert_("验收朱批含积分入账", "积分入账" in page.locator("#opMsg").inner_text())
        assert_("花名册徽章渲染（AI）", page.locator("#employees .kind.ai").count() >= 1)
        assert_("AI 印章徽章靛青", page.evaluate(
            "getComputedStyle(document.querySelector('#employees .kind.ai')).color") == "rgb(47, 77, 107)")
        page.screenshot(path="/workspace/scripts/ui_redesign_top.png")
        page.screenshot(path="/workspace/scripts/ui_redesign_full.png", full_page=True)
        assert_("零 JS 报错", not errors)
        if errors:
            print("  报错明细：", errors[:5])
        browser.close()
    print("[DONE] 截图：scripts/ui_redesign_top.png / ui_redesign_full.png")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
