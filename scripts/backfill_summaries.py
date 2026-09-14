"""一次性回填：把历史 AI 总结 md 文件入库为 `content_summary`。

**背景**：V0.15 之前 AI 总结只写文件（`data/transcripts/{标题}_AI总结.md`）不入库，
导致这些历史视频的总结在页面上读不到。本脚本解析这些 md 并落库为 `version=1`。

**匹配方式**：文件 base（去掉 `_AI总结.md`）与 `_safe_filename(content.title)`
**完全一致** —— 写出文件时用的就是同一个函数，因此可精确匹配，无需模糊匹配。

**幂等**：默认跳过已有总结的视频；加 `--force` 可为已有总结的视频再补一版
（走版本化写入，**不会覆盖**旧版本）。

**解析的内容**（`_save_transcript_files` 写出的固定结构）：

```
# {标题}

## AI 总结

{正文}

## 要点

- 要点1
- 要点2
```

`word_count` / `model` / `provider` 无法从文件恢复，故分别按正文实际字数兜底、
模型留空，并用 `prompt_hash="file-backfill"` 标明来源，便于日后与新生成版本区分。

用法::

    python scripts/backfill_summaries.py              # 预演（只报告，不写库）
    python scripts/backfill_summaries.py --apply      # 实际写入
    python scripts/backfill_summaries.py --apply --force   # 已有总结的也补一版
"""
from __future__ import annotations

import os
import re
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import config  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.main import _safe_filename  # noqa: E402
from app.services import summary_store  # noqa: E402

SUFFIX = "_AI总结.md"
SUMMARY_RE = re.compile(r"##\s*AI\s*总结\s*\n+(.*?)(?=\n##\s|\Z)", re.S)
POINTS_RE = re.compile(r"##\s*要点\s*\n+(.*?)(?=\n##\s|\Z)", re.S)
POINT_ITEM_RE = re.compile(r"^\s*[-*]\s*(.+?)\s*$", re.M)


def parse_summary_md(text: str) -> tuple[str, list[str]]:
    """从 `_AI总结.md` 正文解析出 (总结正文, 要点列表)。"""
    body = text or ""
    m = SUMMARY_RE.search(body)
    summary = (m.group(1).strip() if m else "").strip()
    points: list[str] = []
    mp = POINTS_RE.search(body)
    if mp:
        points = [p.strip() for p in POINT_ITEM_RE.findall(mp.group(1)) if p.strip()]
    return summary, points


def main() -> int:
    apply = "--apply" in sys.argv
    force = "--force" in sys.argv

    init_db()
    out_dir = config.data_dir / "transcripts"
    if not out_dir.exists():
        print(f"目录不存在：{out_dir}")
        return 1

    # 标题 → content_id（用与写出文件相同的文件名函数，保证精确匹配）
    conn = get_conn()
    try:
        rows = conn.execute("SELECT id, title FROM content").fetchall()
        by_filename = {}
        for r in rows:
            key = _safe_filename(r["title"] or "")
            if key and key not in by_filename:
                by_filename[key] = r["id"]
        existing = {
            r["content_id"]
            for r in conn.execute("SELECT DISTINCT content_id FROM content_summary").fetchall()
        }
    finally:
        conn.close()

    files = sorted(out_dir.glob(f"*{SUFFIX}"))
    print(f"扫描目录：{out_dir}")
    print(f"找到 {len(files)} 个总结文件，库内视频 {len(by_filename)} 个，"
          f"已有总结 {len(existing)} 个\n")

    stats = {"written": 0, "skipped_existing": 0, "no_match": 0, "empty": 0}
    for f in files:
        base = f.name[: -len(SUFFIX)]
        content_id = by_filename.get(base)
        if not content_id:
            stats["no_match"] += 1
            print(f"  [跳过] 未匹配到视频：{base[:40]}…")
            continue
        summary, points = parse_summary_md(f.read_text(encoding="utf-8"))
        if not summary:
            stats["empty"] += 1
            print(f"  [跳过] 文件无总结正文：{base[:40]}…")
            continue
        if content_id in existing and not force:
            stats["skipped_existing"] += 1
            print(f"  [跳过] 已有总结（加 --force 可补一版）：{base[:40]}…")
            continue

        if apply:
            record = summary_store.save_summary(
                content_id, {"summary": summary, "key_points": points},
                provider="file-backfill", prompt_hash="file-backfill",
            )
            stats["written"] += 1
            print(f"  [写入] v{(record or {}).get('version')} · {len(summary)} 字 · "
                  f"{len(points)} 条要点 · {base[:40]}…")
        else:
            stats["written"] += 1
            print(f"  [预演] 将写入 {len(summary)} 字 · {len(points)} 条要点 · {base[:40]}…")

    print(f"\n{'=' * 52}")
    print(f"{'已写入' if apply else '待写入'} {stats['written']} / "
          f"跳过（已有）{stats['skipped_existing']} / "
          f"未匹配 {stats['no_match']} / 空文件 {stats['empty']}")
    if not apply:
        print("这是预演模式，未修改数据库。确认无误后加 --apply 实际写入。")
    print(f"{'=' * 52}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
