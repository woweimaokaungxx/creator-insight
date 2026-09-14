"""逐字稿读取：供前端直接阅读（简体校对版优先，回退原始稿）。

**背景**：简体校对版此前只写文件（`data/transcripts/{标题}_简体校对版.md`），
库里的 `transcript.text_full_simplified` 虽已入库，却**没有任何接口暴露** ——
前端读不到，用户只能去翻文件。本模块提供统一的读取入口。

与 `summary_store` 的分工：总结是 AI 生成产物（版本化、可重跑）；
逐字稿是**转写的原始材料**（一条视频一份，不产生多版本），故不版本化。
"""
from __future__ import annotations

from ..db import get_conn

TEXT_SIMPLIFIED = "simplified"   # AI 简体校对版
TEXT_RAW = "raw"                 # 原始逐字稿（可能是繁体 / 含识别错字）


def get_transcript(content_id: str, conn=None) -> dict | None:
    """返回逐字稿阅读数据；该视频无逐字稿时返回 `None`。

    优先返回 **简体校对版**（`text_full_simplified`）；老数据没有简体版时
    回退**原始逐字稿**（`text_full`）。用 `text_kind` 标明当前返回的是哪一种，
    前端据此区分展示（避免把繁体原稿误当成校对稿）。

    `text_kind` 的判定依据是**简体版是否有内容**，而不是「字段是否存在」——
    因为早期记录该字段可能为 `NULL` 或空串。
    """
    own_conn = conn is None
    connection = conn or get_conn()
    try:
        row = connection.execute(
            "SELECT source, language, text_full, text_full_simplified, created_at "
            "FROM transcript WHERE content_id=?",
            (content_id,),
        ).fetchone()
    finally:
        if own_conn:
            connection.close()

    if not row:
        return None

    simplified = (row["text_full_simplified"] or "").strip()
    raw = (row["text_full"] or "").strip()
    text = simplified or raw
    return {
        "content_id": content_id,
        "text": text or None,
        "text_kind": TEXT_SIMPLIFIED if simplified else TEXT_RAW,
        "has_simplified": bool(simplified),
        "char_count": len(text),
        "source": row["source"],       # platform_subtitle / whisper_local
        "language": row["language"],
        "created_at": row["created_at"],
    }
