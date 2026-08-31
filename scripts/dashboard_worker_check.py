"""自动运转 e2e：派单后不敲任何命令，任务自动执行到 reporting，看板一键验收。

验证路径与生产一致：
  1. CLI 建公司 + 设口令；
  2. 本地起 OpenAI 兼容假 LLM（回固定交付文案）；
  3. `laoban dashboard` 启动（自动运转引擎随 LLM 启用）；
  4. Playwright：登录 boss → 提交任务 → 派给 dev → **不碰任何 CLI**，
     等任务自动 doing → reporting（交付物落档）→ 验收评分 → done + 绩效入账。
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

ROOT = tempfile.mkdtemp(prefix="laoban-worker-e2e-")
DASH_PORT = 7902
LLM_PORT = 18998
FAKE_REPLY = "e2e 自动交付：数据清洗完成，产出 sales_clean.csv（AUTO-RUN-OK）。"

ORG = {
    "company": "自动运转演示公司",
    "business": "e2e",
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


def _wait_task_state(task_id: str, want: str, timeout: float = 20.0) -> str:
    """轮询任务档案直到目标状态（验证 worker 自动执行）。"""
    path = os.path.join(ROOT, "tasks", f"{task_id}.json")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    if json.load(f).get("state") == want:
                        return want
            except (json.JSONDecodeError, OSError):
                pass
        time.sleep(0.3)
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("state", "?") if os.path.exists(path) else "?"


def main() -> int:
    llm = ThreadingHTTPServer(("127.0.0.1", LLM_PORT), _FakeLLMHandler)
    threading.Thread(target=llm.serve_forever, daemon=True).start()

    env = dict(os.environ, LAOBAN_OLLAMA_BASE_URL=f"http://127.0.0.1:{LLM_PORT}/v1",
               PYTHONIOENCODING="utf-8")
    with open(os.path.join(ROOT, "org.json"), "w", encoding="utf-8") as f:
        json.dump(ORG, f, ensure_ascii=False, indent=2)
    for args in (["init", "--root", ROOT], ["org", "load", "--root", ROOT],
                 ["auth", "passwd", "--root", ROOT,
                  "--who", "boss", "--password", "pw-boss"]):
        r = subprocess.run([sys.executable, "-m", "laoban", *args],
                           capture_output=True, text=True, env=env, cwd="/workspace")
        if r.returncode != 0:
            print("CLI 失败：", args, r.stdout, r.stderr)
            return 1

    dash = subprocess.Popen(
        [sys.executable, "-m", "laoban", "dashboard", "--root", ROOT,
         "--port", str(DASH_PORT), "--worker-interval", "1.0"],
        env=env, cwd="/workspace", stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)
    try:
        if not _wait_http(f"http://127.0.0.1:{DASH_PORT}/"):
            print("看板未启动")
            return 1
        return _playwright_check()
    finally:
        dash.terminate()
        try:
            dash.wait(timeout=5)
        except subprocess.TimeoutExpired:
            dash.kill()
        out = dash.stdout.read() if dash.stdout else ""
        print(f"\n看板日志（含 worker 输出）：\n{out[:800]}")
        llm.shutdown()
        shutil.rmtree(ROOT, ignore_errors=True)


def _playwright_check() -> int:
    from playwright.sync_api import sync_playwright

    ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{DASH_PORT}/")
        page.wait_for_load_state("networkidle")
        page.fill("#loginId", "boss")
        page.fill("#loginPw", "pw-boss")
        page.click("#loginBar button")
        page.wait_for_timeout(600)

        # 1. 提交 + 派单（网页操作）
        page.fill("#newTaskTitle", "e2e 自动运转任务")
        page.click("button:has-text('提交任务')")
        page.wait_for_timeout(500)
        tid = page.input_value("#assignId")
        page.fill("#assignTo", "dev")
        page.click("button:has-text('派发')")
        page.wait_for_timeout(600)
        msg = page.locator("#opMsg").inner_text()
        if "已派发给 dev" in msg:
            print(f"[OK] 网页派单：{tid}")
        else:
            print(f"[FAIL] 派单异常：{msg!r}")
            ok = False

        # 2. 核心：不敲任何命令，等 worker 自动执行到 reporting
        state = _wait_task_state(tid, "reporting")
        if state == "reporting":
            print("[OK] 自动运转：派单后任务自动 doing → reporting（零人工干预）")
        else:
            print(f"[FAIL] 任务未自动到 reporting（当前 {state}）")
            ok = False

        # 3. 交付物落档
        with open(os.path.join(ROOT, "tasks", f"{tid}.json"),
                  encoding="utf-8") as f:
            t = json.load(f)
        if any("AUTO-RUN-OK" in (p.get("deliverable", "") or "")
               for p in t.get("progress_log", [])):
            print("[OK] 交付物落 progress_log")
        else:
            print("[FAIL] 交付物缺失")
            ok = False

        # 4. 网页验收（刷新后验收框已自动填 tid）
        page.reload()
        page.wait_for_load_state("networkidle")
        page.fill("#loginId", "boss")
        page.fill("#loginPw", "pw-boss")
        page.click("#loginBar button")
        page.wait_for_timeout(600)
        page.fill("#acceptId", tid)
        page.select_option("#acceptScore", "5")
        page.click("button:has-text('验收')")
        page.wait_for_timeout(800)
        msg = page.locator("#opMsg").inner_text()
        if "评分 5/5" in msg:
            print("[OK] 看板一键验收：任务完成 + 绩效入账")
        else:
            print(f"[FAIL] 验收异常：{msg!r}")
            ok = False

        # 5. 绩效面板有 dev 记录
        perf = page.locator("#perf").inner_text()
        if "dev" in perf:
            print("[OK] 绩效面板：dev 完成数 1")
        else:
            print(f"[FAIL] 绩效面板无 dev：{perf!r}")
            ok = False

        page.screenshot(path="/workspace/scripts/worker_auto_run.png", full_page=True)
        browser.close()

    print("[DONE] 截图：scripts/worker_auto_run.png")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
