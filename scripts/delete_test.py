"""视频删除测试：影响面预览 + 级联清理完整性。

对应能力：

- `DELETE /api/contents/{id}`、`GET /api/contents/{id}/delete-preview`
  （实现：`app/services/content_store.py`）
- `scripts/cleanup_empty_contents.py` 的判定依据

覆盖：

1. 预览：正确统计预测 / 观点 / 证据 / 验证 / 总结 / 逐字稿数量
2. 预览：视频不存在 → `None`
3. 删除：视频及**全部下游数据**被清空（逐表断言无残留）
4. 删除：不影响**其它**视频的数据（隔离性）
5. 删除：任务与尝试历史保留，明细仅 `content_id` 置空（操作日志不丢）
6. 删除：返回各表删除条数
7. 重复删除 → `None`（幂等，不抛异常）
8. 「无逐字稿」判定：预览能反映关联数据，供清理脚本据此跳过
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["CI_APP__DATA_DIR"] = tempfile.mkdtemp(prefix="ci_delete_")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import get_conn, init_db  # noqa: E402
from app.services import content_store  # noqa: E402

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


def count(table: str, where: str = "", params: tuple = ()) -> int:
    conn = get_conn()
    try:
        sql = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def build_video(cid: str, vid: str) -> None:
    """造一条「处理完整」的视频：content + transcript + claim + prediction + evidence + verification + summary"""
    conn = get_conn()
    conn.execute(
        "INSERT INTO content (id, creator_id, platform, platform_vid, title, url, fetched_at) "
        "VALUES (?, 'cr-1', 'douyin', ?, ?, ?, 0)",
        (cid, vid, f"视频{vid}", f"https://x/{vid}"),
    )
    conn.execute(
        "INSERT INTO transcript (id, content_id, source, language, text_full, "
        "text_full_simplified, segments_json, created_at) VALUES (?, ?, 'whisper_local', 'zh', ?, ?, '[]', 0)",
        (f"t-{cid}", cid, "原始逐字稿内容", "简体校对版内容"),
    )
    conn.execute(
        "INSERT INTO claim (id, content_id, text, speaker, category, created_at) "
        "VALUES (?, ?, '观点内容', '博主本人', 'opinion', 0)",
        (f"cl-{cid}", cid),
    )
    conn.execute(
        "INSERT INTO prediction (id, content_id, revision_no, raw_text, speaker, status, "
        "prediction_at, created_at, updated_at) VALUES (?, ?, 1, '预测原话', '博主本人', "
        "'pending_review', 0, 0, 0)",
        (f"p-{cid}", cid),
    )
    conn.execute(
        "INSERT INTO evidence (id, prediction_id, content_id, source, source_type, "
        "collected_at, created_at) VALUES (?, ?, ?, 'test', 'news', 0, 0)",
        (f"e-{cid}", f"p-{cid}", cid),
    )
    conn.execute(
        "INSERT INTO verification (id, prediction_id, final_verdict, created_at, updated_at) "
        "VALUES (?, ?, 'correct', 0, 0)",
        (f"v-{cid}", f"p-{cid}"),
    )
    conn.execute(
        "INSERT INTO content_summary (id, content_id, version, summary, is_current, created_at) "
        "VALUES (?, ?, 1, '总结正文', 1, 0)",
        (f"s-{cid}", cid),
    )
    conn.commit()
    conn.close()


init_db()
conn = get_conn()
conn.execute(
    "INSERT INTO creator (id, platform, platform_id, name, url, added_at) "
    "VALUES ('cr-1', 'douyin', 'p1', '测试博主', 'douyin://creator/p1', 0)"
)
# 只抓了目录、没有任何分析数据的「空」视频
conn.execute(
    "INSERT INTO content (id, creator_id, platform, platform_vid, title, url, fetched_at) "
    "VALUES ('c-empty', 'cr-1', 'douyin', 'v-empty', '空视频', 'https://x/empty', 0)"
)
# 任务与明细（引用 c-del，用于验证「操作日志不丢」）
conn.execute(
    "INSERT INTO ingest_task (id, mode, platform, raw_input, status, created_at) "
    "VALUES ('task-del', 'single', 'douyin', 'https://x/del', 'success', 0)"
)
conn.execute(
    "INSERT INTO ingest_task_item (task_id, content_id, platform_vid, title, status, created_at) "
    "VALUES ('task-del', 'c-del', 'v-del', '视频v-del', 'completed', 0)"
)
conn.commit()
conn.close()

build_video("c-del", "v-del")
build_video("c-keep", "v-keep")

# ── 1. 预览影响面 ──
print("── 1. 删除前预览影响面 ──")
pv = content_store.preview_delete("c-del")
check("预览返回数据", pv is not None)
check("预览标题正确", pv["title"] == "视频v-del")
check("预览预测 1 条", pv["predictions"] == 1)
check("预览观点 1 条", pv["claims"] == 1)
check("预览证据 1 条", pv["evidences"] == 1)
check("预览验证 1 条", pv["verifications"] == 1)
check("预览总结 1 条", pv["summaries"] == 1)
check("预览逐字稿 1 条", pv["transcripts"] == 1)

# ── 2. 不存在 ──
print("\n── 2. 不存在的视频 ──")
check("预览不存在的视频 → None", content_store.preview_delete("c-nope") is None)
check("删除不存在的视频 → None", content_store.delete_content("c-nope") is None)

# ── 3. 执行删除 ──
print("\n── 3. 级联删除 ──")
res = content_store.delete_content("c-del")
check("删除返回结果", res is not None)
check("返回预测删除数", res["deleted"]["predictions"] == 1)
check("返回观点删除数", res["deleted"]["claims"] == 1)
check("返回证据删除数", res["deleted"]["evidences"] == 1)
check("返回验证删除数", res["deleted"]["verifications"] == 1)
check("返回总结删除数", res["deleted"]["summaries"] == 1)
check("返回逐字稿删除数", res["deleted"]["transcripts"] == 1)

print("\n── 4. 无残留（逐表检查）──")
check("content 已删", count("content", "id=?", ("c-del",)) == 0)
check("transcript 已级联删", count("transcript", "content_id=?", ("c-del",)) == 0)
check("claim 已级联删", count("claim", "content_id=?", ("c-del",)) == 0)
check("prediction 已级联删", count("prediction", "content_id=?", ("c-del",)) == 0)
check("evidence 已级联删（按 content）",
      count("evidence", "content_id=?", ("c-del",)) == 0)
check("evidence 已级联删（按 prediction）",
      count("evidence", "prediction_id=?", ("p-c-del",)) == 0)
check("verification 已级联删", count("verification", "prediction_id=?", ("p-c-del",)) == 0)
check("content_summary 已级联删", count("content_summary", "content_id=?", ("c-del",)) == 0)

# ── 5. 隔离性：其它视频不受影响 ──
print("\n── 5. 不影响其它视频 ──")
check("其它视频保留", count("content", "id=?", ("c-keep",)) == 1)
check("其它视频逐字稿保留", count("transcript", "content_id=?", ("c-keep",)) == 1)
check("其它视频预测保留", count("prediction", "content_id=?", ("c-keep",)) == 1)
check("其它视频证据保留", count("evidence", "content_id=?", ("c-keep",)) == 1)
check("其它视频总结保留", count("content_summary", "content_id=?", ("c-keep",)) == 1)

# ── 6. 操作日志保留 ──
print("\n── 6. 任务历史保留 ──")
check("任务仍存在", count("ingest_task", "id=?", ("task-del",)) == 1)
check("任务明细仍存在", count("ingest_task_item", "task_id=?", ("task-del",)) == 1)
conn = get_conn()
row = conn.execute(
    "SELECT content_id FROM ingest_task_item WHERE task_id='task-del'"
).fetchone()
conn.close()
check("明细 content_id 已置空", row["content_id"] is None)

# ── 7. 幂等 ──
print("\n── 7. 重复删除 ──")
check("再次删除 → None（不抛异常）", content_store.delete_content("c-del") is None)
check("再次预览 → None", content_store.preview_delete("c-del") is None)

# ── 8. 空视频判定（清理脚本依据）──
print("\n── 8. 「无逐字稿」判定 ──")
conn = get_conn()
empties = conn.execute(
    "SELECT c.id FROM content c WHERE NOT EXISTS "
    "(SELECT 1 FROM transcript t WHERE t.content_id=c.id)"
).fetchall()
conn.close()
ids = {r["id"] for r in empties}
check("空视频被识别为无逐字稿", "c-empty" in ids)
check("有逐字稿的视频不在其中", "c-keep" not in ids)
check("空视频仍在库中（未自动删除）", count("content", "id=?", ("c-empty",)) == 1)

print(f"\n{'=' * 46}")
print(f"视频删除与预览：{PASS} 通过 / {FAIL} 失败")
print(f"{'=' * 46}")
sys.exit(1 if FAIL else 0)
