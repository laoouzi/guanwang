"""Playwright 验证看板：组织架构/消息/队列三个新视图。"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://127.0.0.1:7891")
    page.wait_for_load_state("networkidle")

    # 1. 组织架构视图默认加载
    assert page.locator("#org .dept").count() >= 3, "部门分组应默认渲染"
    assert "dev_dept" in page.locator("#org").inner_text()
    assert "陈工" in page.locator("#org").inner_text()
    print("[OK] 组织架构视图：部门分组 + 人机混排渲染")

    # 2. 消息视图：查询 dev 的收发件箱
    page.fill("#msgWho", "dev")
    page.click("button:has-text('查看收发件箱')")
    page.wait_for_timeout(300)
    inbox_text = page.locator("#msgInbox").inner_text()
    assert "pm" in inbox_text and "请优先处理 T-1" in inbox_text
    print("[OK] 点对点消息视图：dev 收件箱含 pm 的消息")

    # 3. 队列视图：查询 dev 的工位队列
    page.fill("#queueWho", "dev")
    page.click("button:has-text('查看队列')")
    page.wait_for_timeout(300)
    queue_text = page.locator("#queueTasks").inner_text()
    assert "T-1" in queue_text and "写函数" in queue_text and "T-2" in queue_text
    print("[OK] 工位队列视图：dev 队列含 T-1/T-2")

    # 4. 员工花名册带状态列
    assert "在职" in page.locator("#employees").inner_text()
    print("[OK] 花名册状态列渲染")

    page.screenshot(path="/workspace/scripts/dashboard_preview.png", full_page=True)
    browser.close()
    print("[DONE] 截图：scripts/dashboard_preview.png")
