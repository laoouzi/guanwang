from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from .task import Task
from .employee import Employee


class JsonStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.tasks_dir = self.root / "tasks"
        self.employees_dir = self.root / "employees"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.employees_dir.mkdir(parents=True, exist_ok=True)
        # 员工读改写互斥：看板是 ThreadingHTTPServer（请求并发）+
        # worker 后台线程，无锁的 load→改→save 会互相覆盖（队列丢任务）
        self._emp_lock = threading.RLock()

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

    def update_employee(self, emp_id: str,
                        fn: Callable[[Employee], Any]) -> Any:
        """员工原子读改写：锁内重载最新状态 → 应用 fn → 落盘，返回 fn 结果。

        并发（看板多请求线程 + worker 线程）下直接的 load→改→save 会
        丢更新（后写覆盖先写：派单任务凭空消失/出队复活）。所有跨线程
        的员工字段变更（工位队列、经验回写）必须走此口。
        fn 内禁止 LLM/网络调用（会长时间持锁拖垮队列操作）。
        """
        with self._emp_lock:
            emp = self.load_employee(emp_id)
            if emp is None:
                raise KeyError(f"员工不存在：{emp_id}")
            result = fn(emp)
            self.save_employee(emp)
            return result

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
