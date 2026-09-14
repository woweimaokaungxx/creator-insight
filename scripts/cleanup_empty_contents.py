"""清理「只抓了目录、没有逐字稿」的视频记录。

**场景**：批量抓取博主目录时会给每个作品建一条 `content`（标题 / 链接 / 互动数），
但只有**真正处理过**的视频才有 `transcript`。长期下来库里会堆积大量只有元数据、
没有任何分析价值的空记录（实测 72 条里有 63 条如此）。

**判定**：`content` 没有对应的 `transcript` 记录。

**安全冗余**：即使没有逐字稿，只要该视频还挂着**预测或观点**，默认也**跳过不删**
（那些数据可能来自别的途径）。确认要一并删除时才加 `--include-linked`。

**保留不动的数据**：

- 任务与尝试历史（`ingest_task` / `ingest_task_item` / `ingest_attempt`）——
  属于操作日志，删除时只把明细的 `content_id` 置空

用法::

    python scripts/cleanup_empty_contents.py                  # 预演（只报告）
    python scripts/cleanup_empty_contents.py --apply          # 实际删除
    python scripts/cleanup_empty_contents.py --apply --include-linked   # 连有关联数据的也删
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import get_conn, init_db  # noqa: E402
from app.services import content_store  # noqa: E402

EMPTY_SQL = (
    "SELECT c.id, c.title, c.platform, "
    "(SELECT COUNT(*) FROM prediction p WHERE p.content_id=c.id) AS predictions, "
    "(SELECT COUNT(*) FROM claim cl WHERE cl.content_id=c.id) AS claims "
    "FROM content c "
    "WHERE NOT EXISTS (SELECT 1 FROM transcript t WHERE t.content_id=c.id) "
    "ORDER BY c.fetched_at"
)


def main() -> int:
    apply = "--apply" in sys.argv
    include_linked = "--include-linked" in sys.argv

    init_db()
    conn = get_conn()
    try:
        total_content = conn.execute("SELECT COUNT(*) FROM content").fetchone()[0]
        rows = conn.execute(EMPTY_SQL).fetchall()
    finally:
        conn.close()

    targets, protected = [], []
    for r in rows:
        item = {"id": r["id"], "title": r["title"], "platform": r["platform"],
                "predictions": r["predictions"], "claims": r["claims"]}
        if (r["predictions"] or r["claims"]) and not include_linked:
            protected.append(item)
        else:
            targets.append(item)

    print(f"视频总数：{total_content}")
    print(f"无逐字稿：{len(rows)}（待删 {len(targets)}，保护跳过 {len(protected)}）\n")

    for it in targets:
        print(f"  [将删除] {(it['title'] or '（无标题）')[:44]}")
    for it in protected:
        print(f"  [保护跳过] {(it['title'] or '（无标题）')[:36]} "
              f"（预测 {it['predictions']} / 观点 {it['claims']}）")

    if not targets:
        print("\n没有需要清理的记录。")
        return 0

    if not apply:
        print(f"\n这是预演模式，未删除任何数据。确认无误后加 --apply 实际删除 {len(targets)} 条。")
        return 0

    print(f"\n开始删除 {len(targets)} 条…")
    gone_pred = gone_claim = 0
    deleted = 0
    for it in targets:
        res = content_store.delete_content(it["id"])
        if res:
            deleted += 1
            gone_pred += res["deleted"]["predictions"]
            gone_claim += res["deleted"]["claims"]

    conn = get_conn()
    try:
        left = conn.execute("SELECT COUNT(*) FROM content").fetchone()[0]
    finally:
        conn.close()

    print(f"\n{'=' * 52}")
    print(f"已删除 {deleted} 条视频（连带预测 {gone_pred} / 观点 {gone_claim}）")
    print(f"剩余视频 {left} 条")
    print(f"{'=' * 52}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
