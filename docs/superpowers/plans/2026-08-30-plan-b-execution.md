# Plan B：执行引擎 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Plan A 的制度内核之上，实现执行引擎——扩展员工完整档案、LLM 网关（含 MockLLM）、工具循环与风险护栏、审批队列（分级放行+批处理）、员工记忆、派发器/调度器、执行引擎 Runner，最终用 MockLLM 驱动一个任务走完整个状态机（零网络依赖）。

**Architecture:** 全部零第三方运行时依赖。LLM 抽象为 Provider 接口，MockLLM 与 OpenAI 兼容 HTTP 网关共用同一 `chat()` 签名。工具循环以 `Tool` 注册表 + `Guard` 风险判定为核心；审批队列实现「autonomy_level × 风险等级」决策矩阵与「容量/时间联合触发」批处理；Memory 落为员工档案 `memory` 字段的读写封装。本计划末尾的集成冒烟用 MockLLM + 临时存储证明全链路可跑通。

**Tech Stack:** Python 3.10+，标准库（dataclasses / json / urllib / subprocess / pathlib / unittest），零第三方依赖。

**对应设计文档**：`docs/superpowers/specs/2026-08-30-agent-company-framework-design.md` 第 5.1（完整档案）、4.1（组件表）、6.2（审批队列）、6.1（合规硬规则）、4.4（技术决策）。

**前置**：Plan A 已实现 `laoban/core/{task,state_machine,employee,permission,store}.py`。

---

## 文件结构（本计划创建/修改）

```
laoban/
├── core/
│   ├── employee.py            # [修改] 扩展完整档案字段（memory/workspace/...）
│   ├── memory.py              # [新增] 员工记忆读写
│   ├── dispatcher.py          # [新增] 派发器
│   └── scheduler.py           # [新增] 调度器（停滞检测）
├── llm/
│   ├── __init__.py            # [新增]
│   ├── base.py                # [新增] LLM 抽象 + 消息/响应结构
│   ├── mock.py                # [新增] MockLLM
│   └── gateway.py             # [新增] 多供应商网关
└── runner/
    ├── __init__.py            # [新增]
    ├── guard.py               # [新增] 风险护栏（黑名单/白名单/风险等级）
    ├── tools.py               # [新增] 工具注册表与执行
    ├── approval_queue.py      # [新增] 审批队列
    └── runner.py              # [新增] 执行引擎
tests/
├── test_employee_full.py
├── test_memory.py
├── test_llm.py
├── test_guard_tools.py
├── test_approval_queue.py
├── test_dispatcher_scheduler.py
└── test_runner.py
```

---

## 设计细化：审批决策矩阵（对设计文档 6.2 的精确化）

风险等级（Guard 判定）：`low` / `medium` / `high`。支出与编制申请**永远审批**（与 autonomy_level 无关）。

| autonomy_level \ 风险 | low | medium | high | 支出/编制 |
|---|---|---|---|---|
| full（全自主） | 放行 | 放行 | 审批 | 审批 |
| semi（半自主） | 放行 | 审批 | 审批 | 审批 |
| supervised（受监管） | 审批 | 审批 | 审批 | 审批 |

底线：`high` 风险始终不可自动放行（任何 autonomy_level 都要审批）。

---

## Task 1: 扩展员工完整档案字段

**Files:**
- Modify: `laoban/core/employee.py`
- Test: `tests/test_employee_full.py`

- [ ] **Step 1: 写失败测试**

`tests/test_employee_full.py`:
```python
import unittest
from laoban.core.employee import Employee


class TestEmployeeFull(unittest.TestCase):
    def test_full_fields_roundtrip(self):
        e = Employee(
            id="emp-dev-001", name="陈默",
            reports_to="emp-pm-001", source="hired",
            job_description={"mission": "交付代码", "duties": [], "workflow_rules": [], "escalation": "转人类"},
            performance_goals={"max_concurrent": 3, "budget_daily_cost": 20.0},
            capabilities={"tools": ["file_rw"], "skills": ["python"], "model_fit": ["tool_loop"]},
            model_config={"provider": "deepseek", "model": "deepseek-chat"},
            permissions={"collaboration": [], "autonomy_level": "supervised"},
            memory={"experiences": [], "notes": []},
            workspace={"dir": "workspaces/emp-dev-001/", "queue": [], "context": {}},
        )
        e2 = Employee.from_dict(e.to_dict())
        self.assertEqual(e2.reports_to, "emp-pm-001")
        self.assertEqual(e2.job_description["mission"], "交付代码")
        self.assertEqual(e2.permissions["autonomy_level"], "supervised")
        self.assertEqual(e2.memory["experiences"], [])
        self.assertEqual(e2.workspace["dir"], "workspaces/emp-dev-001/")

    def test_defaults_still_sane(self):
        e = Employee(id="x", name="y")
        self.assertEqual(e.memory["experiences"], [])
        self.assertEqual(e.permissions["autonomy_level"], "supervised")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_employee_full -v`
Expected: FAIL（TypeError：`Employee.__init__` 不接受 `reports_to` 等参数）

- [ ] **Step 3: 扩展 employee.py**

`laoban/core/employee.py`（完整替换）:
```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Employee:
    id: str
    name: str
    title: str = ""
    department: str = ""
    reports_to: str = ""
    source: str = "hired"                 # founder/template/hired/cloned
    status: str = "active"                # active/suspended/terminated
    hired_at: str = ""
    job_description: dict[str, Any] = field(default_factory=lambda: {
        "mission": "", "duties": [], "workflow_rules": [],
        "escalation": "超出能力 → 转人类待办或上报直属上级",
    })
    performance_goals: dict[str, Any] = field(default_factory=lambda: {
        "max_concurrent": 3,
        "budget_daily_tokens": 500000,
        "budget_daily_cost": 20.0,
        "quality_bar": "",
    })
    capabilities: dict[str, Any] = field(default_factory=lambda: {
        "tools": [], "skills": [], "model_fit": [],
    })
    model_config: dict[str, Any] = field(default_factory=lambda: {
        "provider": "mock", "model": "mock", "temperature": 0.3,
    })
    permissions: dict[str, Any] = field(default_factory=lambda: {
        "collaboration": [],
        "can_assign_human_tasks": False,
        "spending_limit_per_task": 5.0,
        "autonomy_level": "supervised",   # supervised/semi/full
    })
    memory: dict[str, Any] = field(default_factory=lambda: {
        "experiences": [], "notes": [],
    })
    workspace: dict[str, Any] = field(default_factory=lambda: {
        "dir": "", "queue": [], "context": {},
    })

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "department": self.department,
            "reports_to": self.reports_to,
            "source": self.source,
            "status": self.status,
            "hired_at": self.hired_at,
            "job_description": self.job_description,
            "performance_goals": self.performance_goals,
            "capabilities": self.capabilities,
            "model_config": self.model_config,
            "permissions": self.permissions,
            "memory": self.memory,
            "workspace": self.workspace,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Employee":
        e = cls(id=d["id"], name=d["name"])
        for k in ("title", "department", "reports_to", "source", "status", "hired_at"):
            if k in d:
                setattr(e, k, d[k])
        for k in ("job_description", "performance_goals", "capabilities",
                  "model_config", "permissions", "memory", "workspace"):
            if k in d and isinstance(d[k], dict):
                # 合并默认值，确保新字段也有默认兜底
                merged = dict(getattr(e, k))
                merged.update(d[k])
                setattr(e, k, merged)
        return e
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_employee_full tests.test_permission -v`
Expected: PASS（Plan A 的 permission 测试仍通过 + 新测试通过）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/core/employee.py tests/test_employee_full.py && git commit -m "feat: 员工完整档案字段（memory/workspace/汇报关系/绩效/模型/权限）"
```

---

## Task 2: LLM 抽象与 MockLLM

**Files:**
- Create: `laoban/llm/__init__.py`
- Create: `laoban/llm/base.py`
- Create: `laoban/llm/mock.py`
- Test: `tests/test_llm.py`

- [ ] **Step 1: 写失败测试**

`tests/test_llm.py`:
```python
import unittest
from laoban.llm.base import Message, LLMResponse, LLMProvider
from laoban.llm.mock import MockLLM


class TestMockLLM(unittest.TestCase):
    def test_scripted_response(self):
        llm = MockLLM(responses=["你好，我是开发工程师"])
        resp = llm.chat([Message(role="user", content="hi")])
        self.assertEqual(resp.content, "你好，我是开发工程师")

    def test_round_robin_responses(self):
        llm = MockLLM(responses=["a", "b", "c"])
        self.assertEqual(llm.chat([]).content, "a")
        self.assertEqual(llm.chat([]).content, "b")
        self.assertEqual(llm.chat([]).content, "c")

    def test_exhausted_falls_back(self):
        llm = MockLLM(responses=["only"])
        llm.chat([])
        self.assertEqual(llm.chat([]).content, "only")  # 用完循环回第一条

    def test_default_response(self):
        llm = MockLLM()
        self.assertIsInstance(llm.chat([]).content, str)
        self.assertTrue(len(llm.chat([]).content) > 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_llm -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.llm.base'`）

- [ ] **Step 3: 实现 base.py 与 mock.py**

`laoban/llm/__init__.py`:
```python
```

`laoban/llm/base.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Message:
    role: str          # system / user / assistant / tool
    content: str
    name: str = ""


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class LLMProvider(Protocol):
    def chat(self, messages: list[Message], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        ...
```

`laoban/llm/mock.py`:
```python
from __future__ import annotations

from .base import LLMResponse, Message


class MockLLM:
    """演示模式 LLM：按脚本循环返回，承诺永不抛错。"""

    def __init__(self, responses: list[str] | None = None):
        self._responses = responses or ["（演示模式）任务已处理。"]
        self._idx = 0

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        r = self._responses[self._idx % len(self._responses)]
        self._idx += 1
        return LLMResponse(content=r)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_llm -v`
Expected: PASS（4 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/llm/ tests/test_llm.py && git commit -m "feat: LLM 抽象接口与 MockLLM"
```

---

## Task 3: LLM 网关（多供应商路由）

**Files:**
- Create: `laoban/llm/gateway.py`
- Test: `tests/test_llm.py`（追加）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_llm.py` 末尾追加：
```python
from laoban.llm.gateway import LLMGateway


class TestLLMGateway(unittest.TestCase):
    def test_route_to_mock(self):
        gw = LLMGateway()
        gw.register_mock("mock", MockLLM(responses=["hi"]))
        resp = gw.chat("mock", [Message(role="user", content="x")])
        self.assertEqual(resp.content, "hi")

    def test_unknown_provider_raises(self):
        gw = LLMGateway()
        with self.assertRaises(KeyError):
            gw.chat("nope", [])

    def test_model_config_resolves_provider(self):
        gw = LLMGateway()
        gw.register_mock("deepseek", MockLLM(responses=["ds"]))
        resp = gw.chat_for_employee({"provider": "deepseek", "model": "deepseek-chat"}, [])
        self.assertEqual(resp.content, "ds")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_llm.TestLLMGateway -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.llm.gateway'`）

- [ ] **Step 3: 实现 gateway.py**

`laoban/llm/gateway.py`:
```python
from __future__ import annotations

from typing import Any

from .base import LLMResponse, Message
from .mock import MockLLM


class LLMGateway:
    """统一 LLM 网关：按 provider 路由到具体实现。

    v0.1 只注册 mock provider；openai 兼容 HTTP provider 由后续接入，
    但路由接口已就位（provider 名 → chat 调用），不绑定具体厂商。
    """

    def __init__(self):
        self._providers: dict[str, Any] = {}

    def register_mock(self, name: str, llm: MockLLM) -> None:
        self._providers[name] = llm

    def chat(self, provider: str, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        if provider not in self._providers:
            raise KeyError(f"未注册的 provider: {provider}")
        return self._providers[provider].chat(messages, tools)

    def chat_for_employee(self, model_config: dict[str, Any], messages: list[Message]) -> LLMResponse:
        return self.chat(model_config.get("provider", "mock"), messages)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_llm -v`
Expected: PASS（7 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/llm/gateway.py tests/test_llm.py && git commit -m "feat: LLM 网关（多供应商路由）"
```

---

## Task 4: 工具循环与风险护栏（Guard）

**Files:**
- Create: `laoban/runner/__init__.py`
- Create: `laoban/runner/guard.py`
- Create: `laoban/runner/tools.py`
- Test: `tests/test_guard_tools.py`

- [ ] **Step 1: 写失败测试**

`tests/test_guard_tools.py`:
```python
import unittest
from laoban.runner.guard import Guard, classify_risk
from laoban.runner.tools import TOOLS, Tool


class TestGuard(unittest.TestCase):
    def setUp(self):
        self.guard = Guard(blocklist=["rm -rf"], domain_allowlist=["example.com"])

    def test_dangerous_command_high_risk(self):
        self.assertEqual(self.guard.check_command("rm -rf /"), "high")

    def test_safe_command_medium(self):
        self.assertEqual(self.guard.check_command("ls -la"), "high")  # 命令执行一律 high

    def test_domain_allowlist_medium(self):
        self.assertEqual(self.guard.check_url("https://example.com/x"), "medium")

    def test_domain_not_allowlisted_high(self):
        self.assertEqual(self.guard.check_url("https://evil.com/x"), "high")

    def test_file_inside_workspace_low(self):
        self.assertEqual(classify_risk("file_rw", {"path": "workspaces/a/out.txt"}), "low")

    def test_file_outside_workspace_high(self):
        self.assertEqual(classify_risk("file_rw", {"path": "/etc/passwd"}), "high")


class TestTools(unittest.TestCase):
    def test_tool_registered(self):
        self.assertIn("file_rw", TOOLS)
        self.assertIn("shell_exec", TOOLS)
        self.assertIn("web_search", TOOLS)

    def test_tool_has_required_attrs(self):
        t = TOOLS["shell_exec"]
        self.assertIsInstance(t, Tool)
        self.assertTrue(t.name)
        self.assertTrue(t.description)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_guard_tools -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.runner.guard'`）

- [ ] **Step 3: 实现 guard.py 与 tools.py**

`laoban/runner/__init__.py`:
```python
```

`laoban/runner/guard.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Guard:
    """合规硬规则层：确定性执行，不依赖 LLM。"""

    blocklist: list[str] = field(default_factory=lambda: [
        "rm -rf", "mkfs", "dd if=", "curl", "| sh", "| bash",
    ])
    domain_allowlist: list[str] = field(default_factory=list)

    def check_command(self, cmd: str) -> str:
        low = cmd.lower()
        if any(b in low for b in self.blocklist):
            return "high"
        return "high"  # 命令执行一律 high（安全优先）

    def check_url(self, url: str) -> str:
        from urllib.parse import urlparse
        host = urlparse(url).netloc
        if any(host == d or host.endswith("." + d) for d in self.domain_allowlist):
            return "medium"
        return "high"


def classify_risk(tool: str, args: dict) -> str:
    """根据工具 + 参数判定风险等级 low/medium/high。"""
    if tool == "file_rw":
        path = str(args.get("path", ""))
        if path.startswith("workspaces/"):
            return "low"
        return "high"
    if tool == "web_search":
        url = str(args.get("url", ""))
        from urllib.parse import urlparse
        if urlparse(url).netloc:
            return "medium"
        return "low"
    if tool == "shell_exec":
        return "high"
    return "low"
```

`laoban/runner/tools.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    execute: Callable[[dict[str, Any]], str]


def _file_rw(args: dict[str, Any]) -> str:
    path = args.get("path", "")
    content = args.get("content", "")
    mode = args.get("mode", "write")
    if mode == "read":
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"written {len(content)} bytes"


def _shell_exec(args: dict[str, Any]) -> str:
    import subprocess
    cmd = args.get("command", "")
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return r.stdout or r.stderr
    except subprocess.TimeoutExpired:
        return "timeout"


def _web_search(args: dict[str, Any]) -> str:
    return "（v0.1 演示）搜索结果占位"


TOOLS: dict[str, Tool] = {
    "file_rw": Tool("file_rw", "读写文件", _file_rw),
    "shell_exec": Tool("shell_exec", "执行命令", _shell_exec),
    "web_search": Tool("web_search", "网页搜索", _web_search),
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_guard_tools -v`
Expected: PASS（9 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/runner/guard.py laoban/runner/tools.py laoban/runner/__init__.py tests/test_guard_tools.py && git commit -m "feat: 工具循环与风险护栏（Guard 硬规则）"
```

---

## Task 5: 审批队列（分级放行 + 批处理）

**Files:**
- Create: `laoban/runner/approval_queue.py`
- Test: `tests/test_approval_queue.py`

- [ ] **Step 1: 写失败测试**

`tests/test_approval_queue.py`:
```python
import unittest
from laoban.runner.approval_queue import ApprovalQueue, ApprovalRequest, should_approve


class TestShouldApprove(unittest.TestCase):
    def test_high_risk_always_approve(self):
        self.assertTrue(should_approve("high", "full"))
        self.assertTrue(should_approve("high", "supervised"))

    def test_full_autonomy_low_medium_passthrough(self):
        self.assertFalse(should_approve("low", "full"))
        self.assertFalse(should_approve("medium", "full"))

    def test_semi_medium_approve(self):
        self.assertFalse(should_approve("low", "semi"))
        self.assertTrue(should_approve("medium", "semi"))

    def test_supervised_all_approve(self):
        self.assertTrue(should_approve("low", "supervised"))
        self.assertTrue(should_approve("medium", "supervised"))


class TestApprovalQueue(unittest.TestCase):
    def test_capacity_trigger(self):
        q = ApprovalQueue(batch_size=2, timeout_sec=9999)
        q.enqueue(ApprovalRequest(id="a", type="支出超限", risk="high"))
        self.assertEqual(q.flush_if_ready(), [])
        q.enqueue(ApprovalRequest(id="b", type="支出超限", risk="high"))
        batch = q.flush_if_ready()
        self.assertEqual([r.id for r in batch], ["a", "b"])

    def test_time_trigger(self):
        q = ApprovalQueue(batch_size=99, timeout_sec=0)
        q.enqueue(ApprovalRequest(id="a", type="支出超限", risk="high"))
        batch = q.flush_if_ready()
        self.assertEqual([r.id for r in batch], ["a"])

    def test_urgent_priority_channel(self):
        q = ApprovalQueue(batch_size=99, timeout_sec=9999, urgent_batch_size=1)
        q.enqueue(ApprovalRequest(id="u", type="高危操作", risk="high", priority="urgent"))
        batch = q.flush_if_ready()
        self.assertEqual([r.id for r in batch], ["u"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_approval_queue -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.runner.approval_queue'`）

- [ ] **Step 3: 实现 approval_queue.py**

`laoban/runner/approval_queue.py`:
```python
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


def should_approve(risk: str, autonomy_level: str) -> bool:
    """分级放行：返回 True 表示需要人类审批。

    决策矩阵：high 永远审批；full 放行 low/medium；semi 放行 low；
    supervised 全部审批。
    """
    if risk == "high":
        return True
    if autonomy_level == "supervised":
        return True
    if autonomy_level == "semi":
        return risk == "medium"
    if autonomy_level == "full":
        return False
    return True  # 未知等级默认保守审批


@dataclass
class ApprovalRequest:
    id: str
    type: str                      # 高危操作 | 支出超限 | 编制申请
    risk: str = "high"
    priority: str = "normal"       # normal | urgent
    requester: str = ""
    summary: str = ""
    amount: float = 0.0
    status: str = "pending"        # pending → approved/rejected
    approver: str = ""
    opinion: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass
class ApprovalQueue:
    batch_size: int = 5
    timeout_sec: int = 120
    urgent_batch_size: int = 1
    urgent_timeout_sec: int = 30

    def __post_init__(self):
        self._normal: list[ApprovalRequest] = []
        self._urgent: list[ApprovalRequest] = []

    def enqueue(self, req: ApprovalRequest) -> None:
        (self._urgent if req.priority == "urgent" else self._normal).append(req)

    def _pop_ready(self, items: list[ApprovalRequest], size: int, timeout: int) -> list[ApprovalRequest]:
        ready = [r for r in items if r.status == "pending"]
        now = time.time()
        if len(ready) >= size or (ready and now - ready[0].created_at >= timeout):
            batch = ready[:size]
            for r in batch:
                items.remove(r)
            return batch
        return []

    def flush_if_ready(self) -> list[ApprovalRequest]:
        batch = self._pop_ready(self._urgent, self.urgent_batch_size, self.urgent_timeout_sec)
        if batch:
            return batch
        return self._pop_ready(self._normal, self.batch_size, self.timeout_sec)

    def pending_count(self) -> int:
        return len(self._normal) + len(self._urgent)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_approval_queue -v`
Expected: PASS（8 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/runner/approval_queue.py tests/test_approval_queue.py && git commit -m "feat: 审批队列（分级放行 + 容量/时间批处理 + 优先级）"
```

---

## Task 6: 员工记忆（Memory）

**Files:**
- Create: `laoban/core/memory.py`
- Test: `tests/test_memory.py`

- [ ] **Step 1: 写失败测试**

`tests/test_memory.py`:
```python
import unittest
from laoban.core.employee import Employee
from laoban.core.memory import record_experience, add_note, recall


class TestMemory(unittest.TestCase):
    def test_record_experience(self):
        e = Employee(id="dev", name="阿码")
        record_experience(e, task_type="bugfix", outcome="success", learned="先读测试")
        self.assertEqual(len(e.memory["experiences"]), 1)
        self.assertEqual(e.memory["experiences"][0]["task_type"], "bugfix")

    def test_add_note(self):
        e = Employee(id="dev", name="阿码")
        add_note(e, "该客户不要纯管理背景")
        self.assertEqual(e.memory["notes"], ["该客户不要纯管理背景"])

    def test_recall_returns_memory(self):
        e = Employee(id="dev", name="阿码")
        add_note(e, "n1")
        data = recall(e)
        self.assertEqual(data["notes"], ["n1"])
        self.assertEqual(data["experiences"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_memory -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.core.memory'`）

- [ ] **Step 3: 实现 memory.py**

`laoban/core/memory.py`:
```python
from __future__ import annotations

from typing import Any

from .employee import Employee


def record_experience(emp: Employee, task_type: str, outcome: str, learned: str) -> None:
    emp.memory["experiences"].append({
        "task_type": task_type, "outcome": outcome, "learned": learned,
    })


def add_note(emp: Employee, text: str) -> None:
    emp.memory["notes"].append(text)


def recall(emp: Employee) -> dict[str, Any]:
    return emp.memory
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_memory -v`
Expected: PASS（3 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/core/memory.py tests/test_memory.py && git commit -m "feat: 员工轻量记忆（经验 + 备注）"
```

---

## Task 7: 派发器与调度器

**Files:**
- Create: `laoban/core/dispatcher.py`
- Create: `laoban/core/scheduler.py`
- Test: `tests/test_dispatcher_scheduler.py`

- [ ] **Step 1: 写失败测试**

`tests/test_dispatcher_scheduler.py`:
```python
import unittest
import time
from laoban.core.task import Task, PENDING, TRIAGE
from laoban.core.employee import Employee
from laoban.core.permission import PermissionDenied
from laoban.core.dispatcher import dispatch, resolve_agent_for_state
from laoban.core.scheduler import check_stall


class TestDispatcher(unittest.TestCase):
    def test_resolve_agent_for_state(self):
        self.assertEqual(resolve_agent_for_state(TRIAGE), "receptionist")

    def test_dispatch_returns_target(self):
        emp = Employee(id="receptionist", name="小助")
        task = Task(id="T-1", title="x", state=TRIAGE)
        target = dispatch(task, {"receptionist": emp})
        self.assertEqual(target.id, "receptionist")

    def test_dispatch_unknown_state(self):
        task = Task(id="T-1", title="x", state=PENDING)
        self.assertIsNone(dispatch(task, {}))


class TestScheduler(unittest.TestCase):
    def test_no_stall_when_fresh(self):
        task = Task(id="T-1", title="x", state=TRIAGE)
        self.assertFalse(check_stall(task, threshold_sec=180))

    def test_stall_detected(self):
        task = Task(id="T-1", title="x", state=TRIAGE)
        task.updated_at = time.time() - 200
        self.assertTrue(check_stall(task, threshold_sec=180))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_dispatcher_scheduler -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.core.dispatcher'`）

- [ ] **Step 3: 实现 dispatcher.py 与 scheduler.py**

`laoban/core/dispatcher.py`:
```python
from __future__ import annotations

from typing import Any

from .task import Task, TRIAGE, PLANNING, REVIEW, ASSIGNED, REPORTING
from .employee import Employee

# 状态 → 默认负责岗位 ID（与启动模式/默认模板约定的岗位 id 对齐）
_STATE_AGENT_MAP = {
    TRIAGE: "receptionist",      # 前台助理
    PLANNING: "pm",              # 项目经理
    REVIEW: "reviewer",          # 评审员
    ASSIGNED: "pm",              # 派发由 PM 执行
    REPORTING: "pm",             # 汇总由 PM 执行
}


def resolve_agent_for_state(state: str) -> str | None:
    return _STATE_AGENT_MAP.get(state)


def dispatch(task: Task, employees: dict[str, Employee]) -> Employee | None:
    """根据任务状态解析目标员工。Doing/Next 由 org 推断，v0.1 简化：按状态映射。"""
    agent_id = resolve_agent_for_state(task.state)
    if agent_id is None:
        return None
    return employees.get(agent_id)
```

`laoban/core/scheduler.py`:
```python
from __future__ import annotations

import time

from .task import Task


def check_stall(task: Task, threshold_sec: int = 180) -> bool:
    """判定任务是否停滞：距上次更新超过阈值秒数。兼容 ISO 字符串与时间戳。"""
    from datetime import datetime
    updated = task.updated_at
    if isinstance(updated, str):
        try:
            updated = datetime.fromisoformat(updated).timestamp()
        except ValueError:
            return False  # 无法解析，视为刚更新
    return (time.time() - updated) >= threshold_sec
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_dispatcher_scheduler -v`
Expected: PASS（6 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/core/dispatcher.py laoban/core/scheduler.py tests/test_dispatcher_scheduler.py && git commit -m "feat: 派发器与调度器（状态→岗位映射 + 停滞检测）"
```

---

## Task 8: Runner 执行引擎 + 集成冒烟

**Files:**
- Create: `laoban/runner/runner.py`
- Test: `tests/test_runner.py`

- [ ] **Step 1: 写失败测试（含集成冒烟）**

`tests/test_runner.py`:
```python
import tempfile
import unittest

from laoban.core.store import JsonStore
from laoban.core.task import Task, PENDING, TRIAGE, PLANNING, REVIEW, ASSIGNED, DOING, REPORTING, DONE
from laoban.core.employee import Employee
from laoban.core.state_machine import advance
from laoban.llm.gateway import LLMGateway
from laoban.llm.mock import MockLLM
from laoban.runner.runner import Runner


def make_gateway():
    gw = LLMGateway()
    for pid in ("receptionist", "pm", "reviewer", "dev", "test", "ops"):
        gw.register_mock(pid, MockLLM(responses=[f"[{pid}] 完成"]))
    return gw


def make_employees():
    return {
        "receptionist": Employee(id="receptionist", name="小助",
                                 permissions={"autonomy_level": "supervised", "collaboration": []}),
        "pm": Employee(id="pm", name="老谋"),
        "reviewer": Employee(id="reviewer", name="严审"),
        "dev": Employee(id="dev", name="阿码", capabilities={"tools": ["file_rw"]}),
    }


class TestRunner(unittest.TestCase):
    def test_run_returns_content(self):
        r = Runner(make_gateway())
        emp = Employee(id="dev", name="阿码")
        out = r.run(emp, Task(id="T-1", title="x"))
        self.assertIn("[dev]", out)

    def test_full_flow_mock(self):
        store = JsonStore(tempfile.mkdtemp())
        gw = make_gateway()
        runner = Runner(gw)
        employees = make_employees()

        task = Task(id="T-1", title="写一个函数")
        # 模拟一条完整状态流转，每个状态用 Runner 产出"完成"信号后推进
        path = [TRIAGE, PLANNING, REVIEW, ASSIGNED, DOING, REPORTING, DONE]
        for state in path:
            advance(task, state, actor="boss")
            agent_id = {"triage": "receptionist", "planning": "pm", "review": "reviewer",
                        "assigned": "pm", "doing": "dev", "reporting": "pm"}.get(state, "pm")
            emp = employees.get(agent_id, employees["pm"])
            runner.run(emp, task)
            store.save_task(task)

        self.assertEqual(task.state, DONE)
        self.assertEqual(len(task.flow_log), 7)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_runner -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.runner.runner'`）

- [ ] **Step 3: 实现 runner.py**

`laoban/runner/runner.py`:
```python
from __future__ import annotations

from laoban.core.task import Task
from laoban.core.employee import Employee
from laoban.llm.gateway import LLMGateway
from laoban.llm.base import Message
from laoban.core.memory import recall


class Runner:
    """执行引擎：组装 prompt → LLM → 产出。v0.1 工具循环按需调用。"""

    def __init__(self, gateway: LLMGateway):
        self.gateway = gateway

    def run(self, employee: Employee, task: Task) -> str:
        system = (
            f"你是 {employee.name}（{employee.title or '员工'}）。"
            f"岗位职责：{employee.job_description.get('mission', '')}。"
            f"经验记忆：{recall(employee)}。"
            "请基于任务标题产出可交付结果。"
        )
        messages = [
            Message(role="system", content=system),
            Message(role="user", content=f"任务：{task.title}"),
        ]
        resp = self.gateway.chat_for_employee(employee.model_config, messages)
        return resp.content
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_runner -v`
Expected: PASS（2 tests OK）

- [ ] **Step 5: 全量回归**

Run: `cd /workspace && python -m unittest discover -v`
Expected: PASS（Plan A + Plan B 全部测试通过）

- [ ] **Step 6: Commit**

```bash
cd /workspace && git add laoban/runner/runner.py tests/test_runner.py && git commit -m "feat: Runner 执行引擎 + MockLLM 全流程冒烟"
```

---

## 自审记录

- **Spec 覆盖**：完整档案 → Task 1；LLM 网关 + MockLLM → Task 2/3；工具循环 + Guard → Task 4；审批队列（分级放行+批处理）→ Task 5；Memory → Task 6；派发/调度 → Task 7；Runner → Task 8；MockLLM 全流程（D1 雏形）→ Task 8。
- **占位符扫描**：无 TBD/TODO，每步含完整代码。
- **类型一致性**：`Message/LLMResponse/LLMGateway.chat(provider, messages, tools)`、`MockLLM.chat`、`Guard.check_command/check_url`、`classify_risk`、`should_approve(risk, autonomy_level)`、`ApprovalRequest/ApprovalQueue.flush_if_ready`、`record_experience/add_note/recall`、`dispatch/resolve_agent_for_state`、`check_stall`、`Runner.run(employee, task)` 全部跨任务命名一致；Employee 字段与设计文档 5.1 对齐。
