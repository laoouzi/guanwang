import tempfile
import unittest

from laoban.core.employee import Employee
from laoban.core.state_machine import IllegalTransition
from laoban.core.store import JsonStore
from laoban.core.task import Task, TRIAGE, PLANNING, REVIEW
from laoban.core.workstation import enqueue, dequeue, queue_of, assign_task


def _store_with_dev():
    root = tempfile.mkdtemp()
    st = JsonStore(root)
    st.save_employee(Employee(id="dev", name="阿码"))
    return st


class TestWorkstation(unittest.TestCase):
    def test_enqueue_and_queue_of(self):
        st = _store_with_dev()
        enqueue(st, "dev", "T-1")
        enqueue(st, "dev", "T-2")
        self.assertEqual(queue_of(st, "dev"), ["T-1", "T-2"])
        # 重复入队幂等
        enqueue(st, "dev", "T-1")
        self.assertEqual(queue_of(st, "dev"), ["T-1", "T-2"])

    def test_dequeue(self):
        st = _store_with_dev()
        enqueue(st, "dev", "T-1")
        enqueue(st, "dev", "T-2")
        dequeue(st, "dev", "T-1")
        self.assertEqual(queue_of(st, "dev"), ["T-2"])
        # 出队不存在的任务 = 无操作
        dequeue(st, "dev", "T-404")
        self.assertEqual(queue_of(st, "dev"), ["T-2"])

    def test_enqueue_unknown_employee(self):
        st = _store_with_dev()
        with self.assertRaises(KeyError):
            enqueue(st, "ghost", "T-1")

    def test_enqueue_suspended_employee(self):
        from laoban.core.lifecycle import suspend_employee
        st = _store_with_dev()
        suspend_employee(st, "dev")
        with self.assertRaises(ValueError):
            enqueue(st, "dev", "T-1")

    def test_concurrent_enqueue_loses_nothing(self):
        """并发读改写不丢更新：看板多请求线程同时派单，任务一个不丢。

        无锁的 load→改→save 后写覆盖先写（派单任务凭空消失）；
        update_employee 锁内串行化后 8 线程并发全部落账。
        """
        import threading
        st = _store_with_dev()
        tids = [f"T-{i}" for i in range(8)]
        threads = [threading.Thread(target=enqueue, args=(st, "dev", tid))
                   for tid in tids]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        q = queue_of(st, "dev")
        self.assertEqual(sorted(q), sorted(tids))   # 8 件全在，顺序不定

    def test_concurrent_enqueue_and_dequeue(self):
        """派单（入队）与验收（出队）并发：结果可解释，无凭空消失/复活。"""
        import threading
        st = _store_with_dev()
        enqueue(st, "dev", "T-base")
        errs = []

        def _enq():
            try:
                enqueue(st, "dev", "T-new")
            except Exception as e:
                errs.append(e)

        def _deq():
            try:
                dequeue(st, "dev", "T-base")
            except Exception as e:
                errs.append(e)

        a = threading.Thread(target=_enq)
        b = threading.Thread(target=_deq)
        a.start(); b.start(); a.join(); b.join()
        self.assertEqual(errs, [])
        # 出队成功 + 新任务在（旧代码这里 T-new 会随机消失）
        self.assertEqual(queue_of(st, "dev"), ["T-new"])


class TestAssignTask(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = JsonStore(self.root)
        self.store.save_employee(Employee(id="dev", name="阿码"))
        # 走到 review 状态，等待派发
        task = Task(id="T-1", title="写函数")
        from laoban.core.state_machine import advance
        advance(task, TRIAGE, actor="receptionist")
        advance(task, PLANNING, actor="pm")
        advance(task, REVIEW, actor="reviewer")
        self.store.save_task(task)

    def test_assign_advances_and_enqueues(self):
        task = assign_task(self.store, "T-1", "dev", actor="pm")
        self.assertEqual(task.state, "assigned")
        self.assertEqual(queue_of(self.store, "dev"), ["T-1"])

    def test_assign_to_terminated(self):
        from laoban.core.lifecycle import terminate_employee
        terminate_employee(self.store, "dev")
        with self.assertRaises(ValueError):
            assign_task(self.store, "T-1", "dev")

    def test_assign_unknown_task(self):
        with self.assertRaises(KeyError):
            assign_task(self.store, "T-404", "dev")

    def test_assign_illegal_state(self):
        # pending 状态不可直接派发（需先走 triage/planning/review）
        self.store.save_task(Task(id="T-2", title="还没分拣"))
        with self.assertRaises(IllegalTransition):
            assign_task(self.store, "T-2", "dev")


class TestWorkstationCli(unittest.TestCase):
    def test_cli_assign_and_queue(self):
        from laoban.cli import main
        from laoban.core.state_machine import advance
        with tempfile.TemporaryDirectory() as d:
            root = str(d)
            st = JsonStore(root)
            main(["hire", "--root", root, "--name", "阿码", "--id", "dev"])
            task = Task(id="T-9", title="x")
            advance(task, TRIAGE, actor="r")
            advance(task, PLANNING, actor="p")
            advance(task, REVIEW, actor="v")
            st.save_task(task)
            self.assertEqual(main(["task", "assign", "--root", root,
                                   "--id", "T-9", "--to", "dev"]), 0)
            self.assertEqual(queue_of(JsonStore(root), "dev"), ["T-9"])
            self.assertEqual(main(["queue", "--root", root, "--who", "dev"]), 0)


if __name__ == "__main__":
    unittest.main()
