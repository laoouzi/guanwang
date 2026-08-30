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
