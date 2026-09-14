"""V0.10 测试：AI 总结版本化存储（契约见 docs/25-内容总结与版本.md）。

覆盖：

1. 建表与首次写入
2. 空总结不写、不占用版本号
3. 版本递增：重跑生成 version+1，**旧版本保留不覆盖**
4. 当前版本唯一：同一视频仅一条 `is_current=1`
5. 多模型 / 多提示词对比所需字段（model / prompt_hash / provider / task_id）
6. `key_points` 归一化（过滤空串与非字符串、去两端空白）
7. `word_count` 兜底（AI 未给或非法值 → 按实际字数）
8. 字符串形态 payload 兼容
9. 无总结的视频（老数据）读取不报错
10. 版本号按视频独立
11. 外部事务复用：未提交不生效、提交后生效（调用方掌握事务边界）
12. 辅助函数：prompt_fingerprint / resolve_model
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["CI_APP__DATA_DIR"] = tempfile.mkdtemp(prefix="ci_v10_")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import get_conn, init_db  # noqa: E402
from app.services import summary_store as ss  # noqa: E402

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


init_db()

# 造一个博主 + 两条视频（content_summary 有外键约束，需要真实父记录）
conn = get_conn()
conn.execute(
    "INSERT INTO creator (id, platform, platform_id, name, url, added_at) "
    "VALUES ('cr-1', 'douyin', 'p1', '测试博主', 'douyin://creator/p1', 0)"
)
conn.execute(
    "INSERT INTO content (id, creator_id, platform, platform_vid, title, url, fetched_at) "
    "VALUES ('co-1', 'cr-1', 'douyin', 'v1', '测试视频', 'https://x/v1', 0)"
)
conn.execute(
    "INSERT INTO content (id, creator_id, platform, platform_vid, title, url, fetched_at) "
    "VALUES ('co-2', 'cr-1', 'douyin', 'v2', '无总结视频', 'https://x/v2', 0)"
)
conn.commit()
conn.close()

print("── 1. 建表与首次写入 ──")
r1 = ss.save_summary(
    "co-1",
    {"summary": "第一版总结内容。" * 10, "key_points": ["要点A", "要点B"], "word_count": 80},
    provider="cloud",
)
check("首次写入返回记录", bool(r1))
check("首版 version=1", r1["version"] == 1)
check("首版 is_current=True", r1["is_current"] is True)
check("key_points 正确落库",
      ss.get_current_summary("co-1")["key_points"] == ["要点A", "要点B"])

print("\n── 2. 空总结不写、不占版本号 ──")
check("空白字符串 → 不写", ss.save_summary("co-1", {"summary": "   "}) is None)
check("None → 不写", ss.save_summary("co-1", None) is None)
check("无 key_points 但有正文 → 可写（第 2 版）",
      ss.save_summary("co-1", {"summary": "第二版"})["version"] == 2)
check("空总结未新增记录（共 2 版）", len(ss.list_versions("co-1")) == 2)

print("\n── 3. 重跑不覆盖：旧版本保留 ──")
versions = ss.list_versions("co-1")
check("版本按新→旧排列", [v["version"] for v in versions] == [2, 1])
check("第一版正文仍在（未被覆盖）",
      any(str(v["summary"]).startswith("第一版总结内容") for v in versions))
check("当前版本唯一（仅 1 条 is_current）",
      sum(1 for v in versions if v["is_current"]) == 1)
check("当前版本 = 最新版", ss.get_current_summary("co-1")["version"] == 2)

print("\n── 4. 多模型 / 多提示词对比字段 ──")
r3 = ss.save_summary(
    "co-1", {"summary": "第三版（换提示词重跑）"},
    provider="cloud", model="mimo-v2.5", prompt_hash="abc123def456", task_id="task-x",
)
check("model 落库", r3["model"] == "mimo-v2.5")
check("prompt_hash 落库", r3["prompt_hash"] == "abc123def456")
check("task_id 落库（可追溯来源任务）", r3["task_id"] == "task-x")
check("第三版成为当前版本", ss.get_current_summary("co-1")["version"] == 3)
check("旧版本均已降级",
      all(not v["is_current"] for v in ss.list_versions("co-1") if v["version"] < 3))

print("\n── 5. key_points / word_count 归一化 ──")
r4 = ss.save_summary(
    "co-1",
    {"summary": "第四版", "key_points": [" 保留 ", "", "   ", 123, None, "有效"]},
)
check("过滤空串与非字符串要点", r4["key_points"] == ["保留", "有效"])
check("AI 未给 word_count → 按实际字数兜底", r4["word_count"] == len("第四版"))
check("word_count 非法值不抛异常",
      ss.save_summary("co-1", {"summary": "第五版", "word_count": "abc"})["word_count"] == 3)

print("\n── 6. 字符串形态 payload 兼容 ──")
r6 = ss.save_summary("co-1", "第六版纯字符串总结")
check("字符串 payload 可写", r6["summary"] == "第六版纯字符串总结")
check("字符串 payload 无要点", r6["key_points"] == [])

print("\n── 7. 无总结的视频（老数据） ──")
check("无总结 → get 返回 None", ss.get_current_summary("co-2") is None)
check("无总结 → versions 为空列表", ss.list_versions("co-2") == [])

print("\n── 8. 版本号按视频独立 ──")
ss.save_summary("co-2", {"summary": "co-2 的首版"})
check("co-1 不受影响（仍是第 6 版）", ss.get_current_summary("co-1")["version"] == 6)
check("co-2 从第 1 版开始", ss.get_current_summary("co-2")["version"] == 1)

print("\n── 9. 外部事务复用（调用方掌握提交时机）──")
conn = get_conn()
ss.save_summary("co-2", {"summary": "事务内写入（未提交）"}, conn=conn)
conn.rollback()
conn.close()
check("回滚后不留残影", ss.get_current_summary("co-2")["version"] == 1)

conn = get_conn()
ss.save_summary("co-2", {"summary": "事务内写入（已提交）"}, conn=conn)
conn.commit()
conn.close()
check("复用 conn 提交后生效", ss.get_current_summary("co-2")["summary"] == "事务内写入（已提交）")

print("\n── 10. 辅助函数 ──")
check("prompt_fingerprint 稳定",
      ss.prompt_fingerprint("abc") == ss.prompt_fingerprint("abc"))
check("prompt_fingerprint 区分不同提示词",
      ss.prompt_fingerprint("abc") != ss.prompt_fingerprint("abd"))
check("resolve_model(cloud) 能取到模型名", bool(ss.resolve_model("cloud")))
check("resolve_model(local) 能取到模型名", bool(ss.resolve_model("local")))
check("resolve_model(None) → None", ss.resolve_model(None) is None)

print(f"\n{'=' * 46}")
print(f"V0.10 AI 总结版本化：{PASS} 通过 / {FAIL} 失败")
print(f"{'=' * 46}")
sys.exit(1 if FAIL else 0)
