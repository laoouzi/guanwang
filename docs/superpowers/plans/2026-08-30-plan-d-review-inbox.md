# Plan D：评审检查单与人类收件箱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐三个计划审阅发现的两个高严重度缺口——评审员的准奏/封驳判断逻辑（LLM 检查单）与人类待办收件箱（HumanInbox），使"制度性审核"和"人机混合"从状态机骨架变成真实可用的机制。

**Architecture:** 零第三方依赖。Reviewer 用检查清单驱动 LLM 输出「准奏/封驳」判断；HumanInbox 复用 `JsonStore` 的原子写，把人类待办作为独立实体落盘。两者均用 MockLLM 或纯数据测试，无网络依赖。

**Tech Stack:** Python 3.10+，标准库（dataclasses / uuid / unittest），零第三方依赖。

**对应设计文档**：`docs/superpowers/specs/2026-08-30-agent-company-framework-design.md` 第 6（合规双层·检查单层）、4.2（WaitingHuman 状态）、组件表（HumanInbox）。

**前置**：Plan A + Plan B（已实现 `JsonStore`、`LLMGateway`、`MockLLM`、`Task`、`Employee`）。

---

## 文件结构（本计划创建）

```
laoban/
├── runner/
│   └── reviewer.py            # [新增] 评审检查单（准奏/封驳）
└── core/
    └── human_inbox.py         # [新增] 人类待办收件箱
tests/
├── test_reviewer.py
└── test_human_inbox.py
```

---

## Task 1: 评审检查单（Reviewer 准奏/封驳）

**Files:**
- Create: `laoban/runner/reviewer.py`
- Test: `tests/test_reviewer.py`

- [ ] **Step 1: 写失败测试**

`tests/test_reviewer.py`:
```python
import unittest
from laoban.runner.reviewer import Reviewer, ReviewDecision
from laoban.llm.gateway import LLMGateway
from laoban.llm.mock import MockLLM
from laoban.core.employee import Employee
from laoban.core.task import Task


def make_gateway(verdict):
    gw = LLMGateway()
    gw.register_mock("reviewer", MockLLM(responses=[verdict]))
    return gw


class TestReviewer(unittest.TestCase):
    def test_approve(self):
        gw = make_gateway("[准奏] 方案完整，验收标准明确")
        r = Reviewer(gw, checklist=["完整性"])
        decision = r.review(Employee(id="reviewer", name="严审"),
                            Task(id="T-1", title="x"), plan="方案内容")
        self.assertTrue(decision.approved)
        self.assertIn("准奏", decision.reason)

    def test_reject(self):
        gw = make_gateway("[封驳] 缺少性能测试")
        r = Reviewer(gw, checklist=["完整性"])
        decision = r.review(Employee(id="reviewer", name="严审"),
                            Task(id="T-1", title="x"), plan="")
        self.assertFalse(decision.approved)

    def test_default_checklist(self):
        r = Reviewer(make_gateway("x"))
        self.assertTrue(len(r.checklist) >= 3)

    def test_reject_keyword_bohui(self):
        gw = make_gateway("驳回：方案不完整")
        r = Reviewer(gw)
        decision = r.review(Employee(id="reviewer", name="严审"), Task(id="T-1", title="x"), plan="")
        self.assertFalse(decision.approved)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_reviewer -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.runner.reviewer'`）

- [ ] **Step 3: 实现 reviewer.py**

`laoban/runner/reviewer.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from ..core.task import Task
from ..core.employee import Employee
from ..llm.base import Message
from ..llm.gateway import LLMGateway

DEFAULT_CHECKLIST = [
    "方案完整性：是否覆盖需求要点",
    "子任务拆解合理性：粒度是否可执行",
    "安全合规风险：是否有越权/数据外发/违规内容",
    "验收标准明确性：能否客观判定完成",
]


@dataclass
class ReviewDecision:
    approved: bool
    reason: str


class Reviewer:
    """评审员：用检查清单驱动 LLM 输出「准奏/封驳」判断（合规检查单层）。"""

    def __init__(self, gateway: LLMGateway, checklist: list[str] | None = None):
        self.gateway = gateway
        self.checklist = checklist or DEFAULT_CHECKLIST

    def review(self, employee: Employee, task: Task, plan: str) -> ReviewDecision:
        checklist_text = "\n".join(f"- {c}" for c in self.checklist)
        system = (
            "你是评审员。逐项审查方案，输出「准奏」或「封驳」及理由，"
            f"审查清单：\n{checklist_text}"
        )
        messages = [
            Message(role="system", content=system),
            Message(role="user", content=f"任务：{task.title}\n方案：{plan}"),
        ]
        resp = self.gateway.chat_for_employee(employee.model_config, messages)
        content = resp.content
        approved = ("封驳" not in content) and ("驳回" not in content)
        return ReviewDecision(approved=approved, reason=content)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_reviewer -v`
Expected: PASS（4 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/runner/reviewer.py tests/test_reviewer.py && git commit -m "feat: 评审检查单（LLM 准奏/封驳判断）"
```

---

## Task 2: 人类待办收件箱（HumanInbox）

**Files:**
- Create: `laoban/core/human_inbox.py`
- Test: `tests/test_human_inbox.py`

- [ ] **Step 1: 写失败测试**

`tests/test_human_inbox.py`:
```python
import tempfile
import unittest

from laoban.core.store import JsonStore
from laoban.core.human_inbox import HumanInbox, HumanTask


class TestHumanInbox(unittest.TestCase):
    def setUp(self):
        self.store = JsonStore(tempfile.mkdtemp())
        self.inbox = HumanInbox(self.store)

    def test_create_and_list_pending(self):
        self.inbox.create(task_id="T-1", title="核查简历项目贡献", assignee="陈工")
        pending = self.inbox.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].assignee, "陈工")

    def test_complete_removes_from_pending(self):
        self.inbox.create(task_id="T-1", title="核查", assignee="陈工")
        ht = self.inbox.list_pending()[0]
        self.inbox.complete(ht.id, result="核查通过")
        self.assertEqual(len(self.inbox.list_pending()), 0)

    def test_human_task_roundtrip(self):
        ht = HumanTask(id="HT-1", task_id="T-1", title="x", assignee="y")
        ht2 = HumanTask.from_dict(ht.to_dict())
        self.assertEqual(ht2.id, "HT-1")
        self.assertEqual(ht2.status, "pending")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_human_inbox -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.core.human_inbox'`）

- [ ] **Step 3: 实现 human_inbox.py**

`laoban/core/human_inbox.py`:
```python
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from .store import JsonStore


@dataclass
class HumanTask:
    id: str
    task_id: str
    title: str
    assignee: str
    deliverable_format: str = ""
    status: str = "pending"       # pending → completed
    result: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "task_id": self.task_id, "title": self.title,
            "assignee": self.assignee, "deliverable_format": self.deliverable_format,
            "status": self.status, "result": self.result,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "HumanTask":
        return cls(
            id=d["id"], task_id=d.get("task_id", ""), title=d.get("title", ""),
            assignee=d.get("assignee", ""), deliverable_format=d.get("deliverable_format", ""),
            status=d.get("status", "pending"), result=d.get("result", ""),
        )


class HumanInbox:
    """人类待办收件箱：AI 派发的人类子任务在此认领、填写结果、交还。"""

    def __init__(self, store: JsonStore):
        self.store = store
        self.dir = store.root / "human_tasks"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str):
        return self.dir / f"{task_id}.json"

    def create(self, task_id: str, title: str, assignee: str, deliverable_format: str = "") -> HumanTask:
        ht = HumanTask(id=f"HT-{uuid.uuid4().hex[:6]}", task_id=task_id,
                       title=title, assignee=assignee, deliverable_format=deliverable_format)
        self.store._atomic_write(self._path(ht.id), ht.to_dict())
        return ht

    def list_pending(self) -> list[HumanTask]:
        out = []
        for p in self.dir.glob("*.json"):
            d = self.store._read_json(p)
            if d and d.get("status") == "pending":
                out.append(HumanTask.from_dict(d))
        return out

    def complete(self, task_id: str, result: str) -> None:
        d = self.store._read_json(self._path(task_id))
        if not d:
            raise KeyError(f"人类待办不存在：{task_id}")
        ht = HumanTask.from_dict(d)
        ht.status = "completed"
        ht.result = result
        self.store._atomic_write(self._path(task_id), ht.to_dict())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_human_inbox -v`
Expected: PASS（3 tests OK）

- [ ] **Step 5: 全量回归**

Run: `cd /workspace && python -m unittest discover -v`
Expected: PASS（Plan A + B + C + D 全部测试通过）

- [ ] **Step 6: Commit**

```bash
cd /workspace && git add laoban/core/human_inbox.py tests/test_human_inbox.py && git commit -m "feat: 人类待办收件箱 HumanInbox（list/complete）"
```

---

## 自审记录

- **Spec 覆盖**：LLM 检查单层（准奏/封驳）→ Task 1；HumanInbox（list/complete）→ Task 2。
- **占位符扫描**：无 TBD/TODO，每步含完整代码。
- **类型一致性**：`Reviewer(gateway, checklist).review(employee, task, plan) -> ReviewDecision`、`HumanTask(id, task_id, title, assignee, ...)`、`HumanInbox(store).create/list_pending/complete` 命名一致；复用 `JsonStore._atomic_write/_read_json` 与 Plan A 签名一致。

---

## 执行顺序（四计划全览）

1. **Plan A**（6 任务）→ 制度内核
2. **Plan B**（8 任务）→ 执行引擎
3. **Plan D**（2 任务）→ 评审检查单 + HumanInbox（紧接 B，补审核与人机混合）
4. **Plan C**（7 任务）→ 交互与交付（最后，整合所有内核）
