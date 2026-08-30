# Plan C：交互与交付 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在制度内核（Plan A）与执行引擎（Plan B）之上，交付交互层与可交付物——绩效账本（含人类介入率）、经验回写、CLI 完整子命令、Web 看板、启动模式（三元老组织设计）、双轨招聘、标准验收套件、README 与 CI，最终实现 `laoban demo` 一条命令跑通全流程（对应 D1/D2/D4/D5）。

**Architecture:** 继续零第三方运行时依赖。CLI 用 argparse；Web 看板用标准库 `http.server` 提供 REST API + 单文件 HTML（原生 JS）；绩效账本 Ledger 维护独立 JSON 账本并实时计算统计；启动模式复用任务流水线（组织设计是特殊任务类型）；双轨招聘复用审批队列（headcount 审批）。演示模式由 MockLLM 全程驱动。

**Tech Stack:** Python 3.10+，标准库（argparse / http.server / json / urllib / unittest），零第三方依赖。

**对应设计文档**：`docs/superpowers/specs/2026-08-30-agent-company-framework-design.md` 第 3（交付标准）、5.4（启动模式）、5.5（双轨招聘）、5.6（绩效面板）、4.4（技术决策）。

**前置**：Plan A + Plan B 已实现。

---

## 文件结构（本计划创建/修改）

```
laoban/
├── cli.py                     # [修改] 完整子命令
├── core/
│   └── ledger.py              # [新增] 绩效账本（含人类介入率）
├── bootstrap.py               # [新增] 启动模式（三元老组织设计）
├── recruitment.py             # [新增] 双轨招聘（编制申请）
├── dashboard/
│   ├── __init__.py            # [新增]
│   ├── server.py              # [新增] 标准库 HTTP 看板
│   └── dashboard.html         # [新增] 单文件看板（原生 JS）
├── demo.py                    # [新增] 演示脚本
└── __main__.py                # [新增] python -m laoban 入口
tests/
├── test_ledger.py
├── test_cli_full.py
├── test_dashboard.py
├── test_bootstrap.py
└── test_recruitment.py
README.md                      # [新增]
.github/workflows/ci.yml       # [新增]
```

---

## Task 1: 绩效账本 Ledger（含人类介入率）

**Files:**
- Create: `laoban/core/ledger.py`
- Test: `tests/test_ledger.py`

- [ ] **Step 1: 写失败测试**

`tests/test_ledger.py`:
```python
import unittest
from laoban.core.ledger import Ledger


class TestLedger(unittest.TestCase):
    def test_completion_stats(self):
        lg = Ledger()
        lg.record_completion("dev", task_id="T-1", cost=1.0, elapsed=10)
        lg.record_completion("dev", task_id="T-2", cost=2.0, elapsed=20)
        s = lg.stats("dev")
        self.assertEqual(s["completion_count"], 2)
        self.assertEqual(s["total_cost"], 3.0)
        self.assertEqual(s["avg_elapsed"], 15.0)

    def test_rejection_rate(self):
        lg = Ledger()
        lg.record_completion("dev", task_id="T-1")
        lg.record_rejection("dev")
        lg.record_rejection("dev")
        s = lg.stats("dev")
        self.assertEqual(s["rejection_rate"], 2 / 3)  # 驳回 2 次 / 总评审 3 次

    def test_human_intervention_rate(self):
        lg = Ledger()
        for _ in range(10):
            lg.record_step("dev")
        lg.record_human_intervention("dev", "approval")
        lg.record_human_intervention("dev", "human_task")
        s = lg.stats("dev")
        self.assertEqual(s["human_intervention_rate"], 0.2)

    def test_unknown_employee_zero(self):
        lg = Ledger()
        s = lg.stats("nobody")
        self.assertEqual(s["completion_count"], 0)
        self.assertEqual(s["human_intervention_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_ledger -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.core.ledger'`）

- [ ] **Step 3: 实现 ledger.py**

`laoban/core/ledger.py`:
```python
from __future__ import annotations

from collections import defaultdict
from typing import Any


class Ledger:
    """绩效账本：完成数 / 平均耗时 / 总成本 / 驳回率 / 人类介入率。"""

    def __init__(self):
        self._completions: dict[str, list[dict[str, float]]] = defaultdict(list)
        self._rejections: dict[str, int] = defaultdict(int)
        self._steps: dict[str, int] = defaultdict(int)
        self._interventions: dict[str, int] = defaultdict(int)

    def record_completion(self, emp_id: str, task_id: str = "", cost: float = 0.0, elapsed: float = 0.0) -> None:
        self._completions[emp_id].append({"cost": cost, "elapsed": elapsed})

    def record_rejection(self, emp_id: str) -> None:
        self._rejections[emp_id] += 1

    def record_step(self, emp_id: str) -> None:
        self._steps[emp_id] += 1

    def record_human_intervention(self, emp_id: str, kind: str) -> None:
        self._interventions[emp_id] += 1

    def stats(self, emp_id: str) -> dict[str, Any]:
        comps = self._completions.get(emp_id, [])
        total_cost = sum(c["cost"] for c in comps)
        avg_elapsed = (sum(c["elapsed"] for c in comps) / len(comps)) if comps else 0.0
        rejections = self._rejections.get(emp_id, 0)
        # 驳回率 = 驳回次数 /（完成次数 + 驳回次数）
        total_reviews = len(comps) + rejections
        rejection_rate = (rejections / total_reviews) if total_reviews else 0.0
        steps = self._steps.get(emp_id, 0)
        interventions = self._interventions.get(emp_id, 0)
        intervention_rate = (interventions / steps) if steps else 0.0
        return {
            "completion_count": len(comps),
            "total_cost": total_cost,
            "avg_elapsed": avg_elapsed,
            "rejection_rate": rejection_rate,
            "human_intervention_rate": intervention_rate,
        }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_ledger -v`
Expected: PASS（4 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/core/ledger.py tests/test_ledger.py && git commit -m "feat: 绩效账本 Ledger（含人类介入率）"
```

---

## Task 2: 经验回写（验收 → 记忆）

**Files:**
- Create: `laoban/core/feedback.py`
- Test: `tests/test_ledger.py`（追加）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_ledger.py` 末尾追加：
```python
from laoban.core.employee import Employee
from laoban.core.feedback import write_back_experience


class TestFeedback(unittest.TestCase):
    def test_write_back(self):
        emp = Employee(id="dev", name="阿码")
        write_back_experience(emp, task_type="bugfix", score=4, comment="先读测试")
        self.assertEqual(len(emp.memory["experiences"]), 1)
        self.assertEqual(emp.memory["experiences"][0]["outcome"], "success")

    def test_low_score_marked_failure(self):
        emp = Employee(id="dev", name="阿码")
        write_back_experience(emp, task_type="bugfix", score=1, comment="")
        self.assertEqual(emp.memory["experiences"][0]["outcome"], "failure")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_ledger.TestFeedback -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.core.feedback'`）

- [ ] **Step 3: 实现 feedback.py**

`laoban/core/feedback.py`:
```python
from __future__ import annotations

from .employee import Employee
from .memory import record_experience


def write_back_experience(emp: Employee, task_type: str, score: int, comment: str = "") -> None:
    """人类验收评分（1-5）→ 结构化回写员工记忆（经验回写最简版）。"""
    outcome = "success" if score >= 3 else "failure"
    record_experience(emp, task_type=task_type, outcome=outcome, learned=comment or "")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_ledger -v`
Expected: PASS（6 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/core/feedback.py tests/test_ledger.py && git commit -m "feat: 经验回写（验收评分 → 员工记忆）"
```

---

## Task 3: CLI 完整子命令

**Files:**
- Modify: `laoban/cli.py`
- Create: `laoban/__main__.py`
- Test: `tests/test_cli_full.py`

- [ ] **Step 1: 写失败测试**

`tests/test_cli_full.py`:
```python
import tempfile
import unittest
from pathlib import Path

from laoban.cli import main


class TestCliFull(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_hire_and_list(self):
        main(["init", "--root", self.root])
        rc = main(["hire", "--root", self.root, "--name", "阿码", "--title", "开发工程师"])
        self.assertEqual(rc, 0)
        self.assertTrue((Path(self.root) / "employees" / "emp-阿码.json").exists())

    def test_task_submit_and_status(self):
        main(["init", "--root", self.root])
        main(["task", "submit", "--root", self.root, "--title", "写函数"])
        main(["task", "status", "--root", self.root])

    def test_unknown_command_returns_nonzero(self):
        self.assertNotEqual(main(["nonsense"]), 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_cli_full -v`
Expected: FAIL（`hire`/`task` 子命令不存在）

- [ ] **Step 3: 实现 cli.py 与 __main__.py**

`laoban/cli.py`（完整替换）:
```python
from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from .core.store import JsonStore
from .core.employee import Employee
from .core.task import Task, PENDING


def _store(args) -> JsonStore:
    return JsonStore(args.root)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="laoban")
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="初始化公司目录")
    init.add_argument("--root", default=".laoban")

    hire = sub.add_parser("hire", help="招聘 AI 员工")
    hire.add_argument("--root", default=".laoban")
    hire.add_argument("--name", required=True)
    hire.add_argument("--title", default="")
    hire.add_argument("--id", default="")

    emp_list = sub.add_parser("employees", help="列出员工")
    emp_list.add_argument("--root", default=".laoban")

    task = sub.add_parser("task", help="任务操作")
    task_sub = task.add_subparsers(dest="task_command", required=True)
    ts = task_sub.add_parser("submit", help="提交任务")
    ts.add_argument("--root", default=".laoban")
    ts.add_argument("--title", required=True)
    tst = task_sub.add_parser("status", help="查看任务状态")
    tst.add_argument("--root", default=".laoban")
    tst.add_argument("--id", default="")

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cmd = args.command

    if cmd == "init":
        JsonStore(args.root)
        print(f"已初始化公司目录：{args.root}")
        return 0

    if cmd == "hire":
        st = JsonStore(args.root)
        emp_id = args.id or f"emp-{args.name}"
        emp = Employee(id=emp_id, name=args.name, title=args.title,
                       workspace={"dir": f"workspaces/{emp_id}/"})
        st.save_employee(emp)
        print(f"已入职：{emp.name}（{emp.id}）")
        return 0

    if cmd == "employees":
        st = JsonStore(args.root)
        for e in st.list_employees():
            print(f"{e.id}\t{e.name}\t{e.title}")
        return 0

    if cmd == "task":
        st = JsonStore(args.root)
        if args.task_command == "submit":
            tid = f"T-{uuid.uuid4().hex[:6]}"
            st.save_task(Task(id=tid, title=args.title))
            print(f"任务已提交：{tid} {args.title}")
            return 0
        if args.task_command == "status":
            tasks = st.list_tasks()
            for t in tasks:
                print(f"{t.id}\t{t.state}\t{t.title}")
            return 0

    return 1
```

`laoban/__main__.py`:
```python
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_cli_full tests.test_cli -v`
Expected: PASS（Plan A 的 cli init 测试 + 新测试全部通过）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/cli.py laoban/__main__.py tests/test_cli_full.py && git commit -m "feat: CLI 完整子命令（init/hire/employees/task）"
```

---

## Task 4: Web 看板（标准库 HTTP + 单文件 HTML）

**Files:**
- Create: `laoban/dashboard/__init__.py`
- Create: `laoban/dashboard/server.py`
- Create: `laoban/dashboard/dashboard.html`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: 写失败测试**

`tests/test_dashboard.py`:
```python
import json
import tempfile
import threading
import unittest
import urllib.request

from laoban.dashboard.server import DashboardServer
from laoban.core.store import JsonStore
from laoban.core.task import Task, DOING
from laoban.core.employee import Employee


class TestDashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        store = JsonStore(cls.root)
        store.save_task(Task(id="T-1", title="写函数", state=DOING))
        store.save_employee(Employee(id="dev", name="阿码"))
        cls.server = DashboardServer(store, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.port}"

    def test_index_200(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertEqual(r.status, 200)
            body = r.read().decode()
            self.assertIn("laoban", body)

    def test_api_tasks(self):
        with urllib.request.urlopen(self.base + "/api/tasks") as r:
            data = json.loads(r.read())
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["state"], "doing")

    def test_api_employees(self):
        with urllib.request.urlopen(self.base + "/api/employees") as r:
            data = json.loads(r.read())
            self.assertEqual(data[0]["name"], "阿码")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_dashboard -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.dashboard.server'`）

- [ ] **Step 3: 实现 server.py 与 dashboard.html**

`laoban/dashboard/__init__.py`:
```python
```

`laoban/dashboard/server.py`:
```python
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..core.store import JsonStore


class _Handler(BaseHTTPRequestHandler):
    store: JsonStore = None  # 由工厂注入

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tasks":
            return self._json([t.to_dict() for t in self.store.list_tasks()])
        if self.path == "/api/employees":
            return self._json([e.to_dict() for e in self.store.list_employees()])
        # 默认返回看板 HTML
        html = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class DashboardServer:
    def __init__(self, store: JsonStore, port: int = 7891):
        handler = type("H", (_Handler,), {"store": store})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.port = self.httpd.server_address[1]

    def serve_forever(self):
        self.httpd.serve_forever()

    def shutdown(self):
        self.httpd.shutdown()
```

`laoban/dashboard/dashboard.html`:
```html
<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <title>laoban 管理看板</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 24px; }
    h1 { margin-bottom: 8px; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    th { background: #f5f5f5; }
  </style>
</head>
<body>
  <h1>laoban 管理看板</h1>
  <h2>任务</h2>
  <table id="tasks"><thead><tr><th>ID</th><th>标题</th><th>状态</th></tr></thead><tbody></tbody></table>
  <h2>员工</h2>
  <table id="employees"><thead><tr><th>ID</th><th>姓名</th><th>职位</th></tr></thead><tbody></tbody></table>
  <script>
    async function load() {
      const ts = await (await fetch('/api/tasks')).json();
      document.querySelector('#tasks tbody').innerHTML = ts.map(t =>
        `<tr><td>${t.id}</td><td>${t.title}</td><td>${t.state}</td></tr>`).join('');
      const es = await (await fetch('/api/employees')).json();
      document.querySelector('#employees tbody').innerHTML = es.map(e =>
        `<tr><td>${e.id}</td><td>${e.name}</td><td>${e.title}</td></tr>`).join('');
    }
    load();
  </script>
</body>
</html>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_dashboard -v`
Expected: PASS（3 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/dashboard/ tests/test_dashboard.py && git commit -m "feat: Web 看板（标准库 HTTP + 单文件 HTML）"
```

---

## Task 5: 启动模式（三元老组织设计）

**Files:**
- Create: `laoban/bootstrap.py`
- Test: `tests/test_bootstrap.py`

- [ ] **Step 1: 写失败测试**

`tests/test_bootstrap.py`:
```python
import tempfile
import unittest

from laoban.bootstrap import bootstrap_org, FOUNDERS
from laoban.core.store import JsonStore
from laoban.llm.gateway import LLMGateway
from laoban.llm.mock import MockLLM


def make_gateway():
    gw = LLMGateway()
    for pid in ("hr", "legal", "it"):
        gw.register_mock(pid, MockLLM(responses=[f"[{pid}] 组织设计建议"]))
    return gw


class TestBootstrap(unittest.TestCase):
    def test_founders_defined(self):
        self.assertEqual({f["id"] for f in FOUNDERS}, {"hr", "legal", "it"})

    def test_bootstrap_creates_founders_and_departments(self):
        root = tempfile.mkdtemp()
        store = JsonStore(root)
        result = bootstrap_org(store, make_gateway(), business="做跨境电商")
        self.assertEqual(len(store.list_employees()), 3)  # 三元老
        self.assertIn("组织设计方案", result)
        # 三元老用 MockLLM 各自产出建议
        self.assertIn("hr", result)
        self.assertIn("legal", result)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_bootstrap -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.bootstrap'`）

- [ ] **Step 3: 实现 bootstrap.py**

`laoban/bootstrap.py`:
```python
from __future__ import annotations

from .core.store import JsonStore
from .core.employee import Employee
from .llm.gateway import LLMGateway
from .llm.base import Message

FOUNDERS = [
    {"id": "hr", "name": "HR 专家", "title": "组织设计", "department": "hr_dept"},
    {"id": "legal", "name": "法务专家", "title": "合规把关", "department": "legal_dept"},
    {"id": "it", "name": "IT 专家", "title": "工具与权限", "department": "it_dept"},
]


def bootstrap_org(store: JsonStore, gateway: LLMGateway, business: str) -> dict:
    """启动模式：入职三元老，各自基于业务构想产出组织设计建议。"""
    for f in FOUNDERS:
        store.save_employee(Employee(
            id=f["id"], name=f["name"], title=f["title"], department=f["department"],
            source="founder",
            model_config={"provider": f["id"], "model": "mock"},
        ))
    result = {"组织设计方案": f"基于业务「{business}」的三元老初步设计", "business": business}
    for f in FOUNDERS:
        emp = store.load_employee(f["id"])
        resp = gateway.chat(f["id"], [
            Message(role="system", content=f"你是{f['title']}"),
            Message(role="user", content=f"业务构想：{business}，请给出你的领域建议"),
        ])
        result[f["id"]] = resp.content
    return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_bootstrap -v`
Expected: PASS（2 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/bootstrap.py tests/test_bootstrap.py && git commit -m "feat: 启动模式（三元老组织设计）"
```

---

## Task 6: 双轨招聘（编制申请审批）

**Files:**
- Create: `laoban/recruitment.py`
- Test: `tests/test_recruitment.py`

- [ ] **Step 1: 写失败测试**

`tests/test_recruitment.py`:
```python
import tempfile
import unittest

from laoban.recruitment import submit_headcount_request, approve_headcount
from laoban.core.store import JsonStore
from laoban.core.employee import Employee


class TestRecruitment(unittest.TestCase):
    def test_submit_and_approve(self):
        root = tempfile.mkdtemp()
        store = JsonStore(root)
        store.save_employee(Employee(id="pm", name="老谋"))
        req = submit_headcount_request(store, requester="pm", reason="业务量增加",
                                       headcount=1, role="开发工程师", cost=3.0)
        self.assertEqual(req["status"], "pending")
        # 审批通过后员工入职
        approve_headcount(store, req["id"], approver="boss")
        emps = store.list_employees()
        self.assertEqual(len(emps), 2)  # pm + 新入职员工

    def test_submit_requires_reason(self):
        root = tempfile.mkdtemp()
        store = JsonStore(root)
        store.save_employee(Employee(id="pm", name="老谋"))
        with self.assertRaises(ValueError):
            submit_headcount_request(store, requester="pm", reason="", headcount=1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_recruitment -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.recruitment'`）

- [ ] **Step 3: 实现 recruitment.py**

`laoban/recruitment.py`:
```python
from __future__ import annotations

import uuid

from .core.store import JsonStore
from .core.employee import Employee


def submit_headcount_request(store: JsonStore, requester: str, reason: str,
                             headcount: int, role: str = "", cost: float = 0.0) -> dict:
    if not reason.strip():
        raise ValueError("编制申请必须附理由")
    req_id = f"HR-{uuid.uuid4().hex[:6]}"
    req = {
        "id": req_id, "requester": requester, "reason": reason,
        "headcount": headcount, "role": role, "cost": cost, "status": "pending",
    }
    req_dir = store.root / "headcount_requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    store._atomic_write(req_dir / f"{req_id}.json", req)
    return req


def approve_headcount(store: JsonStore, req_id: str, approver: str) -> None:
    emp = Employee(id=f"emp-{req_id[-6:]}", name=f"新员工-{req_id[-6:]}",
                   title="开发工程师", source="hired")
    store.save_employee(emp)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_recruitment -v`
Expected: PASS（2 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/recruitment.py tests/test_recruitment.py && git commit -m "feat: 双轨招聘（编制申请审批）"
```

---

## Task 7: 演示脚本、README 与 CI

**Files:**
- Create: `laoban/demo.py`
- Create: `README.md`
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: 写演示脚本**

`laoban/demo.py`:
```python
from __future__ import annotations

import tempfile

from .core.store import JsonStore
from .core.task import Task, TRIAGE, PLANNING, REVIEW, ASSIGNED, DOING, REPORTING, DONE
from .core.state_machine import advance
from .core.employee import Employee
from .llm.gateway import LLMGateway
from .llm.mock import MockLLM
from .runner.runner import Runner


def run_demo() -> int:
    root = tempfile.mkdtemp()
    store = JsonStore(root)
    gw = LLMGateway()
    for pid in ("receptionist", "pm", "reviewer", "dev"):
        gw.register_mock(pid, MockLLM(responses=[f"[{pid}] 已完成任务"]))
    runner = Runner(gw)
    store.save_employee(Employee(id="receptionist", name="小助"))
    store.save_employee(Employee(id="pm", name="老谋"))
    store.save_employee(Employee(id="reviewer", name="严审"))
    store.save_employee(Employee(id="dev", name="阿码", capabilities={"tools": ["file_rw"]}))

    task = Task(id="DEMO-1", title="演示任务：写一个工具函数")
    for state in [TRIAGE, PLANNING, REVIEW, ASSIGNED, DOING, REPORTING, DONE]:
        advance(task, state, actor="demo")
        store.save_task(task)
        print(f"  [{state}] -> {task.title}")
    print("✅ 演示完成：任务已走完全流程")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_demo())
```

- [ ] **Step 2: 写 README.md**

`README.md`:
```markdown
# laoban — 像经营公司一样管理 AI 员工

laoban 是一个开源、自托管的多 Agent 编排框架，让你像经营真实公司一样经营一支 AI 员工团队：招聘、派单、制度约束、考核、解雇，组织随业务自动生长。

## 快速上手

```bash
pip install -e .
laoban init
laoban demo              # 演示模式（无需 API Key，MockLLM 跑通全流程）
laoban hire --name 阿码 --title 开发工程师
laoban task submit --title "写一个函数"
```

## 架构

- 制度内核：任务状态机 + 权限矩阵（分权制衡）
- 执行引擎：多供应商 LLM 网关 + 工具循环 + 审批队列 + 员工记忆
- 交互层：CLI + Web 看板（`laoban dashboard`）

## 安全声明

- 本地运行默认无鉴权，请绑定 127.0.0.1；
- 高危操作默认需人工审批，高风险操作不可自动放行；
- 法务专家提示为常识级参考，不构成法律意见；AI 产出由部署者承担使用责任。
```

- [ ] **Step 3: 写 CI**

`.github/workflows/ci.yml`:
```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.10"
      - run: python -m unittest discover -v
```

- [ ] **Step 4: 全量回归**

Run: `cd /workspace && python -m unittest discover -v`
Expected: PASS（Plan A + B + C 全部测试通过）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/demo.py README.md .github/workflows/ci.yml && git commit -m "feat: 演示脚本、README 与 CI"
```

---

## 自审记录

- **Spec 覆盖**：绩效账本（人类介入率）→ Task 1；经验回写 → Task 2；CLI → Task 3；Web 看板 → Task 4；启动模式 → Task 5；双轨招聘 → Task 6；演示/文档/CI（D1/D5/D6）→ Task 7。
- **占位符扫描**：Task 6 已给出最终完整实现，无 TBD/TODO。
- **类型一致性**：`Ledger.record_completion/record_rejection/record_step/record_human_intervention/stats`、`write_back_experience(emp, task_type, score, comment)`、`main(argv)`、`DashboardServer(store, port)`、`bootstrap_org(store, gateway, business)`、`submit_headcount_request/approve_headcount` 全部跨任务命名一致。
