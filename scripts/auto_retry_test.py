"""自动重试执行层测试（契约见 docs/23 §2/§3）。

`v08_test.py` 只覆盖 `task_store` 里的策略**纯函数**；本脚本覆盖
`app.main._process_with_auto_retry` 的**实际重试循环**（真跑，不 mock 循环本身）。

覆盖：

1. 首次成功 → 完全不重试
2. 临时故障 → 退避后自动重试，重试成功即返回
3. 每次尝试单独写一条 `ingest_attempt`（策略 / 状态 / 错误分类）
4. 重试上限 3 次：耗尽后抛出，不再继续
5. `needs_action`（登录失效）→ **立即抛出，零重试**（不可绕过，§7）
6. `non_retryable`（作品已删除）→ 立即抛出，零重试
7. `on_retry` 回调的次数与序号
8. `auto_retry_count` 只随自动重试递增；`attempt_count` 随每次尝试递增
9. **无明细 id 时仍受 3 次上限约束**（回归：曾因「额度只读库内值」而无限重试）

退避被替换为 0 秒（`RETRY_BACKOFF_SECONDS`），避免测试等待 5+20+60 秒；
退避序列本身由 `v08_test.py` 断言。
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["CI_APP__DATA_DIR"] = tempfile.mkdtemp(prefix="ci_retry_")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import get_conn, init_db  # noqa: E402
from app.main import _process_with_auto_retry  # noqa: E402
from app.services import task_store as ts  # noqa: E402

# 退避置 0，避免测试等待 5+20+60 秒
ts.RETRY_BACKOFF_SECONDS = (0, 0, 0)

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


def attempts_of(item_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM ingest_attempt WHERE item_id=? ORDER BY id", (item_id,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def make_task(task_id: str, vid: str) -> int:
    ts.create_task(task_id, mode="all", platform="douyin", raw_input="https://v/x")
    ts.add_items(task_id, [{"platform_vid": vid, "title": vid}])
    return ts.list_items(task_id)[0]["id"]


init_db()

# ── 1. 首次成功：不重试 ──
print("── 1. 首次成功不重试 ──")
i1 = make_task("rt-1", "a1")
calls = [0]


def ok_once():
    calls[0] += 1
    return {"saved_files": ["data/transcripts/a.md"]}


payload = _process_with_auto_retry(process_fn=ok_once, item_id=i1, task_id="rt-1",
                                   attempt_strategy="whisper_local")
check("返回处理结果", payload.get("saved_files") == ["data/transcripts/a.md"])
check("只调用 1 次", calls[0] == 1)
check("只写 1 条尝试记录", len(attempts_of(i1)) == 1)
check("尝试记录为成功", attempts_of(i1)[0]["status"] == "success")
check("未消耗自动重试额度", ts.find_item(i1)["auto_retry_count"] == 0)

# ── 2. 临时故障 → 自动重试后成功 ──
print("\n── 2. 临时故障自动重试后成功 ──")
i2 = make_task("rt-2", "a2")
n2 = [0]


def fail_twice_then_ok():
    n2[0] += 1
    if n2[0] < 3:
        raise TimeoutError("Connection timed out")
    return {"saved_files": ["data/transcripts/b.md"]}


retries: list[tuple] = []
payload2 = _process_with_auto_retry(
    process_fn=fail_twice_then_ok, item_id=i2, task_id="rt-2",
    attempt_strategy="whisper_local",
    on_retry=lambda n, w, e: retries.append((n, w)),
)
check("重试 2 次后成功", n2[0] == 3)
check("返回成功结果", payload2.get("saved_files") == ["data/transcripts/b.md"])
check("on_retry 被调用 2 次", len(retries) == 2)
check("重试序号从 1 递增", [r[0] for r in retries] == [1, 2])
check("写 3 条尝试记录", len(attempts_of(i2)) == 3)
check("前 2 条失败、最后成功",
      [a["status"] for a in attempts_of(i2)] == ["failed", "failed", "success"])
check("失败尝试带错误分类",
      all(a["error_class"] == ts.ERROR_RETRYABLE for a in attempts_of(i2)[:2]))
check("尝试记录带策略", attempts_of(i2)[0]["strategy"] == "whisper_local")
check("自动重试额度 = 2", ts.find_item(i2)["auto_retry_count"] == 2)
check("执行次数 attempt_count = 3", ts.find_item(i2)["attempt_count"] == 3)

# ── 3. 超出上限 → 抛出且不再重试 ──
print("\n── 3. 重试上限 3 次 ──")
i3 = make_task("rt-3", "a3")
n3 = [0]


def always_timeout():
    n3[0] += 1
    raise TimeoutError("Connection timed out")


raised = None
try:
    _process_with_auto_retry(process_fn=always_timeout, item_id=i3, task_id="rt-3",
                             attempt_strategy="whisper_local")
except Exception as exc:
    raised = exc
check("超限后抛出异常", raised is not None)
check("共执行 4 次（首跑 + 3 次重试）", n3[0] == 4)
check("自动重试额度用满 3", ts.find_item(i3)["auto_retry_count"] == 3)
check("写 4 条尝试记录", len(attempts_of(i3)) == 4)
check("额度用尽后不再自动重试", ts.should_auto_retry(ts.find_item(i3)) is False)

# ── 4. needs_action → 零重试（不可绕过，§7）──
print("\n── 4. 需人工处理零重试（docs/23 §7）──")
i4 = make_task("rt-4", "a4")
n4 = [0]


def need_login():
    n4[0] += 1
    raise RuntimeError("登录已失效，请重新扫码")


try:
    _process_with_auto_retry(process_fn=need_login, item_id=i4, task_id="rt-4",
                             attempt_strategy="whisper_local")
    check("needs_action 抛出异常", False)
except Exception:
    check("needs_action 抛出异常", True)
check("只执行 1 次（不重试）", n4[0] == 1)
check("未消耗自动重试额度", ts.find_item(i4)["auto_retry_count"] == 0)
check("尝试记录标为 needs_action",
      attempts_of(i4)[0]["error_class"] == ts.ERROR_NEEDS_ACTION)

# ── 5. non_retryable → 零重试 ──
print("\n── 5. 确定性失败零重试 ──")
i5 = make_task("rt-5", "a5")
n5 = [0]


def video_gone():
    n5[0] += 1
    raise RuntimeError("视频不存在或已删除")


try:
    _process_with_auto_retry(process_fn=video_gone, item_id=i5, task_id="rt-5",
                             attempt_strategy="platform_subtitle")
    check("non_retryable 抛出异常", False)
except Exception:
    check("non_retryable 抛出异常", True)
check("只执行 1 次", n5[0] == 1)
check("未消耗自动重试额度", ts.find_item(i5)["auto_retry_count"] == 0)
check("尝试记录带策略", attempts_of(i5)[0]["strategy"] == "platform_subtitle")

# ── 6. 无明细 id：仍受上限约束（回归）──
print("\n── 6. 无明细 id 仍受上限约束（回归）──")
ts.create_task("rt-6", mode="single", platform="douyin", raw_input="https://v/6")
n6 = [0]


def no_item_always_fail():
    n6[0] += 1
    raise TimeoutError("Connection timed out")


try:
    _process_with_auto_retry(process_fn=no_item_always_fail, item_id=None,
                             task_id="rt-6", attempt_strategy="single")
    check("无明细 id 也抛出异常（不无限重试）", False)
except Exception:
    check("无明细 id 也抛出异常（不无限重试）", True)
check("无明细 id 仍只执行 4 次（首跑 + 3 次重试）", n6[0] == 4)

# 无明细 id 且第二次成功 → 正常返回
n7 = [0]


def no_item_fail_then_ok():
    n7[0] += 1
    if n7[0] < 2:
        raise TimeoutError("Connection timed out")
    return {"saved_files": []}


payload7 = _process_with_auto_retry(process_fn=no_item_fail_then_ok, item_id=None,
                                    task_id="rt-6", attempt_strategy="single")
check("无明细 id 也能重试成功", n7[0] == 2 and payload7 == {"saved_files": []})

print(f"\n{'=' * 46}")
print(f"自动重试执行层：{PASS} 通过 / {FAIL} 失败")
print(f"{'=' * 46}")
sys.exit(1 if FAIL else 0)
