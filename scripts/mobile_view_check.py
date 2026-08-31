"""手机版视觉验证：375×812 视口起看板 → 断言响应式布局 + 时间线催办合流 + 未读红点 + 截图。

数据：完整生命周期任务（8 节点时间线）+ 超期催办任务（催办/升级/自动标记合流）+
未读信 2 封（emp-chen 登录后红点亮，查看收件箱后清零）。
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = tempfile.mkdtemp(prefix="laoban-mobile-check-")
PORT = 7933

ORG = {
    "company": "手机版演示公司",
    "business": "mobile",
    "departments": [
        {"id": "hq", "name": "总办", "roles": [
            {"id": "boss", "name": "老板", "kind": "human", "title": "CEO",
             "permissions": {"role": "admin"}},
        ]},
        {"id": "ops", "name": "运营部", "roles": [
            {"id": "pm", "name": "潘经理", "kind": "human", "title": "项目经理"},
        ]},
        {"id": "dev_dept", "name": "研发部", "roles": [
            {"id": "dev", "kind": "ai", "name": "阿码", "title": "开发工程师",
             "reports_to": "pm"},
            {"id": "emp-chen", "kind": "human", "name": "陈工", "title": "数据核查员",
             "reports_to": "dev"},
        ]},
    ],
}


def _wait_http(url, timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def _seed() -> int:
    """直接走核心 API 造数据：CLI 造不出 flow_log/urge_log 这类富任务。"""
    from laoban.core.store import JsonStore
    from laoban.core.task import Task, PENDING, ASSIGNED, DOING, REPORTING, DONE
    from laoban.core.state_machine import advance
    from laoban.core.messenger import send as msg_send
    from laoban.core.workstation import enqueue
    from laoban.org import instantiate

    store = JsonStore(ROOT)
    with open(os.path.join(ROOT, "org.json"), encoding="utf-8") as f:
        org = json.load(f)
    instantiate(store, org, which="all")

    now = datetime.now(timezone.utc)

    # 完整生命周期（时间线 8 节点：提交 + 7 次流转，含停留时长）
    t1 = Task(id="T-1001", title="客户主数据清洗", created_by="pm",
              due_at=(now - timedelta(days=1)).isoformat())
    for st, actor, remark in [
            ("triage", "receptionist" if False else "pm", "常规数据类"),
            ("planning", "pm", "拆解为三步"),
            ("review", "reviewer" if False else "pm", "方案可行"),
            (ASSIGNED, "pm", "派给阿码"),
            (DOING, "dev", ""),
            (REPORTING, "dev", "清洗完成，修复 832 条"),
            (DONE, "pm", "验收通过")]:
        advance(t1, st, actor=actor, remark=remark)
        store.save_task(t1)
    t1.assignee = "dev"
    # 时间戳拉开间隔（同一毫秒连转 → 停留时长为 0 不渲染 ⏳）
    t1.created_at = (now - timedelta(days=7)).isoformat()
    for i, entry in enumerate(t1.flow_log):
        entry["at"] = (now - timedelta(days=7)
                       + timedelta(days=i + 1)).isoformat()
    store.save_task(t1)
    enqueue(store, "dev", t1.id)

    # 超期 + 催办升级链（催 2 次：升级直属上级 dev → 升级上级的上级 pm，第 2 次自动）
    t2 = Task(id="T-1002", title="月度对账差异核对", created_by="pm",
              assignee="emp-chen",
              due_at=(now - timedelta(days=3)).isoformat())
    t2.flow_log.append({"at": (now - timedelta(days=4)).isoformat(),
                        "from": PENDING, "to": ASSIGNED, "actor": "pm",
                        "remark": "派给陈工"})
    t2.flow_log.append({"at": (now - timedelta(days=3, hours=2)).isoformat(),
                        "from": ASSIGNED, "to": DOING, "actor": "emp-chen",
                        "remark": "开始核对"})
    t2.urge_log.append({"at": (now - timedelta(days=2)).isoformat(), "by": "pm",
                        "to": "emp-chen", "escalated_to": "dev"})
    t2.urge_log.append({"at": (now - timedelta(days=1)).isoformat(), "by": "pm",
                        "to": "emp-chen", "escalated_to": "pm", "auto": True})
    t2.state = DOING
    store.save_task(t2)
    enqueue(store, "emp-chen", t2.id)

    # 新提交 + 在办（列表与负荷视图有数据）
    t3 = Task(id="T-1003", title="新员工培训资料整理", created_by="pm")
    store.save_task(t3)
    t4 = Task(id="T-1004", title="库存异常排查", created_by="pm", assignee="dev",
              due_at=(now + timedelta(days=2)).isoformat())
    t4.state = DOING
    t4.flow_log.append({"at": (now - timedelta(hours=10)).isoformat(),
                        "from": ASSIGNED, "to": DOING, "actor": "dev"})
    store.save_task(t4)

    # 未读 ×2 → emp-chen 红点
    msg_send(store, "pm", "emp-chen",
             "T-1002 对账差异今天必须给结论，老板在等。", task_id="T-1002")
    msg_send(store, "dev", "emp-chen",
             "主数据已清洗完，核对以新表为准。", task_id="T-1001")

    # 口令：boss（管理员走查）+ emp-chen（员工红点走查）
    from laoban.core.auth import AuthStore
    auth = AuthStore(ROOT)
    auth.set_password("boss", "pw-boss")
    auth.set_password("emp-chen", "pw-chen")
    print("[SEED] boss / emp-chen 口令已设，任务 4 件（T-1002 催办 2 次）")
    return 0


def main() -> int:
    with open(os.path.join(ROOT, "org.json"), "w", encoding="utf-8") as f:
        json.dump(ORG, f, ensure_ascii=False, indent=2)
    if _seed() != 0:
        return 1
    env = dict(os.environ, LAOBAN_AUTO_URGE="0", PYTHONIOENCODING="utf-8")
    dash = subprocess.Popen(
        [sys.executable, "-m", "laoban", "dashboard", "--root", ROOT,
         "--port", str(PORT), "--no-worker"],
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
        import shutil
        shutil.rmtree(ROOT, ignore_errors=True)


def _check() -> int:
    from playwright.sync_api import sync_playwright
    ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 375, "height": 812},
                                  device_scale_factor=2, is_mobile=True,
                                  has_touch=True)
        page = ctx.new_page()
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

        def login(emp, pw):
            page.fill("#loginId", emp)
            page.fill("#loginPw", pw)
            page.click("#loginBar button")
            page.wait_for_timeout(600)

        # ---- 管理员走查：布局 + 时间线合流 ----
        login("boss", "pw-boss")

        assert_("viewport meta 生效（375 视口）",
                page.evaluate("document.documentElement.clientWidth") == 375)
        over = page.evaluate("document.documentElement.scrollWidth"
                             " - document.documentElement.clientWidth")
        if over > 1:
            offenders = page.evaluate("""() => {
              const vw = document.documentElement.clientWidth;
              const bad = [];
              const contained = el => {
                for (let p = el.parentElement; p && p !== document.body;
                     p = p.parentElement) {
                  const cs = getComputedStyle(p);
                  if (['auto','scroll','hidden'].includes(cs.overflowX)
                      || ['auto','scroll','hidden'].includes(cs.overflow))
                    return true;
                }
                return false;
              };
              document.querySelectorAll('body *').forEach(el => {
                const r = el.getBoundingClientRect();
                if ((r.right > vw + 1 || r.left < -1) && !contained(el)
                    && r.width > 0)
                  bad.push(`${el.tagName}${el.id ? '#'+el.id : ''}.`
                           + String(el.className).slice(0, 50)
                           + ` r=${Math.round(r.right)} w=${Math.round(r.width)}`);
              });
              return bad.slice(0, 25);
            }""")
            print("  [DEBUG] 溢出元素：", offenders)
        assert_(f"页面无横向溢出（手机不出现整页横滚，超出 {over}px）", over <= 1)
        nav = page.evaluate("() => { const n = document.querySelector('nav.anchors');"
                            "const cs = getComputedStyle(n);"
                            "return {ox: cs.overflowX, sw: n.scrollWidth, cw: n.clientWidth}; }")
        assert_(f"导航横向滑条（overflow={nav['ox']}，内容 {nav['sw']}>{nav['cw']}）",
                nav["ox"] == "auto" and nav["sw"] > nav["cw"])
        fs = page.evaluate(
            "getComputedStyle(document.getElementById('newTaskTitle')).fontSize")
        assert_(f"输入字号 16px 防 iOS 聚焦放大（实际 {fs}）", fs == "16px")
        tbl = page.evaluate(
            "() => { const t = document.querySelector('#tasks');"
            "const cs = getComputedStyle(t);"
            "return {d: cs.display, ox: cs.overflowX, sw: t.scrollWidth, cw: t.clientWidth}; }")
        assert_(f"任务表容器化横滚（display={tbl['d']}，内容 {tbl['sw']}>{tbl['cw']}）",
                tbl["d"] == "block" and tbl["ox"] == "auto" and tbl["sw"] > tbl["cw"])
        page.screenshot(path="/workspace/scripts/mobile_view_top.png")

        # 时间线：T-1001 完整流转 8 节点 + 停留时长（按 id 定位行，不依赖排序）
        row_of = lambda tid: page.locator(
            f"#tasks tbody tr:has-text('{tid}')").first
        row_of("T-1001").locator("button:has-text('详情')").click()
        page.wait_for_timeout(200)
        assert_("T-1001 时间线 8 节点（提交 + 7 流转）",
                page.locator("#tasks .tl-node").count() == 8)
        assert_("时间线含停留时长标记",
                page.locator("#tasks .tl-dwell").count() >= 5)
        page.screenshot(path="/workspace/scripts/mobile_view_timeline.png",
                        full_page=True)

        # 时间线：T-1002 催办节点合流（收起 T-1001，展开 T-1002）
        row_of("T-1001").locator("button:has-text('详情')").click()
        row_of("T-1002").locator("button:has-text('详情')").click()
        page.wait_for_timeout(200)
        assert_("T-1002 催办节点 2 条（与流转合流渲染）",
                page.locator("#tasks .tl-urge").count() == 2)
        body = page.locator("#tasks .tl-row").inner_text()
        assert_("催办文案含「第 1 次催办」「第 2 次催办」",
                "第 1 次催办" in body and "第 2 次催办" in body)
        assert_("升级链可见（抄送 dev → pm）",
                "升级抄送 dev" in body and "升级抄送 pm" in body)
        assert_("自动催办标记可见", "（自动）" in body)
        assert_("催办徽标渲染（催2）",
                page.locator("#tasks .wait-badge").filter(has_text="催2").count() == 1)

        # ---- #25 时间线节点操作跳转 ----
        # 催办节点出「回 emp-chen」「回 dev」「回 pm」签；流转节点出「找 ·」签
        acts = page.locator("#tasks .tl-act")
        acts_text = acts.all_inner_texts()
        assert_(f"操作签渲染（{len(acts_text)} 枚）",
                any(t.startswith("回 ") for t in acts_text)
                and any(t.startswith("找 ") for t in acts_text))
        assert_("升级节点直接回复签（回 dev / 回 pm）",
                "回 dev" in acts_text and "回 pm" in acts_text)
        assert_("承接人工位跳转签",
                page.locator("#tasks .tl-meta .tl-act").count() == 1)
        page.screenshot(path="/workspace/scripts/mobile_view_tl_acts.png")
        # 点「回 emp-chen」→ 对话区收件人预填 + 视口滚到对话区
        page.locator("#tasks .tl-act", has_text="回 emp-chen").first.click()
        page.wait_for_timeout(700)   # smooth 滚动
        assert_("点击回信签：chatTo 预填 emp-chen",
                page.input_value("#chatTo") == "emp-chen")
        chat_box = page.evaluate(
            "() => { const r = document.getElementById('chatSection')"
            ".getBoundingClientRect(); return r.top; }")
        assert_(f"点击回信签：视口滚到对话区（top={chat_box:.0f}）",
                -100 < chat_box < 500)
        # 点 meta 的「工位」→ 队列区预填承接人并加载（T-1002 仍展开）
        page.locator("#tasks .tl-meta .tl-act", has_text="工位").click()
        page.wait_for_timeout(700)
        assert_("点击工位签：queueWho 预填 emp-chen",
                page.input_value("#queueWho") == "emp-chen")
        assert_("点击工位签：队列已加载（表格有任务行）",
                page.locator("#queueTasks tbody tr").count() >= 1)

        # ---- #24 PWA：manifest / 图标 / SW ----
        page.wait_for_timeout(500)   # 等 load 事件后 SW 注册+激活+claim
        pwa = page.evaluate("""async () => {
          const m = await (await fetch('/manifest.json')).json();
          const icons = await Promise.all(m.icons.map(async i =>
            (await fetch(i.src)).status));
          const sw = await navigator.serviceWorker.getRegistration();
          return {display: m.display, start: m.start_url, icons,
                  sw: !!sw, scope: sw ? sw.scope : ''};
        }""")
        assert_(f"manifest standalone + start_url=/（{pwa['display']}）",
                pwa["display"] == "standalone" and pwa["start"] == "/")
        assert_(f"manifest 图标全部可达（{pwa['icons']}）",
                all(s == 200 for s in pwa["icons"]))
        assert_(f"Service Worker 已注册（scope={pwa['scope']}）", pwa["sw"])
        assert_("页面受 SW 控制（离线壳就绪）",
                page.evaluate("!!navigator.serviceWorker.controller"))

        # ---- 员工走查：未读红点 ----
        page.click("text=退出")
        page.wait_for_timeout(400)
        login("emp-chen", "pw-chen")
        dot = page.locator("#msgDot")
        assert_("未读红点亮（2 封新信）",
                dot.evaluate("el => el.classList.contains('on')")
                and dot.inner_text().strip() == "2")
        page.screenshot(path="/workspace/scripts/mobile_view_reddot.png")

        # 查看收件箱 → 已读清零（红点熄灭）
        page.locator("#msgSection button:has-text('查看收发件箱')").click()
        page.wait_for_timeout(600)
        assert_("查看收件箱后红点熄灭",
                not dot.evaluate("el => el.classList.contains('on')"))
        page.screenshot(path="/workspace/scripts/mobile_view_inbox.png")

        page.screenshot(path="/workspace/scripts/mobile_view_full.png", full_page=True)
        assert_("零 JS 报错", not errors)
        if errors:
            print("  报错明细：", errors[:5])
        ctx.close()
        browser.close()
    print("[DONE] 截图：scripts/mobile_view_{top,timeline,reddot,inbox,full}.png")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
