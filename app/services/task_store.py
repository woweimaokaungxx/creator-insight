"""V0.13 任务持久化存储。

契约见 `docs/22-任务与状态契约.md` / `docs/23-任务恢复与重试矩阵.md`。

把此前仅存在于进程内存（`app/main.py` 的 `_INGEST_TASKS`）的 ingest 任务落到
SQLite 三表：`ingest_task` / `ingest_task_item` / `ingest_attempt`。

职责：

- 任务与明细读写（对外结构保持与旧内存实现**兼容**，前端无需改字段名）
- 状态机守卫：吸收态不可降级（不变量 #3）
- 错误三分类：`retryable` / `non_retryable` / `needs_action`（docs/23 §1）
- 批次状态由明细**汇总**（不变量 #4），不得反向覆盖
- 启动断点恢复：`running -> queued`（docs/23 §5）
- 待处理集合过滤（docs/22 §6）

本模块只提供纯函数，不持有可变全局状态，可被任意模块安全导入。
"""
from __future__ import annotations

import json
import threading
import time

from ..db import get_conn

_WRITE_LOCK = threading.Lock()

# 吸收态：不可被后续失败降级（docs/22 不变量 #3 / §5）
TASK_ABSORBING = {"success"}
ITEM_ABSORBING = {"completed"}

# 视为「占用中」的单条状态：新批次默认跳过（docs/22 不变量 #6）
ITEM_BUSY = {"queued", "running"}

# 任务处于「进行中」的状态（供前端与恢复逻辑使用）
TASK_ACTIVE = {"queued", "running", "pausing", "paused", "interrupted_recoverable"}

# ─── 自动重试策略（docs/23 §3）──────────────────────────────
# 单条自动重试上限：**仅 `retryable` 计入**；`needs_action` 一律不自动重试。
MAX_AUTO_ATTEMPTS = 3
# 指数退避：第 1 / 2 / 3 次自动重试前分别等待 5s / 20s / 60s
RETRY_BACKOFF_SECONDS = (5, 20, 60)


# ─── 错误分类（docs/23 §1）──────────────────────────────────
ERROR_RETRYABLE = "retryable"
ERROR_NON_RETRYABLE = "non_retryable"
ERROR_NEEDS_ACTION = "needs_action"

# 需人工处理：不重试、不切换通道绕过（docs/23 §7）
_NEEDS_ACTION_HINTS = (
    "登录", "验证码", "风控", "限流", "配额", "已达每日 ai 调用上限", "daily_call_limit",
    "403", "429", "需要人工",
)
# 确定性失败：重试无意义
_NON_RETRYABLE_HINTS = (
    "不存在", "已删除", "私密", "404", "链接失效", "未获取到字幕",
    "审核不通过", "无法识别平台", "输入不能为空",
)
# 临时性故障：可有限重试
_RETRYABLE_HINTS = (
    "timeout", "timed out", "超时", "connection", "连接", "网络", "中断",
    "502", "503", "504", "temporarily", "请稍后重试",
)


def classify_error(exc: object) -> str:
    """把异常或错误文本归入三类之一（优先级：needs_action > non_retryable > retryable）"""
    msg = (str(exc) or "").lower()
    for hint in _NEEDS_ACTION_HINTS:
        if hint.lower() in msg:
            return ERROR_NEEDS_ACTION
    for hint in _NON_RETRYABLE_HINTS:
        if hint.lower() in msg:
            return ERROR_NON_RETRYABLE
    for hint in _RETRYABLE_HINTS:
        if hint.lower() in msg:
            return ERROR_RETRYABLE
    # 未命中任何特征时按可重试处理（有限次数兜底，避免直接放弃）
    return ERROR_RETRYABLE


# ─── 任务 ───────────────────────────────────────────────────
def create_task(task_id: str, mode: str = "single", platform: str = "",
                raw_input: str = "") -> str:
    """创建任务（queued）"""
    now = int(time.time())
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO ingest_task (id, mode, platform, raw_input, status, "
                "stage, stage_progress, progress, message, total, succeeded, failed_count, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, mode, platform, raw_input, "queued", "parse", 0.0, 0.0,
                 "排队中", 0, 0, 0, now),
            )
            conn.commit()
        finally:
            conn.close()
    return task_id


def set_task_status(task_id: str, status: str | None = None, **fields) -> None:
    """更新任务状态与字段（含吸收态守卫）。``status=None`` 表示只更新字段。

    可传字段：stage / stage_progress / progress / message / error / result /
              total / succeeded / failed_count / platform / mode / started_at / finished_at
    """
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            row = conn.execute("SELECT status FROM ingest_task WHERE id=?", (task_id,)).fetchone()
            if not row:
                return
            current = row["status"]
            if status is not None and current in TASK_ABSORBING and status != current:
                # 不变量 #3：已完成的任务不得被降级
                print(f"[task_store] 拒绝任务降级 {task_id}: {current} -> {status}")
                return
            sets, params = [], []
            if status is not None:
                sets.append("status=?")
                params.append(status)
            for key in ("stage", "stage_progress", "progress", "message", "error",
                        "total", "succeeded", "failed_count", "platform", "mode",
                        "started_at", "finished_at"):
                if key in fields:
                    sets.append(f"{key}=?")
                    params.append(fields[key])
            if "result" in fields:
                sets.append("result_json=?")
                params.append(json.dumps(fields["result"], ensure_ascii=False)
                              if fields["result"] is not None else None)
            if not sets:
                return
            params.append(task_id)
            conn.execute(f"UPDATE ingest_task SET {', '.join(sets)} WHERE id=?", params)
            conn.commit()
        finally:
            conn.close()


def summarize_task(task_id: str) -> dict:
    """按明细汇总批次状态与计数（不变量 #4：只从明细 → 任务，不反向覆盖）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM ingest_task_item WHERE task_id=? GROUP BY status",
            (task_id,),
        ).fetchall()
        task = conn.execute("SELECT mode, status FROM ingest_task WHERE id=?", (task_id,)).fetchone()
    finally:
        conn.close()

    counts = {r["status"]: r["n"] for r in rows}
    total = sum(counts.values())
    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    running = counts.get("running", 0)
    queued = counts.get("queued", 0)

    if total == 0:
        status = task["status"] if task else "queued"
    elif running or queued:
        status = "running" if running else "queued"
    elif failed == 0:
        status = "success"
    elif completed == 0:
        status = "failed"
    else:
        status = "partial"

    return {
        "status": status, "total": total, "succeeded": completed,
        "failed_count": failed, "running": running, "queued": queued,
    }


# ─── 明细 ───────────────────────────────────────────────────
def add_items(task_id: str, items: list[dict]) -> None:
    """批量写入明细。items: [{content_id, platform_vid, title}]"""
    if not items:
        return
    now = int(time.time())
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            for it in items:
                conn.execute(
                    "INSERT OR IGNORE INTO ingest_task_item "
                    "(task_id, content_id, platform_vid, title, status, stage, stage_cn, "
                    "attempt_count, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (task_id, it.get("content_id"), it.get("platform_vid"),
                     it.get("title") or "", "queued", "pending", "等待中", 0, now, now),
                )
            conn.commit()
        finally:
            conn.close()


def update_item(item_id: int, **fields) -> None:
    """更新单条明细（含吸收态守卫）。

    可传字段：status / stage / stage_cn / error / error_class / content_id / title /
              attempt_count（可传 inc_attempt=True 自增）/
              auto_retry_count（可传 inc_auto_retry=True 自增，仅自动重试使用）
    """
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            row = conn.execute("SELECT status FROM ingest_task_item WHERE id=?", (item_id,)).fetchone()
            if not row:
                return
            target = fields.get("status")
            if row["status"] in ITEM_ABSORBING and target and target != row["status"]:
                # 不变量 #3：已完成的作品状态不得被后续失败降级
                print(f"[task_store] 拒绝明细降级 item={item_id}: {row['status']} -> {target}")
                fields.pop("status", None)
            sets, params = [], []
            for key in ("status", "stage", "stage_cn", "error", "error_class",
                        "content_id", "title", "auto_retry_count"):
                if key in fields:
                    sets.append(f"{key}=?")
                    params.append(fields[key])
            if fields.get("inc_attempt"):
                sets.append("attempt_count=attempt_count+1")
            elif "attempt_count" in fields:
                sets.append("attempt_count=?")
                params.append(fields["attempt_count"])
            if fields.get("inc_auto_retry"):
                sets.append("auto_retry_count=auto_retry_count+1")
            sets.append("updated_at=?")
            params.append(int(time.time()))
            params.append(item_id)
            conn.execute(f"UPDATE ingest_task_item SET {', '.join(sets)} WHERE id=?", params)
            conn.commit()
        finally:
            conn.close()


def list_items(task_id: str) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM ingest_task_item WHERE task_id=? ORDER BY id", (task_id,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def find_item(item_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM ingest_task_item WHERE id=?", (item_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


# ─── 尝试记录（docs/22 §9.3）────────────────────────────────
def start_attempt(task_id: str, item_id: int | None, strategy: str = "") -> int:
    """开始一次尝试，返回 attempt_id"""
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO ingest_attempt (task_id, item_id, strategy, status, started_at) "
                "VALUES (?,?,?,?,?)",
                (task_id, item_id, strategy, "running", int(time.time())),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        finally:
            conn.close()


def finish_attempt(attempt_id: int, status: str, error: str = "",
                   error_class: str = "", artifact_paths: list[str] | None = None) -> None:
    if not attempt_id:
        return
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE ingest_attempt SET status=?, error=?, error_class=?, "
                "artifact_paths_json=?, finished_at=? WHERE id=?",
                (status, error[:500] if error else None, error_class or None,
                 json.dumps(artifact_paths or [], ensure_ascii=False),
                 int(time.time()), attempt_id),
            )
            conn.commit()
        finally:
            conn.close()


# ─── 查询（兼容旧内存实现结构）──────────────────────────────
def _task_row_to_dict(row, items: list[dict], counts: dict) -> dict:
    """把表记录转成与旧 `_INGEST_TASKS` 条目兼容的结构"""
    done = counts.get("completed", 0) + counts.get("failed", 0)
    return {
        "id": row["id"],
        "status": row["status"],
        "stage": row["stage"] or "",
        "stage_progress": row["stage_progress"] or 0.0,
        "progress": row["progress"] or 0.0,
        "message": row["message"] or "",
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "mode": row["mode"],
        "total": row["total"] or 0,
        "current_index": done,
        "platform": row["platform"] or "",
        "raw_input": row["raw_input"] or "",
        # V0.13 新增（只增不改）
        "succeeded": counts.get("completed", 0),
        "failed_count": counts.get("failed", 0),
        "resumable": row["status"] in {"paused", "waiting_for_action", "partial", "failed"},
        "retryable_count": counts.get("retryable", 0),
        "items": items,
    }


def _items_to_public(task_id: str) -> tuple[list[dict], dict]:
    rows = list_items(task_id)
    public, counts = [], {}
    for i, r in enumerate(rows, start=1):
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if r["error_class"] == ERROR_RETRYABLE and r["status"] == "failed":
            counts["retryable"] = counts.get("retryable", 0) + 1
        public.append({
            "index": i,
            "item_id": r["id"],
            "title": r["title"] or "",
            "stage": r["stage"] or "",
            "stage_cn": r["stage_cn"] or "",
            "status": r["status"],
            "error": r["error"],
            "error_class": r["error_class"],
            "attempt_count": r["attempt_count"] or 0,
        })
    return public, counts


def get_task(task_id: str) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM ingest_task WHERE id=?", (task_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    items, counts = _items_to_public(task_id)
    return _task_row_to_dict(row, items, counts)


def list_tasks(limit: int = 10) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM ingest_task ORDER BY created_at DESC LIMIT ?", (int(limit),)
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        items, counts = _items_to_public(row["id"])
        out.append(_task_row_to_dict(row, items, counts))
    return out


# ─── 断点恢复（docs/23 §5）──────────────────────────────────
def recover_interrupted() -> dict:
    """服务启动时把中断的 running 明细退回 queued（不计业务重试，幂等）"""
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            items = conn.execute(
                "UPDATE ingest_task_item SET status='queued', stage='pending', stage_cn='等待恢复', "
                "updated_at=? WHERE status='running'",
                (int(time.time()),),
            ).rowcount or 0
            tasks = conn.execute(
                "UPDATE ingest_task SET status='queued', message='服务重启，已恢复待处理' "
                "WHERE status IN ('running','interrupted_recoverable')"
            ).rowcount or 0
            # 单条已跑完但批次未收尾的，按明细汇总补正
            conn.commit()
        finally:
            conn.close()
    if items or tasks:
        print(f"[task_store] 断点恢复：明细 {items} 条 → queued，任务 {tasks} 个 → queued")
    return {"items_requeued": items, "tasks_requeued": tasks}


# ─── 待处理集合过滤（docs/22 §6）────────────────────────────
def filter_pending_videos(platform: str, videos: list) -> tuple[list, int]:
    """批量模式过滤：跳过已完成 / 正在排队或运行的视频（按 platform_vid 匹配）。

    返回 (待处理列表, 跳过数)。单条模式不调用本函数——用户显式指定即视为重处理意图。
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT i.platform_vid AS vid, i.status AS st FROM ingest_task_item i "
            "JOIN ingest_task t ON t.id = i.task_id "
            "WHERE t.platform=? AND i.status IN ('completed','queued','running')",
            (platform,),
        ).fetchall()
        busy = {r["vid"] for r in rows if r["vid"]}
    finally:
        conn.close()
    kept = [v for v in videos if (getattr(v, "platform_vid", "") or "") not in busy]
    return kept, len(videos) - len(kept)


# ─── 自动重试（docs/23 §2/§3）───────────────────────────────
def should_auto_retry(item: dict | None) -> bool:
    """是否应自动重试该条。

    三条硬规则（docs/23 §1/§3/§7）：

    1. **只有 `retryable` 才重试**：`non_retryable`（链接失效/作品删除）重试无意义；
    2. **`needs_action` 绝不重试**（登录失效 / 验证码 / 403 / 429 / 配额）——宁可停住等人工，
       也不把账号打到风控，且不得通过切换通道绕过；
    3. **额度只由 `auto_retry_count` 计**：重启恢复与用户手动「重试失败项」**不消耗**自动重试额度。
    """
    if not item:
        return False
    if item.get("error_class") != ERROR_RETRYABLE:
        return False
    return int(item.get("auto_retry_count") or 0) < MAX_AUTO_ATTEMPTS


def retry_backoff_seconds(auto_retry_count: int) -> int:
    """第 N 次自动重试前应等待的秒数（超出序列长度后取最后一档）。"""
    idx = max(0, min(int(auto_retry_count or 0), len(RETRY_BACKOFF_SECONDS) - 1))
    return RETRY_BACKOFF_SECONDS[idx]


def mark_auto_retry(item_id: int) -> None:
    """自动重试前调用：明细退回 `queued`，并累加自动重试次数。

    保留上次的 `error` / `error_class` 供排查（不清理失败原因）。
    排除 `completed`（吸收态，不变量 #3），其余状态均允许重置为待执行。
    """
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE ingest_task_item SET status='queued', stage='pending', "
                "stage_cn='自动重试', auto_retry_count=auto_retry_count+1, updated_at=? "
                "WHERE id=? AND status != 'completed'",
                (int(time.time()), item_id),
            )
            conn.commit()
        finally:
            conn.close()


# ─── 控制操作（docs/23 §8）─────────────────────────────────
def retry_failed_items(task_id: str) -> int:
    """把失败明细重新置为 queued（保留 attempt_count 与失败原因）"""
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            n = conn.execute(
                "UPDATE ingest_task_item SET status='queued', stage='pending', stage_cn='等待重试', "
                "updated_at=? WHERE task_id=? AND status='failed'",
                (int(time.time()), task_id),
            ).rowcount or 0
            if n:
                conn.execute(
                    "UPDATE ingest_task SET status='queued', message='已重排失败项', finished_at=NULL "
                    "WHERE id=? AND status IN ('partial','failed')",
                    (task_id,),
                )
            conn.commit()
        finally:
            conn.close()
    return n


def request_pause(task_id: str) -> bool:
    """请求安全暂停：置 pausing，执行循环在下一条前停下"""
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            n = conn.execute(
                "UPDATE ingest_task SET status='pausing', message='暂停中，当前视频处理完后停止' "
                "WHERE id=? AND status IN ('queued','running')",
                (task_id,),
            ).rowcount or 0
            conn.commit()
        finally:
            conn.close()
    return bool(n)


def is_pause_requested(task_id: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT status FROM ingest_task WHERE id=?", (task_id,)).fetchone()
    finally:
        conn.close()
    return bool(row) and row["status"] in {"pausing", "paused"}


def mark_paused(task_id: str) -> None:
    """执行循环确认停止后落定 paused"""
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE ingest_task SET status='paused', message='已暂停（可继续处理剩余）' "
                "WHERE id=? AND status='pausing'",
                (task_id,),
            )
            conn.commit()
        finally:
            conn.close()


def request_resume(task_id: str) -> bool:
    """继续：把 paused / waiting_for_action / partial / failed 的任务及其剩余明细置回可执行"""
    with _WRITE_LOCK:
        conn = get_conn()
        try:
            n = conn.execute(
                "UPDATE ingest_task SET status='queued', error=NULL, finished_at=NULL, "
                "message='继续处理剩余' WHERE id=? AND status IN "
                "('paused','pausing','waiting_for_action','partial','failed','queued')",
                (task_id,),
            ).rowcount or 0
            conn.execute(
                "UPDATE ingest_task_item SET status='queued', stage='pending', stage_cn='等待继续', "
                "updated_at=? WHERE task_id=? AND status IN ('failed','paused','not_started')",
                (int(time.time()), task_id),
            )
            conn.commit()
        finally:
            conn.close()
    return bool(n)


def has_pending_items(task_id: str) -> bool:
    """是否仍有待处理明细（queued / running）"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM ingest_task_item WHERE task_id=? AND status IN ('queued','running')",
            (task_id,),
        ).fetchone()
    finally:
        conn.close()
    return bool(row and row["n"])


def next_pending_item(task_id: str) -> dict | None:
    """取下一个待处理明细（供恢复/继续时执行）"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM ingest_task_item WHERE task_id=? AND status='queued' ORDER BY id LIMIT 1",
            (task_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None
