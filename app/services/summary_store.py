"""AI 总结产物存储（版本化，重跑不覆盖）。

背景：AI 总结此前**只写文件**（`data/transcripts/*_AI总结.md`），不入库 ——
前端无法展示、语义检索索引不到、文件删掉即永久丢失。
本模块把总结落成 SQLite 记录。

设计要点：

- **版本化，重跑不覆盖**：同一视频重跑（换模型 / 换提示词 / 换参数）生成
  `version+1` 的新记录，旧版本**保留**用于对比，不做 `UPDATE` 覆盖。
- **当前版本唯一**：同一 `content_id` 仅一条 `is_current=1`，读「当前总结」走该标记；
  写新版本时先把旧版降级。
- **可复用外部事务**：SQLite 同一时刻只允许一个写者。若调用方（如 `pipeline`）
  已持有未提交的写事务，**必须传入 conn 复用**，否则本模块另开连接写会
  `database is locked`。
- **空总结不写**：避免用空值占用版本号，导致版本序列出现无意义的空洞。

契约见 `docs/25-内容总结与版本.md`。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid

from ..config import config
from ..db import get_conn

# 版本号分配需要「读 max + 写」两步，串行化避免并发下重号
_WRITE_LOCK = threading.Lock()


def resolve_model(provider: str | None) -> str | None:
    """按 provider 名解析当前配置里的模型名（用于多模型对比时区分版本来源）。"""
    if not provider:
        return None
    section = "local_fallback" if str(provider).lower() == "local" else "cloud"
    try:
        model = config.get("ai", section, "model")
        return str(model) if model else None
    except Exception:
        return None


def prompt_fingerprint(system_prompt: str) -> str:
    """提示词指纹：提示词改动后可用它区分「同模型不同提示词」的两个版本。"""
    digest = hashlib.sha1((system_prompt or "").encode("utf-8")).hexdigest()
    return digest[:12]


def _normalize(payload) -> tuple[str, list[str], int | None]:
    """归一化 AI 返回的总结：兼容 dict / 纯字符串两种形态。"""
    if isinstance(payload, dict):
        text = str(payload.get("summary") or "").strip()
        raw_points = payload.get("key_points") or []
        word_count = payload.get("word_count")
    else:
        text = str(payload or "").strip()
        raw_points = []
        word_count = None
    points = [p.strip() for p in raw_points if isinstance(p, str) and p.strip()]
    try:
        word_count = int(word_count) if word_count is not None else None
    except (TypeError, ValueError):
        word_count = None
    return text, points, word_count


def _row_to_dict(row) -> dict:
    d = dict(row)
    try:
        d["key_points"] = json.loads(d.pop("key_points_json") or "[]")
    except Exception:
        d["key_points"] = []
    d["is_current"] = bool(d.get("is_current"))
    return d


def save_summary(content_id: str, payload, *, conn=None, provider: str | None = None,
                 model: str | None = None, prompt_hash: str | None = None,
                 task_id: str | None = None, now: int | None = None) -> dict | None:
    """写入一版新的 AI 总结，并把旧版本降级（不删除、不覆盖）。

    返回写入的记录；总结为空时返回 None（不占用版本号）。

    conn 传入时复用调用方事务（不 commit、不 close），否则自行开关连接。
    """
    text, points, word_count = _normalize(payload)
    if not text:
        return None
    now = now or int(time.time())
    own_conn = conn is None

    with _WRITE_LOCK:
        connection = conn or get_conn()
        try:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS v FROM content_summary WHERE content_id=?",
                (content_id,),
            ).fetchone()
            version = int(row["v"] or 0) + 1
            # 先把旧版本降级，保证「当前版本唯一」
            connection.execute(
                "UPDATE content_summary SET is_current=0 WHERE content_id=?", (content_id,)
            )
            summary_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO content_summary (id, content_id, version, summary, key_points_json, "
                "word_count, provider, model, prompt_hash, task_id, is_current, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (summary_id, content_id, version, text,
                 json.dumps(points, ensure_ascii=False),
                 word_count if word_count else len(text),
                 provider, model or resolve_model(provider), prompt_hash, task_id, now),
            )
            if own_conn:
                connection.commit()
        finally:
            if own_conn:
                connection.close()

    return {
        "id": summary_id,
        "content_id": content_id,
        "version": version,
        "summary": text,
        "key_points": points,
        "word_count": word_count if word_count else len(text),
        "provider": provider,
        "model": model or resolve_model(provider),
        "prompt_hash": prompt_hash,
        "task_id": task_id,
        "is_current": True,
        "created_at": now,
    }


def get_current_summary(content_id: str, conn=None) -> dict | None:
    """取当前生效版本的总结（无则 None）。"""
    own_conn = conn is None
    connection = conn or get_conn()
    try:
        row = connection.execute(
            "SELECT * FROM content_summary WHERE content_id=? AND is_current=1 "
            "ORDER BY version DESC LIMIT 1",
            (content_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        if own_conn:
            connection.close()


def list_versions(content_id: str, conn=None) -> list[dict]:
    """列出该视频的全部总结版本（新→旧），供多模型 / 多提示词对比。"""
    own_conn = conn is None
    connection = conn or get_conn()
    try:
        rows = connection.execute(
            "SELECT * FROM content_summary WHERE content_id=? ORDER BY version DESC",
            (content_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        if own_conn:
            connection.close()
