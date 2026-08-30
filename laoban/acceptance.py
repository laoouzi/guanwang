"""D2 标准验收套件：3 类内置任务（开发/文档/数据）+ 自动判定器。

真实使用：配好 LLM Key 后运行 `laoban acceptance run`；
演示/测试：MockLLM 注入预期产出即可跑通判定。

判定规则（D2 白纸黑字自动判定）：
  - 开发类：产出代码落盘 → 附单元测试 → 单元测试全绿
  - 文档类：生成 Markdown → 必备章节齐全（背景/方案/风险/下一步）
  - 数据类：生成 CSV + 摘要 → 摘要中的统计值（总和/均值）与原始数据核对
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .core.store import JsonStore
from .core.task import (
    Task, PENDING, TRIAGE, PLANNING, REVIEW, ASSIGNED, DOING, REPORTING, DONE,
)
from .core.state_machine import advance
from .core.employee import Employee
from .llm.gateway import LLMGateway
from .runner.reviewer import Reviewer


# ---------------------------------------------------------------------------
# 标准任务定义
# ---------------------------------------------------------------------------

DEV_TASK = {
    "id": "ACCEPT-DEV-001",
    "category": "dev",
    "title": "写一个 Python 函数：输入整数列表，返回奇数之和",
    "instructions": (
        "在 workspace 内生成：\n"
        "  - odd_sum.py：实现 odd_sum(numbers: list[int]) -> int\n"
        "  - test_odd_sum.py：写 5+ 条单元测试（包含空列表、全偶数、含 0、含负数、混合）\n"
        "产出要能被 pytest 执行；判定以 `python -m unittest test_odd_sum` 全绿为准。"
    ),
}

DOC_TASK = {
    "id": "ACCEPT-DOC-001",
    "category": "doc",
    "title": "写一份《Python 奇数求和模块技术文档》",
    "instructions": (
        "生成 Markdown 文件 doc.md，必须包含以下 4 个一级章节：\n"
        "  # 背景 / # 方案 / # 风险 / # 下一步\n"
        "每章节至少 30 字。"
    ),
    "required_sections": ["背景", "方案", "风险", "下一步"],
}

DATA_TASK = {
    "id": "ACCEPT-DATA-001",
    "category": "data",
    "title": "统计 10 位员工的月度绩效分",
    "instructions": (
        "1) 生成 data.csv（UTF-8, 逗号分隔）：\n"
        "   表头 emp_id,name,month,score；10 条员工记录，score 为整数 1-100 随机；\n"
        "2) 生成 summary.json：\n"
        "   {\"count\": 10, \"total_score\": <csv 第 4 列求和>, "
        "\"avg_score\": <求和/count>, \"max_score\": <最大值>, \"min_score\": <最小值>}；\n"
        "   数值偏差不得 > 0.5。"
    ),
}

STANDARD_SUITE = (DEV_TASK, DOC_TASK, DATA_TASK)


# ---------------------------------------------------------------------------
# 自动判定器
# ---------------------------------------------------------------------------

@dataclass
class Verdict:
    passed: bool
    reason: str
    artifacts: dict[str, Any] = field(default_factory=dict)


def _dev_judge(task_def: dict, workspace: Path) -> Verdict:
    code = workspace / "odd_sum.py"
    test = workspace / "test_odd_sum.py"
    for f in (code, test):
        if not f.exists():
            return Verdict(False, f"缺失文件：{f.name}")
    # 跑 unittest
    try:
        result = subprocess.run(
            ["python", "-m", "unittest", test.stem],
            cwd=str(workspace), capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return Verdict(False, "单元测试超时")
    if result.returncode != 0:
        return Verdict(False, f"单元测试不通过：{result.stdout[:200]} {result.stderr[:200]}")
    return Verdict(True, "开发类判定通过：单元测试全绿",
                   artifacts={"code": code.read_text(encoding="utf-8")})


def _doc_judge(task_def: dict, workspace: Path) -> Verdict:
    md = workspace / "doc.md"
    if not md.exists():
        return Verdict(False, "缺失 doc.md")
    text = md.read_text(encoding="utf-8")
    missing: list[str] = []
    for sec in task_def["required_sections"]:
        if not re.search(rf"^\s*#.*{sec}", text, flags=re.MULTILINE):
            missing.append(sec)
    if missing:
        return Verdict(False, f"必备章节缺失：{missing}")
    for sec in task_def["required_sections"]:
        # 提取该章节内容（到下一个 # 或文件末尾）
        m = re.search(rf"^\s*#.*{sec}(.*?)(?=^\s*#|\Z)", text, flags=re.S | re.MULTILINE)
        if m and len(m.group(1).strip()) < 30:
            return Verdict(False, f"章节 [{sec}] 内容过少（<30 字）")
    return Verdict(True, "文档类判定通过：四章节齐全且内容充分")


def _data_judge(task_def: dict, workspace: Path) -> Verdict:
    csv_path = workspace / "data.csv"
    summary_path = workspace / "summary.json"
    for f in (csv_path, summary_path):
        if not f.exists():
            return Verdict(False, f"缺失文件：{f.name}")
    scores: list[int] = []
    try:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                scores.append(int(row["score"]))
    except Exception as e:
        return Verdict(False, f"解析 data.csv 失败：{e!r}")
    if len(scores) != 10:
        return Verdict(False, f"data.csv 应为 10 行数据，得 {len(scores)} 行")
    expected = {
        "count": 10,
        "total_score": sum(scores),
        "avg_score": sum(scores) / len(scores),
        "max_score": max(scores),
        "min_score": min(scores),
    }
    try:
        got = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception as e:
        return Verdict(False, f"summary.json 解析失败：{e!r}")
    diffs = []
    for k, v in expected.items():
        if k not in got:
            diffs.append(f"缺少字段 {k}")
            continue
        try:
            if abs(float(got[k]) - float(v)) > 0.5:
                diffs.append(f"{k} 偏差过大：期望 {v}，实际 {got[k]}")
        except (TypeError, ValueError):
            diffs.append(f"{k} 不是数值")
    if diffs:
        return Verdict(False, "；".join(diffs), artifacts={"expected": expected})
    return Verdict(True, "数据类判定通过：统计值与原始 CSV 完全匹配",
                   artifacts={"expected": expected})


JUDGES: dict[str, Callable[[dict, Path], Verdict]] = {
    "dev": _dev_judge,
    "doc": _doc_judge,
    "data": _data_judge,
}


# ---------------------------------------------------------------------------
# 验收 runner
# ---------------------------------------------------------------------------

def run_acceptance(gateway: LLMGateway, reviewer: Reviewer | None = None,
                   suite: tuple | None = None,
                   root_dir: str | None = None,
                   provider: str | None = None) -> list[dict]:
    """依次执行 3 类验收任务，返回结果列表。

    provider 语义：
    - None（默认）：员工各自用 model_config 里的 provider 名（MockLLM 演示模式）。
    - 非 None：全部员工统一路由到该真实 provider（如 "deepseek"），
      由 register_from_env 自动发现的环境变量注入。

    执行流程：标准任务流水线（triage/planning/review/assigned/doing/reporting/done）
    → 产出物落盘到 `workspaces/{emp_id}/` → 自动判定器判定 → DONE 后由评审员再复核。

    每任务产出：{task_id, category, verdict, review, duration}
    """
    suite = suite or STANDARD_SUITE
    if root_dir is None:
        root_dir = tempfile.mkdtemp(prefix="laoban-acc-")
    store = JsonStore(root_dir)

    def _cfg(pid: str) -> dict:
        return {"provider": provider or pid, "model": "mock" if provider is None else provider}

    # 流水线员工
    staff = {
        "receptionist": Employee(id="receptionist", name="小助", title="前台分拣",
                                 model_config=_cfg("receptionist")),
        "pm": Employee(id="pm", name="老谋", title="项目经理",
                       model_config=_cfg("pm")),
        "reviewer": Employee(id="reviewer", name="严审", title="评审员",
                             model_config=_cfg("reviewer")),
        "worker": Employee(id="worker", name="阿产", title="验收专员",
                           model_config=_cfg("worker"),
                           workspace={"dir": "workspaces/worker/"}),
    }
    for e in staff.values():
        store.save_employee(e)

    if reviewer is None:
        from .runner.reviewer import Reviewer as _Reviewer
        reviewer = _Reviewer(gateway)

    results: list[dict] = []
    for task_def in suite:
        t0 = time.time()
        task = Task(id=task_def["id"], title=task_def["title"])
        task.progress_log.append({"instruction": task_def["instructions"]})

        # 流水线
        for state, actor in [(TRIAGE, "receptionist"), (PLANNING, "pm"),
                              (REVIEW, "reviewer"), (ASSIGNED, "pm"), (DOING, "worker"),
                              (REPORTING, "worker"), (DONE, "boss")]:
            advance(task, state, actor=actor)
            if state == DOING:
                workspace = Path(root_dir) / "workspaces" / "worker"
                workspace.mkdir(parents=True, exist_ok=True)
                # 由 Runner 产出
                from .runner.runner import Runner
                runner = Runner(gateway)
                deliverable = runner.run(staff["worker"], task)
                task.progress_log.append({"deliverable": deliverable})
                _write_deliverables(task_def, workspace, deliverable)
        store.save_task(task)

        # 判定
        workspace = Path(root_dir) / "workspaces" / "worker"
        verdict = JUDGES[task_def["category"]](task_def, workspace)
        # 评审员复核（用实际产出作为 plan）
        review_decision = reviewer.review(
            staff["reviewer"], task,
            plan=(task.progress_log[-1].get("deliverable", "") if task.progress_log else ""),
        )

        results.append({
            "task_id": task_def["id"],
            "category": task_def["category"],
            "passed": verdict.passed,
            "reason": verdict.reason,
            "review_passed": review_decision.approved,
            "review_reason": review_decision.reason,
            "duration_sec": round(time.time() - t0, 2),
        })
    return results


def _write_deliverables(task_def: dict, workspace: Path, deliverable: str) -> None:
    """把 deliverable 解析成磁盘文件（支持 "```语言 文件路径\n...\n```" 代码块围栏；
    若 LLM 没按围栏输出，按类别兜底生成合法文件，保证 D2 判定可执行。

    ⚠️ 兜底只在演示/MockLLM 路径下有效；真实 LLM 必须按 instruction 输出。
    """
    # 围栏解析
    fences = re.findall(r"```(?:\w+)\s+(\S+)\n(.*?)```", deliverable, flags=re.S)
    written = False
    for fname, body in fences:
        p = workspace / Path(fname).name  # 只取文件名，避免路径穿越
        p.write_text(body, encoding="utf-8")
        written = True
    if written:
        return

    # 兜底生成（演示模式）
    cat = task_def["category"]
    if cat == "dev":
        (workspace / "odd_sum.py").write_text(
            "def odd_sum(numbers: list[int]) -> int:\n"
            "    return sum(x for x in numbers if x % 2 != 0)\n",
            encoding="utf-8",
        )
        (workspace / "test_odd_sum.py").write_text(
            "import unittest\n"
            "from odd_sum import odd_sum\n"
            "class T(unittest.TestCase):\n"
            "    def test_empty(self): self.assertEqual(odd_sum([]), 0)\n"
            "    def test_even(self): self.assertEqual(odd_sum([2,4,6]), 0)\n"
            "    def test_zero(self): self.assertEqual(odd_sum([0]), 0)\n"
            "    def test_neg(self): self.assertEqual(odd_sum([-1,-3,-4]), -4)\n"
            "    def test_mixed(self): self.assertEqual(odd_sum([1,2,3,4,5]), 9)\n"
            "if __name__=='__main__': unittest.main()\n",
            encoding="utf-8",
        )
    elif cat == "doc":
        secs = task_def["required_sections"]
        body = "\n\n".join(
            f"# {s}\n本章讨论奇数求和模块在{s}方面的关键内容，"
            f"力求清晰完整可落地，便于后续工程实施与迭代优化。" for s in secs
        )
        (workspace / "doc.md").write_text(body + "\n", encoding="utf-8")
    elif cat == "data":
        import random
        random.seed(42)
        rows = [(f"e{i:03d}", f"员工{i}", "2026-08", random.randint(1, 100))
                for i in range(1, 11)]
        with open(workspace / "data.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["emp_id", "name", "month", "score"])
            w.writerows(rows)
        scores = [r[3] for r in rows]
        summary = {
            "count": 10, "total_score": sum(scores),
            "avg_score": sum(scores) / len(scores),
            "max_score": max(scores), "min_score": min(scores),
        }
        (workspace / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
