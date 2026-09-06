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
