"""内容阅读测试：逐字稿读取 + 历史总结回填解析。

对应能力：

- `GET /api/contents/{id}/transcript`（实现：`app/services/transcript_store.py`）
- `scripts/backfill_summaries.py`：把历史 `*_AI总结.md` 解析入库

覆盖：

1. 逐字稿：有简体版 → 返回简体版并标 `simplified`
2. 逐字稿：只有原稿（`NULL` 与空串两种情况）→ 回退原稿并标 `raw`
3. 逐字稿：无记录 → `None`（不抛异常）
4. 逐字稿：字数统计、来源、语言字段
5. 回填解析：标准结构（`## AI 总结` + `## 要点`）
6. 回填解析：无要点 / 无总结 / 多要点 / 不同项目符号
7. 回填写入：走 `summary_store` 版本化（得 `v1`，可被后续版本覆盖而不丢）
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["CI_APP__DATA_DIR"] = tempfile.mkdtemp(prefix="ci_reader_")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db import get_conn, init_db  # noqa: E402
from app.services import summary_store, transcript_store  # noqa: E402
from backfill_summaries import parse_summary_md  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


def add_content(cid: str, vid: str, title: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO content (id, creator_id, platform, platform_vid, title, url, fetched_at) "
        "VALUES (?, 'cr-1', 'douyin', ?, ?, ?, 0)",
        (cid, vid, title, f"https://x/{vid}"),
    )
    conn.commit()
    conn.close()


def add_transcript(content_id: str, text_full: str, simplified: str | None,
                   source: str = "whisper_local") -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO transcript (id, content_id, source, language, text_full, "
        "text_full_simplified, segments_json, created_at) VALUES (?, ?, ?, 'zh', ?, ?, '[]', 0)",
        (f"t-{content_id}", content_id, source, text_full, simplified),
    )
    conn.commit()
    conn.close()


init_db()
conn = get_conn()
conn.execute(
    "INSERT INTO creator (id, platform, platform_id, name, url, added_at) "
    "VALUES ('cr-1', 'douyin', 'p1', '测试博主', 'douyin://creator/p1', 0)"
)
conn.commit()
conn.close()

add_content("c-simp", "v1", "有简体版的视频")
add_content("c-raw-null", "v2", "只有原稿（NULL）的视频")
add_content("c-raw-empty", "v3", "只有原稿（空串）的视频")
add_content("c-none", "v4", "无逐字稿的视频")

add_transcript("c-simp", "這是繁體原始逐字稿" * 10, "这是简体校对版逐字稿" * 10)
add_transcript("c-raw-null", "這是繁體原始逐字稿內容" * 10, None)
add_transcript("c-raw-empty", "原始逐字稿內容" * 10, "   ", source="platform_subtitle")

# ── 1. 简体版优先 ──
print("── 1. 逐字稿：简体校对版优先 ──")
r = transcript_store.get_transcript("c-simp")
check("返回数据", r is not None)
check("text_kind = simplified", r["text_kind"] == transcript_store.TEXT_SIMPLIFIED)
check("has_simplified = True", r["has_simplified"] is True)
check("返回的是简体版正文", r["text"].startswith("这是简体校对版"))
check("字数统计正确", r["char_count"] == len("这是简体校对版逐字稿" * 10))
check("来源字段透传", r["source"] == "whisper_local")
check("语言字段透传", r["language"] == "zh")

# ── 2. 无简体版 → 回退原稿 ──
print("\n── 2. 逐字稿：回退原始稿 ──")
r2 = transcript_store.get_transcript("c-raw-null")
check("NULL 简体版 → 回退原稿", r2["text"].startswith("這是繁體原始逐字稿"))
check("NULL 时 text_kind = raw", r2["text_kind"] == transcript_store.TEXT_RAW)
check("NULL 时 has_simplified = False", r2["has_simplified"] is False)

r3 = transcript_store.get_transcript("c-raw-empty")
check("空串简体版 → 回退原稿", r3["text"].startswith("原始逐字稿內容"))
check("空串时 text_kind = raw", r3["text_kind"] == transcript_store.TEXT_RAW)
check("空串时 has_simplified = False", r3["has_simplified"] is False)
check("平台字幕来源透传", r3["source"] == "platform_subtitle")

# ── 3. 无逐字稿 ──
print("\n── 3. 逐字稿：无记录 ──")
check("无记录 → None", transcript_store.get_transcript("c-none") is None)
check("不存在的视频 → None", transcript_store.get_transcript("c-not-exist") is None)

# ── 4. 回填解析 ──
print("\n── 4. 历史总结 md 解析 ──")
md_std = """# 某视频标题

## AI 总结

这是一段总结正文，用于验证解析。

## 要点

- 要点甲
- 要点乙
- 要点丙
"""
summary, points = parse_summary_md(md_std)
check("解析出总结正文", summary == "这是一段总结正文，用于验证解析。")
check("解析出 3 条要点", points == ["要点甲", "要点乙", "要点丙"])

summary2, points2 = parse_summary_md("## AI 总结\n\n只有总结没有要点。\n")
check("无要点章节 → 要点为空", points2 == [])
check("无要点章节 → 正文正确", summary2 == "只有总结没有要点。")

summary3, _ = parse_summary_md("# 标题\n\n## 要点\n\n- 甲\n")
check("无总结章节 → 正文为空", summary3 == "")

summary4, points4 = parse_summary_md(
    "## AI 总结\n\n正文A\n\n## 要点\n\n* 星号项\n- 短横项\n"
)
check("兼容星号与短横项目符号", points4 == ["星号项", "短横项"])
check("多段正文完整保留", summary4 == "正文A")

summary5, _ = parse_summary_md("")
check("空文件不抛异常", summary5 == "")

# ── 5. 回填写入走版本化 ──
print("\n── 5. 回填写入版本化 ──")
rec = summary_store.save_summary(
    "c-simp", {"summary": summary, "key_points": points},
    provider="file-backfill", prompt_hash="file-backfill",
)
check("回填写入 v1", rec["version"] == 1)
check("回填 provider 标记来源", rec["provider"] == "file-backfill")
check("回填 prompt_hash 标记来源", rec["prompt_hash"] == "file-backfill")
check("总结可读回", summary_store.get_current_summary("c-simp")["summary"] == summary)

rec2 = summary_store.save_summary(
    "c-simp", {"summary": "重新处理后的新总结"}, provider="cloud", model="mimo-v2.5",
)
check("重新处理生成 v2（不覆盖 v1）", rec2["version"] == 2)
check("回填版本仍可读回", len(summary_store.list_versions("c-simp")) == 2)
check("旧版本正文保留",
      any(v["version"] == 1 and v["summary"] == summary
          for v in summary_store.list_versions("c-simp")))

print(f"\n{'=' * 46}")
print(f"内容阅读（逐字稿 + 回填）：{PASS} 通过 / {FAIL} 失败")
print(f"{'=' * 46}")
sys.exit(1 if FAIL else 0)
