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
