"""AI 复盘机制：验收评分 → 自动回写经验教训 → 下次执行生效。

规则（复盘永不缺席原则）：
- 老板留了评语且 score>=3：learned = 评语（老板的话就是经验，现状行为）；
- 评语为空 或 低分（score<=2）：自动复盘生成教训——
  - 有 LLM 网关且承接人是 AI：把任务要求 + 交付物 + 评分喂给该员工自己的模型，
    生成一条 ≤60 字的具体教训；
  - LLM 不可用 / 调用失败：降级为模板教训，保证复盘永远落账，不因外部依赖失效。
- 落账复用 write_back_experience 格式（memory.experiences），附加 auto 审计标记。
"""
from __future__ import annotations

from .employee import Employee
from .task import Task
from .feedback import write_back_experience

LOW_SCORE = 2        # score <= 2 视为低分，强制复盘
LESSON_MAX_LEN = 120  # 教训截断长度（防 LLM 长篇大论撑爆记忆）

_PROMPT = (
    "你是 {name}（{title}），刚完成任务《{title_task}》，人类验收评分 {score}/5。"
    "{reason}任务要求：{instruction}\n你的交付物节选：\n{snippet}\n\n"
    "请复盘输出一条不超过 60 字的具体教训：下次同类任务怎么做得更好。"
    "直接给结论，不要客套，不要复述任务。"
)


def review_and_learn(store, emp: Employee, task: Task, score: int,
                     comment: str = "", gateway=None) -> dict:
    """验收后复盘：生成/采纳教训 → 回写员工记忆，返回该条经验（含 auto 标记）。"""
    learned = (comment or "").strip()
    auto = False
    if not learned or score <= LOW_SCORE:
        learned = _generate_lesson(emp, task, score, (comment or "").strip(),
                                   gateway)
        auto = True
    write_back_experience(emp, task_type=task.title, score=score,
                          comment=learned)
    exp = emp.memory["experiences"][-1]
    exp["auto"] = auto    # 审计：这条教训是否由 AI 自动复盘生成
    return exp


def _generate_lesson(emp: Employee, task: Task, score: int,
                     comment: str, gateway) -> str:
    # 交付物：取最近一次落档的
    deliverable = ""
    for p in reversed(task.progress_log):
        d = p.get("deliverable")
        if d:
            deliverable = d
            break

    # LLM 复盘（仅 AI 员工 + 有交付物 + 有网关）
    if gateway is not None and emp.kind == "ai" and deliverable:
        try:
            from ..llm.base import Message
            msgs = [Message(role="user", content=_PROMPT.format(
                name=emp.name, title=emp.title or "员工",
                title_task=task.title, score=score,
                reason=f"人类评语：{comment}。" if comment else "人类未留评语。",
                instruction=task.instruction or task.title,
                snippet=deliverable[:800]))]
            text = gateway.chat_for_employee(emp.model_config, msgs).content.strip()
            if text:
                return text[:LESSON_MAX_LEN]
        except Exception:
            pass    # LLM 失败 → 模板降级，复盘不缺席

    # 模板教训（无 LLM / 人类员工 / LLM 失败）
    if score <= LOW_SCORE:
        return (f"《{task.title[:30]}》验收 {score}/5 未达标："
                "下次先对照任务要求逐条自查再交付。")
    return (f"《{task.title[:30]}》验收 {score}/5 通过："
            "沉淀当前做法中可复用的部分。")
