"""视频（content）的删除与影响面预览。

**为什么需要单独一个模块**：SQLite 外键**没有声明 `ON DELETE CASCADE`**，
而且 `evidence` 同时挂在 `prediction_id` 与 `content_id` 上 —— 直接
`DELETE FROM content` 会被外键挡住（`FOREIGN KEY constraint failed`）。
必须**按依赖顺序**逐层清理，且全部在**同一个事务**里完成（避免删一半留下脏数据）。

依赖顺序（叶子 → 根）::

    evidence ──┐
    verification ──┤
    prediction ──┼──> content
    claim ───────┤
    content_summary ─┤
    transcript ──┘

`ingest_task_item.content_id` 只是**弱引用**（无外键），删除时置 `NULL`，
保留任务与尝试历史 —— 任务记录属于"操作日志"，不该因为作品被删而消失。
"""
from __future__ import annotations

import threading

from ..db import get_conn

_WRITE_LOCK = threading.Lock()

# 各类下游数据的统计 SQL（`evidences` 需两个占位符）
_COUNT_SQL = {
    "predictions": ("SELECT COUNT(*) FROM prediction WHERE content_id=?", 1),
    "claims": ("SELECT COUNT(*) FROM claim WHERE content_id=?", 1),
    "evidences": (
        "SELECT COUNT(*) FROM evidence WHERE content_id=? "
        "OR prediction_id IN (SELECT id FROM prediction WHERE content_id=?)",
        2,
    ),
    "verifications": (
        "SELECT COUNT(*) FROM verification WHERE prediction_id IN "
        "(SELECT id FROM prediction WHERE content_id=?)",
        1,
    ),
    "summaries": ("SELECT COUNT(*) FROM content_summary WHERE content_id=?", 1),
    "transcripts": ("SELECT COUNT(*) FROM transcript WHERE content_id=?", 1),
}

# 删除顺序：叶子 → 根。每项 (统计键, SQL, 占位符个数)
_DELETE_STEPS = (
    ("evidences",
     "DELETE FROM evidence WHERE content_id=? "
     "OR prediction_id IN (SELECT id FROM prediction WHERE content_id=?)", 2),
    ("verifications",
     "DELETE FROM verification WHERE prediction_id IN "
     "(SELECT id FROM prediction WHERE content_id=?)", 1),
    # 自引用 parent_prediction_id 需先断开，否则同批删除可能触发外键冲突
    ("predictions", "UPDATE prediction SET parent_prediction_id=NULL WHERE content_id=?", 1),
    ("predictions", "DELETE FROM prediction WHERE content_id=?", 1),
    ("claims", "DELETE FROM claim WHERE content_id=?", 1),
    ("summaries", "DELETE FROM content_summary WHERE content_id=?", 1),
    ("transcripts", "DELETE FROM transcript WHERE content_id=?", 1),
)


def _params(sql_params: int, content_id: str) -> tuple:
    return (content_id,) * sql_params


def preview_delete(content_id: str, conn=None) -> dict | None:
    """预览删除影响面（不修改数据）。视频不存在时返回 `None`。

    前端在二次确认弹窗里展示这些数字，避免误删带走预测数据。
    """
    own_conn = conn is None
    connection = conn or get_conn()
    try:
        row = connection.execute(
            "SELECT id, title, platform, creator_id FROM content WHERE id=?", (content_id,)
        ).fetchone()
        if not row:
            return None
        counts = {
            key: connection.execute(sql, _params(n, content_id)).fetchone()[0]
            for key, (sql, n) in _COUNT_SQL.items()
        }
        return {
            "content_id": content_id,
            "title": row["title"],
            "platform": row["platform"],
            **counts,
        }
    finally:
        if own_conn:
            connection.close()


def delete_content(content_id: str, conn=None) -> dict | None:
    """删除视频及其全部下游数据（单事务）。视频不存在时返回 `None`。

    返回各表的删除条数，便于前端提示与测试断言。
    """
    own_conn = conn is None
    with _WRITE_LOCK:
        connection = conn or get_conn()
        try:
            exists = connection.execute(
                "SELECT 1 FROM content WHERE id=?", (content_id,)
            ).fetchone()
            if not exists:
                return None

            # 先统计（COUNT 的 rowcount 无意义，必须取值）
            deleted = {
                key: connection.execute(sql, _params(n, content_id)).fetchone()[0]
                for key, (sql, n) in _COUNT_SQL.items()
            }
            # 明细只断开引用，保留任务与尝试历史
            connection.execute(
                "UPDATE ingest_task_item SET content_id=NULL WHERE content_id=?", (content_id,)
            )
            for key, sql, n in _DELETE_STEPS:
                connection.execute(sql, _params(n, content_id))
            connection.execute("DELETE FROM content WHERE id=?", (content_id,))

            if own_conn:
                connection.commit()
        except Exception:
            if own_conn:
                connection.rollback()
            raise
        finally:
            if own_conn:
                connection.close()

    return {"content_id": content_id, "deleted": deleted}
