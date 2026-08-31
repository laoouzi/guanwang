"""RBAC 端到端验证：两个身份登录看板，看到的内容确实不一样。

验证路径与生产一致（不走测试桩）：
  1. CLI 建公司 + 自定义 org.json（两个部门 + 岗位 role 配置）；
  2. CLI 给普通员工 / 部门负责人 / 老板分别设口令；
  3. Playwright 分别以 staff / manager 身份登录：
     - staff：花名册只有本部门、组织架构无其他部门、查别人消息被拒；
     - manager：花名册全公司（跨部门脱敏）、能看本部门下属队列；
     - 前端角色徽章与查询框锁定行为正确。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = tempfile.mkdtemp(prefix="laoban-rbac-")
DASH_PORT = 7895

ORG = {
    "company": "权限演示公司",
    "business": "RBAC 演示",
    "departments": [
        {"id": "dev_dept", "name": "研发部", "roles": [
            {"id": "mgr-dev", "name": "沈负责人", "kind": "human",
             "title": "研发负责人",
             "permissions": {"role": "manager"}},
            {"id": "dev", "name": "阿码", "kind": "ai", "title": "开发工程师"},
            {"id": "emp-chen", "name": "陈工", "kind": "human",
             "title": "数据核查员"},
        ]},
        {"id": "fin_dept", "name": "财务部", "roles": [
            {"id": "fin", "name": "小金", "kind": "ai", "title": "财务分析"},
            {"id": "emp-wang", "name": "王姐", "kind": "human",
             "title": "出纳"},
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


def main() -> int:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    py = sys.executable
    with open(os.path.join(ROOT, "org.json"), "w", encoding="utf-8") as f:
        json.dump(ORG, f, ensure_ascii=False, indent=2)

    steps = [
        [py, "-m", "laoban", "init", "--root", ROOT],
        [py, "-m", "laoban", "org", "load", "--root", ROOT],
        [py, "-m", "laoban", "auth", "passwd", "--root", ROOT,
         "--who", "emp-chen", "--password", "pw-chen"],
        [py, "-m", "laoban", "auth", "passwd", "--root", ROOT,
         "--who", "mgr-dev", "--password", "pw-mgr"],
        [py, "-m", "laoban", "auth", "passwd", "--root", ROOT,
         "--who", "boss", "--password", "pw-boss"],
    ]
    for cmd in steps:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd="/workspace")
        if r.returncode != 0:
            print("命令失败：", " ".join(cmd[2:]))
            print(r.stdout, r.stderr)
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
        shutil.rmtree(ROOT, ignore_errors=True)


def _login(page, emp_id, pw):
    page.goto(f"http://127.0.0.1:{DASH_PORT}/")
    page.wait_for_load_state("networkidle")
    page.fill("#loginId", emp_id)
    page.fill("#loginPw", pw)
    page.click("#loginBar button")
    page.wait_for_timeout(400)
    return page


def _playwright_check() -> int:
    from playwright.sync_api import sync_playwright

    ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ===== staff 视角：emp-chen（研发部普通员工）=====
        page = _login(browser.new_page(), "emp-chen", "pw-chen")
        me = page.locator("#meInfo").inner_text()
        if "员工" in me and "dev_dept" in me:
            print("[OK] staff 登录：角色徽章=员工，可见范围=dev_dept")
        else:
            print(f"[FAIL] staff 身份栏异常：{me!r}")
            ok = False

        n_emp = page.locator("#employees tbody tr").count()
        if n_emp == 3:   # 本部门 3 人（mgr-dev/dev/emp-chen）
            print(f"[OK] staff 花名册：只看到本部门 {n_emp} 人")
        else:
            print(f"[FAIL] staff 花名册人数异常：{n_emp}（应 3）")
            ok = False

        depts = page.locator("#org .dept h3").all_inner_texts()
        if len(depts) == 1 and "dev_dept" in depts[0]:
            print(f"[OK] staff 组织架构：仅本部门（{depts[0]}）")
        else:
            print(f"[FAIL] staff 组织架构异常：{depts}")
            ok = False

        if page.locator("#msgWho").is_disabled():
            print("[OK] staff 消息查询框锁定为本人")
        else:
            print("[FAIL] staff 消息查询框未锁定")
            ok = False

        # 直接请求别人消息 → 403
        status = page.evaluate("""async () => {
            const r = await fetch('/api/messages?who=emp-wang'); return r.status; }""")
        if status == 403:
            print("[OK] staff 查他人消息被拒（403）")
        else:
            print(f"[FAIL] staff 查他人消息未被拒：{status}")
            ok = False

        # staff 花名册无权限字段（同事项脱敏）
        has_perm = page.evaluate("""async () => {
            const es = await (await fetch('/api/employees')).json();
            return es.some(e => e.id !== 'emp-chen' && 'permissions' in e); }""")
        if not has_perm:
            print("[OK] staff 视角：同事档案不含 permissions/memory 等敏感字段")
        else:
            print("[FAIL] staff 视角泄露敏感字段")
            ok = False

        page.screenshot(path="/workspace/scripts/rbac_staff_view.png", full_page=True)
        page.close()

        # ===== manager 视角：mgr-dev（研发负责人）=====
        page = _login(browser.new_page(), "mgr-dev", "pw-mgr")
        me = page.locator("#meInfo").inner_text()
        if "部门负责人" in me:
            print("[OK] manager 登录：角色徽章=部门负责人")
        else:
            print(f"[FAIL] manager 身份栏异常：{me!r}")
            ok = False

        n_emp = page.locator("#employees tbody tr").count()
        if n_emp == 6:   # 全公司 6 人
            print(f"[OK] manager 花名册：全公司 {n_emp} 人")
        else:
            print(f"[FAIL] manager 花名册人数异常：{n_emp}（应 6）")
            ok = False

        depts = page.locator("#org .dept h3").all_inner_texts()
        if len(depts) >= 3:
            print(f"[OK] manager 组织架构：全公司 {len(depts)} 个部门")
        else:
            print(f"[FAIL] manager 组织架构异常：{depts}")
            ok = False

        # 跨部门脱敏：fin_dept 员工无 permissions 字段
        cross = page.evaluate("""async () => {
            const es = await (await fetch('/api/employees')).json();
            const fin = es.find(e => e.department === 'fin_dept');
            return fin && ('permissions' in fin); }""")
        if not cross:
            print("[OK] manager 跨部门档案脱敏（无 permissions）")
        else:
            print("[FAIL] manager 跨部门泄露敏感字段")
            ok = False

        # manager 可查本部门下属队列
        page.fill("#queueWho", "dev")
        page.click("button:has-text('查看队列')")
        page.wait_for_timeout(500)
        queue_head = page.locator("#queueTasks tbody").inner_text()
        if "队列为空" in queue_head or "T-" in queue_head:
            print("[OK] manager 查本部门下属队列：允许")
        else:
            print(f"[FAIL] manager 查下属队列异常：{queue_head!r}")
            ok = False

        # manager 查下属消息仍被拒（隐私边界）
        status = page.evaluate("""async () => {
            const r = await fetch('/api/messages?who=emp-chen'); return r.status; }""")
        if status == 403:
            print("[OK] manager 查下属私信被拒（403，隐私边界）")
        else:
            print(f"[FAIL] manager 查下属私信未被拒：{status}")
            ok = False

        page.screenshot(path="/workspace/scripts/rbac_manager_view.png", full_page=True)
        page.close()
        browser.close()

    print("[DONE] 截图：scripts/rbac_staff_view.png / rbac_manager_view.png")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
