# Plan A：制度内核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 laoban 的制度内核——任务模型、状态机（含合法转换强校验与驳回循环）、权限矩阵、员工最小模型、JSON 原子存储，全部零第三方依赖、TDD 完成。

**Architecture:** 纯 Python 标准库，包名 `laoban`，核心逻辑位于 `laoban/core/` 下的聚焦小模块。状态机用字符串常量 + 转换白名单字典；权限矩阵以员工 `permissions`/`capabilities` 字段为输入；存储用 JSON 文件 + 临时文件原子重命名。此计划不引入 LLM、不引入网络、不引入数据库，每个 Task 独立可测。

**Tech Stack:** Python 3.10+，标准库（dataclasses / json / os / pathlib / tempfile / argparse / unittest），零运行时第三方依赖。

**对应设计文档**：`docs/superpowers/specs/2026-08-30-agent-company-framework-design.md` 第 4.2（状态机）、5.1（档案）、5.2（权限矩阵）、4.4（JSON 存储）。

---

## 文件结构（本计划创建）

```
/workspace/
├── pyproject.toml                 # 项目元数据，包名 laoban，CLI 入口 laoban
├── laoban/
│   ├── __init__.py                # 版本号
│   ├── cli.py                     # CLI 入口（Plan A 仅实现 init）
│   └── core/
│       ├── __init__.py
│       ├── task.py                # 任务模型 + 状态常量
│       ├── state_machine.py       # 状态机 + 转换校验 + 驳回计数
│       ├── employee.py            # 员工最小模型
│       ├── permission.py          # 权限矩阵（协作 + 工具）
│       └── store.py               # JSON 原子存储
└── tests/
    ├── __init__.py
    ├── test_task.py
    ├── test_state_machine.py
    ├── test_permission.py
    └── test_store.py
```

---

## Task 1: 项目骨架

**Files:**
- Create: `pyproject.toml`
- Create: `laoban/__init__.py`
- Create: `laoban/core/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: 写 pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "laoban"
version = "0.1.0"
description = "Run your company of AI employees"
requires-python = ">=3.10"

[project.scripts]
laoban = "laoban.cli:main"

[tool.setuptools.packages.find]
include = ["laoban*"]
```

- [ ] **Step 2: 写包初始化文件**

`laoban/__init__.py`:
```python
__version__ = "0.1.0"
```

`laoban/core/__init__.py`（空文件）:
```python
```

`tests/__init__.py`（空文件）:
```python
```

- [ ] **Step 3: 验证包可导入**

Run: `cd /workspace && python -c "import laoban; print(laoban.__version__)"`
Expected: 输出 `0.1.0`

- [ ] **Step 4: Commit**

```bash
cd /workspace && git add pyproject.toml laoban/ tests/ && git commit -m "chore: laoban 项目骨架（pyproject + 包结构）"
```

---

## Task 2: 任务模型与状态常量

**Files:**
- Create: `laoban/core/task.py`
- Test: `tests/test_task.py`

- [ ] **Step 1: 写失败测试**

`tests/test_task.py`:
```python
import unittest
from laoban.core.task import Task, PENDING, DONE, TERMINAL_STATES, utcnow


class TestTaskModel(unittest.TestCase):
    def test_defaults(self):
        t = Task(id="T-1", title="写函数")
        self.assertEqual(t.state, PENDING)
        self.assertEqual(t.priority, "normal")
        self.assertEqual(t.review_round, 0)
        self.assertEqual(t.flow_log, [])
        self.assertEqual(t.progress_log, [])

    def test_roundtrip(self):
        t = Task(id="T-1", title="写函数", state=DONE, review_round=2)
        d = t.to_dict()
        self.assertEqual(d["id"], "T-1")
        t2 = Task.from_dict(d)
        self.assertEqual(t2.id, t.id)
        self.assertEqual(t2.state, DONE)
        self.assertEqual(t2.review_round, 2)

    def test_terminal_states(self):
        self.assertIn(DONE, TERMINAL_STATES)

    def test_utcnow_is_iso(self):
        self.assertIn("T", utcnow())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_task -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.core.task'`）

- [ ] **Step 3: 实现 task.py**

`laoban/core/task.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

PENDING = "pending"
TRIAGE = "triage"
PLANNING = "planning"
REVIEW = "review"
ASSIGNED = "assigned"
DOING = "doing"
WAITING_HUMAN = "waiting_human"
REPORTING = "reporting"
DONE = "done"
CANCELLED = "cancelled"
BLOCKED = "blocked"

TERMINAL_STATES = frozenset({DONE, CANCELLED, BLOCKED})


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Task:
    id: str
    title: str
    state: str = PENDING
    priority: str = "normal"
    review_round: int = 0
    block_reason: str = ""
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)
    flow_log: list[dict[str, Any]] = field(default_factory=list)
    progress_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "state": self.state,
            "priority": self.priority,
            "review_round": self.review_round,
            "block_reason": self.block_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "flow_log": self.flow_log,
            "progress_log": self.progress_log,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Task":
        return cls(
            id=d["id"],
            title=d["title"],
            state=d.get("state", PENDING),
            priority=d.get("priority", "normal"),
            review_round=d.get("review_round", 0),
            block_reason=d.get("block_reason", ""),
            created_at=d.get("created_at", utcnow()),
            updated_at=d.get("updated_at", utcnow()),
            flow_log=d.get("flow_log", []),
            progress_log=d.get("progress_log", []),
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_task -v`
Expected: PASS（4 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/core/task.py tests/test_task.py && git commit -m "feat: 任务模型与状态常量"
```

---

## Task 3: 状态机与转换校验

**Files:**
- Create: `laoban/core/state_machine.py`
- Test: `tests/test_state_machine.py`

- [ ] **Step 1: 写失败测试**

`tests/test_state_machine.py`:
```python
import unittest
from laoban.core.task import (
    Task, PENDING, TRIAGE, PLANNING, REVIEW, ASSIGNED, DOING,
    WAITING_HUMAN, REPORTING, DONE, CANCELLED, BLOCKED,
)
from laoban.core.state_machine import (
    advance, can_transition, IllegalTransition, MAX_REVIEW_ROUNDS,
)


class TestStateMachine(unittest.TestCase):
    def test_happy_path(self):
        t = Task(id="T-1", title="x")
        for s in [TRIAGE, PLANNING, REVIEW, ASSIGNED, DOING, REPORTING, DONE]:
            advance(t, s, actor="boss")
        self.assertEqual(t.state, DONE)
        self.assertEqual(len(t.flow_log), 7)

    def test_illegal_jump_rejected(self):
        t = Task(id="T-1", title="x")  # pending
        with self.assertRaises(IllegalTransition):
            advance(t, DOING)

    def test_terminal_blocks_advance(self):
        t = Task(id="T-1", title="x", state=DONE)
        ok, _ = can_transition(t, TRIAGE)
        self.assertFalse(ok)

    def test_cancel_anywhere(self):
        t = Task(id="T-1", title="x", state=DOING)
        advance(t, CANCELLED)
        self.assertEqual(t.state, CANCELLED)

    def test_reject_increments_round(self):
        t = Task(id="T-1", title="x", state=REVIEW)
        advance(t, PLANNING, actor="reviewer", remark="封驳")
        self.assertEqual(t.review_round, 1)
        self.assertEqual(t.state, PLANNING)

    def test_reject_beyond_max_rounds(self):
        t = Task(id="T-1", title="x", state=REVIEW, review_round=MAX_REVIEW_ROUNDS)
        with self.assertRaises(IllegalTransition):
            advance(t, PLANNING)

    def test_waiting_human_roundtrip(self):
        t = Task(id="T-1", title="x", state=DOING)
        advance(t, WAITING_HUMAN)
        advance(t, DOING)
        self.assertEqual(t.state, DOING)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_state_machine -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.core.state_machine'`）

- [ ] **Step 3: 实现 state_machine.py**

`laoban/core/state_machine.py`:
```python
from __future__ import annotations

from .task import (
    Task, PENDING, TRIAGE, PLANNING, REVIEW, ASSIGNED, DOING,
    WAITING_HUMAN, REPORTING, DONE, CANCELLED, BLOCKED,
    TERMINAL_STATES, utcnow,
)

MAX_REVIEW_ROUNDS = 3

_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING: frozenset({TRIAGE}),
    TRIAGE: frozenset({PLANNING}),
    PLANNING: frozenset({REVIEW}),
    REVIEW: frozenset({ASSIGNED, PLANNING}),
    ASSIGNED: frozenset({DOING}),
    DOING: frozenset({REPORTING, WAITING_HUMAN}),
    WAITING_HUMAN: frozenset({DOING}),
    REPORTING: frozenset({DONE}),
}


class IllegalTransition(Exception):
    """非法状态流转。"""


def can_transition(
    task: Task,
    to_state: str,
    max_review_rounds: int = MAX_REVIEW_ROUNDS,
) -> tuple[bool, str]:
    if task.state in TERMINAL_STATES:
        return False, f"终态 {task.state} 不可再流转"
    if to_state in {CANCELLED, BLOCKED}:
        return True, "允许取消/阻塞"
    if task.state == REVIEW and to_state == PLANNING:
        if task.review_round >= max_review_rounds:
            return False, f"驳回超限（最多 {max_review_rounds} 轮）"
        return True, "驳回"
    if to_state not in _VALID_TRANSITIONS.get(task.state, frozenset()):
        return False, f"非法转换 {task.state} -> {to_state}"
    return True, "合法"


def advance(
    task: Task,
    to_state: str,
    actor: str = "",
    remark: str = "",
    max_review_rounds: int = MAX_REVIEW_ROUNDS,
) -> Task:
    ok, reason = can_transition(task, to_state, max_review_rounds)
    if not ok:
        raise IllegalTransition(reason)
    if task.state == REVIEW and to_state == PLANNING:
        task.review_round += 1
    task.flow_log.append({
        "at": utcnow(),
        "from": task.state,
        "to": to_state,
        "actor": actor,
        "remark": remark,
    })
    task.state = to_state
    task.updated_at = utcnow()
    return task
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_state_machine -v`
Expected: PASS（7 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/core/state_machine.py tests/test_state_machine.py && git commit -m "feat: 状态机与合法转换强校验"
```

---

## Task 4: 员工模型与权限矩阵

**Files:**
- Create: `laoban/core/employee.py`
- Create: `laoban/core/permission.py`
- Test: `tests/test_permission.py`

- [ ] **Step 1: 写失败测试**

`tests/test_permission.py`:
```python
import unittest
from laoban.core.employee import Employee
from laoban.core.permission import (
    can_collaborate, can_use_tool,
    require_collaboration, PermissionDenied,
)


class TestPermission(unittest.TestCase):
    def test_collaboration_allowed(self):
        pm = Employee(id="pm", name="老谋", permissions={"collaboration": ["reviewer"]})
        self.assertTrue(can_collaborate(pm, "reviewer"))

    def test_collaboration_denied(self):
        pm = Employee(id="pm", name="老谋", permissions={"collaboration": ["reviewer"]})
        self.assertFalse(can_collaborate(pm, "dev"))

    def test_require_collaboration_raises(self):
        pm = Employee(id="pm", name="老谋", permissions={"collaboration": []})
        with self.assertRaises(PermissionDenied):
            require_collaboration(pm, "dev")

    def test_tool_allowlist(self):
        dev = Employee(id="dev", name="阿码", capabilities={"tools": ["file_rw"]})
        self.assertTrue(can_use_tool(dev, "file_rw"))
        self.assertFalse(can_use_tool(dev, "shell_exec"))

    def test_employee_roundtrip(self):
        dev = Employee(id="dev", name="阿码", title="开发工程师")
        d = dev.to_dict()
        self.assertEqual(d["name"], "阿码")
        dev2 = Employee.from_dict(d)
        self.assertEqual(dev2.id, dev.id)
        self.assertEqual(dev2.title, "开发工程师")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_permission -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.core.employee'`）

- [ ] **Step 3: 实现 employee.py 与 permission.py**

`laoban/core/employee.py`:
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
    status: str = "active"
    permissions: dict[str, Any] = field(default_factory=lambda: {"collaboration": []})
    capabilities: dict[str, Any] = field(default_factory=lambda: {"tools": []})

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "department": self.department,
            "status": self.status,
            "permissions": self.permissions,
            "capabilities": self.capabilities,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Employee":
        return cls(
            id=d["id"],
            name=d["name"],
            title=d.get("title", ""),
            department=d.get("department", ""),
            status=d.get("status", "active"),
            permissions=d.get("permissions", {"collaboration": []}),
            capabilities=d.get("capabilities", {"tools": []}),
        )
```

`laoban/core/permission.py`:
```python
from __future__ import annotations

from .employee import Employee


class PermissionDenied(Exception):
    """越权调用。"""


def can_collaborate(from_emp: Employee, to_emp_id: str) -> bool:
    return to_emp_id in from_emp.permissions.get("collaboration", [])


def require_collaboration(from_emp: Employee, to_emp_id: str) -> None:
    if not can_collaborate(from_emp, to_emp_id):
        raise PermissionDenied(f"{from_emp.id} 无权联系 {to_emp_id}")


def can_use_tool(emp: Employee, tool: str) -> bool:
    return tool in emp.capabilities.get("tools", [])
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_permission -v`
Expected: PASS（5 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/core/employee.py laoban/core/permission.py tests/test_permission.py && git commit -m "feat: 员工模型与权限矩阵（协作+工具）"
```

---

## Task 5: JSON 原子存储

**Files:**
- Create: `laoban/core/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: 写失败测试**

`tests/test_store.py`:
```python
import tempfile
import unittest
from pathlib import Path

from laoban.core.store import JsonStore
from laoban.core.task import Task, DOING
from laoban.core.employee import Employee


class TestJsonStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = JsonStore(self.tmp)

    def test_task_roundtrip(self):
        t = Task(id="T-1", title="x", state=DOING)
        self.store.save_task(t)
        loaded = self.store.load_task("T-1")
        self.assertEqual(loaded.id, "T-1")
        self.assertEqual(loaded.state, DOING)

    def test_list_tasks(self):
        self.store.save_task(Task(id="T-1", title="a"))
        self.store.save_task(Task(id="T-2", title="b"))
        self.assertEqual(len(self.store.list_tasks()), 2)

    def test_employee_roundtrip(self):
        e = Employee(id="dev", name="阿码")
        self.store.save_employee(e)
        loaded = self.store.load_employee("dev")
        self.assertEqual(loaded.name, "阿码")

    def test_missing_task_is_none(self):
        self.assertIsNone(self.store.load_task("nope"))

    def test_no_leftover_tmp_files(self):
        self.store.save_task(Task(id="T-1", title="x"))
        leftovers = list(Path(self.tmp, "tasks").glob("*.tmp"))
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_store -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.core.store'`）

- [ ] **Step 3: 实现 store.py**

`laoban/core/store.py`:
```python
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .task import Task
from .employee import Employee


class JsonStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.tasks_dir = self.root / "tasks"
        self.employees_dir = self.root / "employees"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.employees_dir.mkdir(parents=True, exist_ok=True)

    def _atomic_write(self, path: Path, data: dict[str, Any]) -> None:
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    # ---- tasks ----
    def save_task(self, task: Task) -> None:
        self._atomic_write(self.tasks_dir / f"{task.id}.json", task.to_dict())

    def load_task(self, task_id: str) -> Task | None:
        d = self._read_json(self.tasks_dir / f"{task_id}.json")
        return Task.from_dict(d) if d else None

    def list_tasks(self) -> list[Task]:
        tasks = []
        for p in self.tasks_dir.glob("*.json"):
            d = self._read_json(p)
            if d:
                tasks.append(Task.from_dict(d))
        return tasks

    # ---- employees ----
    def save_employee(self, emp: Employee) -> None:
        self._atomic_write(self.employees_dir / f"{emp.id}.json", emp.to_dict())

    def load_employee(self, emp_id: str) -> Employee | None:
        d = self._read_json(self.employees_dir / f"{emp_id}.json")
        return Employee.from_dict(d) if d else None

    def list_employees(self) -> list[Employee]:
        emps = []
        for p in self.employees_dir.glob("*.json"):
            d = self._read_json(p)
            if d:
                emps.append(Employee.from_dict(d))
        return emps
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_store -v`
Expected: PASS（5 tests OK）

- [ ] **Step 5: Commit**

```bash
cd /workspace && git add laoban/core/store.py tests/test_store.py && git commit -m "feat: JSON 原子存储（任务+员工）"
```

---

## Task 6: CLI init 命令（Plan A 集成冒烟）

**Files:**
- Create: `laoban/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: 写失败测试**

`tests/test_cli.py`:
```python
import tempfile
import unittest
from pathlib import Path

from laoban.cli import main


class TestCliInit(unittest.TestCase):
    def test_init_creates_dirs(self):
        tmp = tempfile.mkdtemp()
        rc = main(["init", "--root", tmp])
        self.assertEqual(rc, 0)
        self.assertTrue((Path(tmp) / "tasks").exists())
        self.assertTrue((Path(tmp) / "employees").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /workspace && python -m unittest tests.test_cli -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'laoban.cli'`）

- [ ] **Step 3: 实现 cli.py**

`laoban/cli.py`:
```python
from __future__ import annotations

import argparse

from .core.store import JsonStore


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="laoban")
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="初始化公司目录")
    init.add_argument("--root", default=".laoban", help="数据目录（默认 .laoban）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "init":
        JsonStore(args.root)
        print(f"已初始化公司目录：{args.root}")
        return 0
    return 1
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /workspace && python -m unittest tests.test_cli -v`
Expected: PASS（1 test OK）

- [ ] **Step 5: 全量回归**

Run: `cd /workspace && python -m unittest discover -v`
Expected: PASS（全部测试通过，无失败）

- [ ] **Step 6: Commit**

```bash
cd /workspace && git add laoban/cli.py tests/test_cli.py && git commit -m "feat: CLI init 命令（Plan A 集成冒烟）"
```

---

## 自审记录

- **Spec 覆盖**：状态机（含驳回 ≤3 轮、WaitingHuman、Cancelled/Blocked）→ Task 3；权限矩阵（协作+工具）→ Task 4；员工最小模型 → Task 4；JSON 原子存储 → Task 5；合法转换强校验（D3）→ Task 3。
- **占位符扫描**：无 TBD/TODO，每个代码步骤含完整代码。
- **类型一致性**：`Task.id/state/review_round/flow_log`、`Employee.id/name/permissions/capabilities`、`JsonStore.save_task/load_task/list_tasks/save_employee/load_employee/list_employees`、`advance/can_transition/IllegalTransition` 在全部 Task 中命名一致。

---

## 下一步（Plan B / C 边界，待本计划执行后细化）

- **Plan B（执行引擎）**：在 `Employee` 上扩展完整人事档案字段（mission/duties/performance_goals/model_config/reports_to/autonomy_level 等，见设计文档 5.1）；新增 `laoban/llm/`（gateway + mock）、`laoban/runner/`（runner + tools + approval）、`laoban/core/dispatcher.py`、`laoban/core/scheduler.py`；MockLLM 驱动任务走完全流程。
- **Plan C（交互与交付）**：完善 CLI 子命令（hire/task/approve/status）、`dashboard/`（单文件 HTML + 标准库 HTTP）、启动模式（三元老组织设计）、双轨招聘、绩效 Ledger（含人类介入率）、合规硬规则、标准验收套件、README、CI。
