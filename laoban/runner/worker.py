"""自动运转引擎（P2）：派单后 AI 自动执行 → 汇报，人只在验收/审批介入。

WorkerLoop 每轮 tick：
1. 扫全部在职 AI 员工的工位队列；
2. 状态 assigned 的任务：推进 doing → Runner 执行（含 [TOOL] 协作循环）
   → 推进 reporting（交付物落 progress_log）；
3. 任务停在 reporting 等人类验收（看板驾驶舱一键验收）。

边界与失败处理：
- 仅 AI 员工自动执行；人类员工的任务保持 assigned（走人类待办/线下完成）；
- LLM 调用失败 → 任务转 blocked（终态，block_reason 落原因），老板看板上
  可见并可重新派单（assign_task_auto 从 blocked 不行——终态。重新提交或人工处理）；
- 幂等：非 assigned 状态（doing/reporting/…）跳过，重复 tick 无副作用。
"""
from __future__ import annotations

import threading
import time

from ..core.employee import Employee
from ..core.store import JsonStore
from ..core.task import Task, ASSIGNED, DOING, REPORTING, BLOCKED
from ..core.state_machine import advance, IllegalTransition
from ..core.ledger import FileLedger
from ..llm.gateway import LLMGateway
from .runner import Runner


class WorkerLoop:
    """后台自动执行循环：线程跑 run_forever，测试可直接调 tick()。"""

    def __init__(self, store: JsonStore, gateway: LLMGateway,
                 ledger: FileLedger | None = None, interval: float = 2.0):
        self.store = store
        self.gateway = gateway
        self.runner = Runner(gateway, store=store)
        self.ledger = ledger or FileLedger(store)
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_results: list[dict] = []   # 最近一轮执行摘要（审计/调试）

    # ---- 主循环 ----
    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:   # 循环兜底：单轮异常不杀线程
                print(f"[worker] tick 异常：{e!r}")
            self._stop.wait(self.interval)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run_forever,
                                        daemon=True, name="laoban-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ---- 单轮 ----
    def tick(self) -> list[dict]:
        """扫一遍全部 AI 员工队列，执行 assigned 任务。返回执行摘要。"""
        results: list[dict] = []
        for emp in self.store.list_employees():
            if emp.kind != "ai" or emp.status != "active":
                continue
            for task_id in list(emp.workspace.get("queue", [])):
                task = self.store.load_task(task_id)
                if not task or task.state != ASSIGNED:
                    continue
                results.append(self._execute(emp, task))
        self.last_results = results
        return results

    def _execute(self, emp: Employee, task: Task) -> dict:
        """单个任务：assigned → doing → Runner → reporting（或 blocked）。"""
        summary = {"task_id": task.id, "employee": emp.id}
        try:
            advance(task, DOING, actor=emp.id, remark="开工（自动）")
            self.store.save_task(task)
            self.ledger.record_step(emp.id)
        except IllegalTransition as e:
            summary["result"] = f"跳过：{e}"
            return summary

        started = time.time()
        try:
            deliverable = self.runner.run(emp, task)
        except Exception as e:   # LLM/网络等一切执行失败 → blocked（终态可见）
            task.block_reason = f"自动执行失败：{e}"
            advance(task, BLOCKED, actor="worker",
                    remark=task.block_reason)
            self.store.save_task(task)
            summary["result"] = f"blocked：{e}"
            print(f"[worker] {task.id} 执行失败转 blocked：{e!r}")
            return summary

        # 成本核算：token 用量（读后清零）× 员工单价 → 验收时入账
        usage = self.gateway.take_usage(
            emp.model_config.get("provider", "mock"))
        from ..core.points import accept_cost
        task.progress_log.append({
            "deliverable": deliverable,
            "by": emp.id,
            "at": task.updated_at,
            "elapsed": round(time.time() - started, 1),
            "usage_tokens": usage,
            "cost": round(accept_cost(emp, elapsed_sec=time.time() - started,
                                      usage_tokens=usage), 6),
        })
        advance(task, REPORTING, actor=emp.id,
                remark=f"交付（自动，{len(deliverable)} 字）")
        self.store.save_task(task)
        summary["result"] = "reporting"
        return summary
