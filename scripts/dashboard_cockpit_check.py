"""任务驾驶舱 e2e：看板上完成提交 → 派单 → 验收 → 绩效入账 → 审批。

验证路径与生产一致：
  1. CLI 建公司 + 设口令（boss=admin、mgr-dev=manager、emp-chen=staff）；
  2. 造一张 pending 审批单（模拟 AI 员工发起高危操作）；
  3. Playwright 以 boss 登录：提交任务 → 派给 dev →（python 模拟员工开工
     doing）→ 验收评分 4 → 绩效表出现 dev 一行；
  4. 审批：点「通过」→ 弹窗输入意见 → 状态 approved；
  5. staff 登录：派单/验收行隐藏；manager 登录：可见。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = tempfile.mkdtemp(prefix="laoban-cockpit-")
DASH_PORT = 7901

ORG = {
    "company": "驾驶舱演示公司",
    "business": "e2e",
    "departments": [
        {"id": "dev_dept", "name": "研发部", "roles": [
            {"id": "mgr-dev", "name": "沈负责人", "kind": "human",
             "title": "研发负责人", "permissions": {"role": "manager"}},
            {"id": "dev", "name": "阿码", "kind": "ai", "title": "开发工程师"},
            {"id": "emp-chen", "name": "陈工", "kind": "human",
             "title": "数据核查员"},
        ]},
        {"id": "hq", "name": "总办", "roles": [
            {"id": "boss", "name": "老板", "kind": "human", "title": "CEO",
             "permissions": {"role": "admin"}},
        ]},
    ],
}


def _wait_http(url: str, timeout: float = 15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def _run_cli(env, *args):
    r = subprocess.run([sys.executable, "-m", "laoban", *args],
                       capture_output=True, text=True, env=env, cwd="/workspace")
    if r.returncode != 0:
        print("CLI 失败：", " ".join(args))
        print(r.stdout, r.stderr)
        sys.exit(1)
    return r.stdout


def _make_pending_approval():
    """模拟 AI 员工发起高危操作 → 一张 pending 审批单。"""
    sys.path.insert(0, "/workspace")
    from laoban.core.store import JsonStore
    from laoban.runner.approval_log import ApprovalLog, ApprovalRequest
    st = JsonStore(ROOT)
    log = ApprovalLog(st)
    log.log_request(ApprovalRequest(
        id="AP-e2e0001", type="高危操作", risk="high",
        requester="dev", summary="执行 rm -rf 临时目录"))


def _advance_doing(task_id: str):
    """模拟员工开工：assigned → doing（Runner 的职责，e2e 里直接推进）。"""
    sys.path.insert(0, "/workspace")
    from laoban.core.store import JsonStore
    from laoban.core.state_machine import advance
    st = JsonStore(ROOT)
    t = st.load_task(task_id)
    advance(t, "doing", actor="dev", remark="开工")
    st.save_task(t)


def main() -> int:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with open(os.path.join(ROOT, "org.json"), "w", encoding="utf-8") as f:
        json.dump(ORG, f, ensure_ascii=False, indent=2)
    _run_cli(env, "init", "--root", ROOT)
    _run_cli(env, "org", "load", "--root", ROOT)
    _run_cli(env, "auth", "passwd", "--root", ROOT,
             "--who", "boss", "--password", "pw-boss")
    _run_cli(env, "auth", "passwd", "--root", ROOT,
             "--who", "mgr-dev", "--password", "pw-mgr")
    _run_cli(env, "auth", "passwd", "--root", ROOT,
             "--who", "emp-chen", "--password", "pw-chen")
    _make_pending_approval()

    dash = subprocess.Popen(
        [sys.executable, "-m", "laoban", "dashboard", "--root", ROOT,
         "--port", str(DASH_PORT)],
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
        shutil.rmtree(ROOT, ignore_errors=True)


def _login(page, emp_id, pw):
    page.goto(f"http://127.0.0.1:{DASH_PORT}/")
    page.wait_for_load_state("networkidle")
    page.fill("#loginId", emp_id)
    page.fill("#loginPw", pw)
    page.click("#loginBar button")
    page.wait_for_timeout(500)
    return page


def _playwright_check() -> int:
    from playwright.sync_api import sync_playwright

    ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ===== boss（admin）全流程 =====
        page = _login(browser.new_page(), "boss", "pw-boss")

        # 1. 提交任务
        page.fill("#newTaskTitle", "e2e 数据清洗")
        page.fill("#newTaskInstr", "清洗 sales.csv")
        page.click("button:has-text('提交任务')")
        page.wait_for_timeout(600)
        msg = page.locator("#opMsg").inner_text()
        if "任务已提交" in msg and page.input_value("#assignId").startswith("T-"):
            print(f"[OK] 提交任务：{msg}")
        else:
            print(f"[FAIL] 提交任务异常：{msg!r}")
            ok = False
        tid = page.input_value("#assignId")

        # 2. 派单给 dev
        page.fill("#assignTo", "dev")
        page.click("button:has-text('派发')")
        page.wait_for_timeout(600)
        msg = page.locator("#opMsg").inner_text()
        if "已派发给 dev" in msg and page.input_value("#acceptId") == tid:
            print(f"[OK] 派发：{msg}")
        else:
            print(f"[FAIL] 派发异常：{msg!r}")
            ok = False

        # 3. 模拟员工开工（assigned → doing）
        _advance_doing(tid)
        page.reload()
        page.wait_for_load_state("networkidle")

        # 4. 验收评分 4
        page.fill("#acceptId", tid)
        page.select_option("#acceptScore", "4")
        page.fill("#acceptComment", "e2e 验收：不错")
        page.click("button:has-text('验收')")
        page.wait_for_timeout(800)
        msg = page.locator("#opMsg").inner_text()
        if "评分 4/5" in msg:
            print(f"[OK] 验收：{msg}")
        else:
            print(f"[FAIL] 验收异常：{msg!r}")
            ok = False

        # 5. 绩效表出现 dev 行（完成数 1）
        perf_text = page.locator("#perf").inner_text()
        if "dev" in perf_text:
            print("[OK] 绩效面板：dev 已入账")
        else:
            print(f"[FAIL] 绩效面板无 dev：{perf_text!r}")
            ok = False

        # 6. 审批：通过（处理 prompt 弹窗）
        page.on("dialog", lambda d: d.accept("e2e 审批意见"))
        page.click("button:has-text('通过')")
        page.wait_for_timeout(800)
        msg = page.locator("#opMsg").inner_text()
        approvals_text = page.locator("#approvals").inner_text()
        if "已通过" in msg and "approved" in approvals_text:
            print("[OK] 审批通过：状态已更新")
        else:
            print(f"[FAIL] 审批异常：{msg!r} / {approvals_text!r}")
            ok = False

        page.screenshot(path="/workspace/scripts/cockpit_admin_view.png", full_page=True)
        page.close()

        # ===== staff：派单/验收行隐藏 =====
        page = _login(browser.new_page(), "emp-chen", "pw-chen")
        if not page.locator("#assignRow").is_visible() and \
                not page.locator("#acceptRow").is_visible():
            print("[OK] staff 视角：派单/验收操作行隐藏")
        else:
            print("[FAIL] staff 仍能看到派单/验收行")
            ok = False
        page.close()

        # ===== manager：操作行可见 =====
        page = _login(browser.new_page(), "mgr-dev", "pw-mgr")
        if page.locator("#assignRow").is_visible() and \
                page.locator("#acceptRow").is_visible():
            print("[OK] manager 视角：派单/验收可用（限本部门，API 层校验）")
        else:
            print("[FAIL] manager 看不到派单/验收行")
            ok = False
        page.close()
        browser.close()

    print("[DONE] 截图：scripts/cockpit_admin_view.png")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
