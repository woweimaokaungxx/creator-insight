"""V0.8 测试：任务持久化与恢复（契约见 docs/22-任务与状态契约.md、docs/23-任务恢复与重试矩阵.md）。

覆盖：

1. 建表与基本 CRUD（任务 / 明细 / 尝试）
2. 批次状态由明细**汇总**（不变量 #4）
3. 吸收态保护：`completed` 明细与 `success` 任务不可被降级（不变量 #3）
4. 错误三分类：`needs_action` > `non_retryable` > `retryable`
5. 重启断点恢复：`running -> queued`，且**不增加业务重试次数**（不变量 #10）
6. 待处理集合过滤：跳过已完成 / 进行中，保留历史失败（docs/22 §6、不变量 #6）
7. 控制操作：重试失败项 / 安全暂停 / 继续
8. 尝试记录：策略、错误分类、产物路径
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["CI_APP__DATA_DIR"] = tempfile.mkdtemp(prefix="ci_v08_")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import get_conn, init_db  # noqa: E402
from app.services import task_store as ts  # noqa: E402

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


class FakeVideo:
    """模拟 adapter 返回的视频目录条目"""

    def __init__(self, vid: str, title: str = "") -> None:
        self.platform_vid = vid
        self.title = title or vid


# ── 1. 建表与基本 CRUD ──
print("── 1. 建表与基本 CRUD ──")
init_db()
conn = get_conn()
tables = {r["name"] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ingest%'")}
conn.close()
check("已建 ingest_task 表", "ingest_task" in tables)
check("已建 ingest_task_item 表", "ingest_task_item" in tables)
check("已建 ingest_attempt 表", "ingest_attempt" in tables)

tid = ts.create_task("task-a", mode="all", platform="douyin", raw_input="https://v/1")
check("创建任务返回 id", tid == "task-a")
task = ts.get_task("task-a")
check("新任务初始状态 queued", task and task["status"] == "queued")
check("新任务含兼容字段", task and {"id", "status", "stage", "items", "mode"} <= set(task))
check("新任务 raw_input 可回读", task and task["raw_input"] == "https://v/1")

ts.add_items("task-a", [{"platform_vid": f"v{i}", "title": f"视频{i}"} for i in (1, 2, 3)])
items = ts.list_items("task-a")
check("明细写入 3 条", len(items) == 3)
check("明细按写入顺序", [i["platform_vid"] for i in items] == ["v1", "v2", "v3"])
check("明细初始为 queued", all(i["status"] == "queued" for i in items))

# 重复插入同一 platform_vid 应被唯一约束忽略
ts.add_items("task-a", [{"platform_vid": "v1", "title": "重复"}])
check("唯一约束阻止重复明细", len(ts.list_items("task-a")) == 3)

# ── 2. 批次状态由明细汇总 ──
print("── 2. 批次状态由明细汇总（不变量 #4）──")
ts.update_item(items[0]["id"], status="running")
check("有 running → 任务 running", ts.summarize_task("task-a")["status"] == "running")

ts.update_item(items[0]["id"], status="completed", inc_attempt=True)
ts.update_item(items[1]["id"], status="completed", inc_attempt=True)
ts.update_item(items[2]["id"], status="completed", inc_attempt=True)
s = ts.summarize_task("task-a")
check("全 completed → success", s["status"] == "success")
check("成功计数正确", s["succeeded"] == 3 and s["failed_count"] == 0)

# 部分完成 → partial（注意：不能先 completed 再降级，会被吸收态守卫拦住）
ts.create_task("task-a2", mode="all", platform="douyin", raw_input="https://v/x")
ts.add_items("task-a2", [{"platform_vid": f"z{i}"} for i in (1, 2, 3)])
zitems = ts.list_items("task-a2")
ts.update_item(zitems[0]["id"], status="completed", inc_attempt=True)
ts.update_item(zitems[1]["id"], status="completed", inc_attempt=True)
ts.update_item(zitems[2]["id"], status="failed", error="连接超时",
               error_class=ts.ERROR_RETRYABLE, inc_attempt=True)
s = ts.summarize_task("task-a2")
check("部分完成 → partial", s["status"] == "partial")
check("失败计数正确", s["failed_count"] == 1)
check("成功计数正确（partial）", s["succeeded"] == 2)

# 全失败 → failed
ts.create_task("task-a3", mode="all", platform="douyin", raw_input="https://v/y")
ts.add_items("task-a3", [{"platform_vid": "w1"}])
witems = ts.list_items("task-a3")
ts.update_item(witems[0]["id"], status="failed", error="视频不存在",
               error_class=ts.ERROR_NON_RETRYABLE, inc_attempt=True)
check("全失败 → failed", ts.summarize_task("task-a3")["status"] == "failed")

# ── 3. 吸收态保护 ──
print("── 3. 吸收态保护（不变量 #3）──")
ts.update_item(items[0]["id"], status="failed", error="不该生效")
check("completed 明细不可降级", ts.find_item(items[0]["id"])["status"] == "completed")

ts.set_task_status("task-a", "success", message="完成")
ts.set_task_status("task-a", "failed", error="不该生效")
check("success 任务不可降级", ts.get_task("task-a")["status"] == "success")

ts.set_task_status("task-a", None, message="只更新字段")
check("status=None 时仍可更新其它字段", ts.get_task("task-a")["message"] == "只更新字段")
check("status=None 不改变状态", ts.get_task("task-a")["status"] == "success")

# ── 4. 错误三分类 ──
print("── 4. 错误三分类（docs/23 §1）──")
check("登录失效 → needs_action", ts.classify_error("登录已失效，请重新扫码") == ts.ERROR_NEEDS_ACTION)
check("验证码 → needs_action", ts.classify_error("遇到验证码，需要人工处理") == ts.ERROR_NEEDS_ACTION)
check("429 限流 → needs_action", ts.classify_error("HTTP 429 Too Many Requests") == ts.ERROR_NEEDS_ACTION)
check("配额耗尽 → needs_action", ts.classify_error("已达每日 AI 调用上限（50 次）") == ts.ERROR_NEEDS_ACTION)
check("作品不存在 → non_retryable", ts.classify_error("视频不存在或已删除") == ts.ERROR_NON_RETRYABLE)
check("目录审核不通过 → non_retryable", ts.classify_error("审核不通过：字段缺失") == ts.ERROR_NON_RETRYABLE)
check("连接超时 → retryable", ts.classify_error("Connection timed out") == ts.ERROR_RETRYABLE)
check("未知错误兜底 → retryable", ts.classify_error("某个没见过的异常") == ts.ERROR_RETRYABLE)
check("优先级：登录 + 超时 → needs_action",
      ts.classify_error("登录失效且 connection timed out") == ts.ERROR_NEEDS_ACTION)

# ── 5. 重启断点恢复 ──
print("── 5. 重启断点恢复（docs/23 §5，不变量 #10）──")
ts.create_task("task-b", mode="all", platform="douyin", raw_input="https://v/2")
ts.add_items("task-b", [{"platform_vid": "r1", "title": "恢复1"}, {"platform_vid": "r2", "title": "恢复2"}])
ritems = ts.list_items("task-b")
ts.update_item(ritems[0]["id"], status="running", inc_attempt=True)
ts.update_item(ritems[1]["id"], status="completed", inc_attempt=True)
ts.set_task_status("task-b", "running", started_at=1)
before_attempt = ts.find_item(ritems[0]["id"])["attempt_count"]

result = ts.recover_interrupted()
check("恢复统计返回", result["items_requeued"] >= 1 and result["tasks_requeued"] >= 1)
check("running 明细退回 queued", ts.find_item(ritems[0]["id"])["status"] == "queued")
check("completed 明细保持完成", ts.find_item(ritems[1]["id"])["status"] == "completed")
check("恢复不增加重试次数", ts.find_item(ritems[0]["id"])["attempt_count"] == before_attempt)
check("任务退回 queued", ts.get_task("task-b")["status"] == "queued")

again = ts.recover_interrupted()
check("恢复幂等（第二次无改动）", again["items_requeued"] == 0 and again["tasks_requeued"] == 0)

# ── 6. 待处理集合过滤 ──
print("── 6. 待处理集合过滤（docs/22 §6）──")
videos = [FakeVideo("p1"), FakeVideo("p2"), FakeVideo("p3")]
ts.create_task("task-c", mode="all", platform="douyin", raw_input="https://v/3")
ts.add_items("task-c", [{"platform_vid": "p1", "title": "已完成"}, {"platform_vid": "p2", "title": "失败"}])
citems = ts.list_items("task-c")
ts.update_item(citems[0]["id"], status="completed")
ts.update_item(citems[1]["id"], status="failed", error_class=ts.ERROR_RETRYABLE)

kept, skipped = ts.filter_pending_videos("douyin", videos)
kept_vids = [v.platform_vid for v in kept]
check("跳过已完成作品", "p1" not in kept_vids)
check("历史失败可重新进入", "p2" in kept_vids)
check("新作品保留", "p3" in kept_vids)
check("跳过计数正确", skipped == 1)

# 其它平台的记录不应影响本平台过滤
ts.create_task("task-d", mode="all", platform="bilibili", raw_input="https://v/4")
ts.add_items("task-d", [{"platform_vid": "p3", "title": "B站同名ID"}])
kept2, _ = ts.filter_pending_videos("douyin", videos)
check("过滤按平台隔离", "p3" in [v.platform_vid for v in kept2])

# ── 7. 控制操作 ──
print("── 7. 控制操作（docs/23 §8）──")
ts.create_task("task-e", mode="all", platform="douyin", raw_input="https://v/5")
ts.add_items("task-e", [{"platform_vid": "e1", "title": "失败项"}, {"platform_vid": "e2", "title": "完成项"}])
eitems = ts.list_items("task-e")
ts.update_item(eitems[0]["id"], status="failed", error="下载中断",
               error_class=ts.ERROR_RETRYABLE, inc_attempt=True)
ts.update_item(eitems[1]["id"], status="completed", inc_attempt=True)

n = ts.retry_failed_items("task-e")
check("重试失败项数量正确", n == 1)
check("失败项变回 queued", ts.find_item(eitems[0]["id"])["status"] == "queued")
check("已完成项不受影响", ts.find_item(eitems[1]["id"])["status"] == "completed")
check("重试保留尝试次数", ts.find_item(eitems[0]["id"])["attempt_count"] == 1)
check("重试保留失败原因", ts.find_item(eitems[0]["id"])["error"] == "下载中断")
check("重试保留错误分类", ts.find_item(eitems[0]["id"])["error_class"] == ts.ERROR_RETRYABLE)

ts.create_task("task-f", mode="all", platform="douyin", raw_input="https://v/6")
ts.set_task_status("task-f", "running")
check("请求暂停成功", ts.request_pause("task-f") is True)
check("暂停请求已记录", ts.is_pause_requested("task-f") is True)
ts.mark_paused("task-f")
check("暂停落定为 paused", ts.get_task("task-f")["status"] == "paused")
check("暂停中不再接受暂停请求", ts.request_pause("task-f") is False)
check("继续成功", ts.request_resume("task-f") is True)
check("继续后回到 queued", ts.get_task("task-f")["status"] == "queued")

# 已完成任务不可暂停
ts.set_task_status("task-a", None)
ts.create_task("task-g", mode="single", platform="douyin", raw_input="https://v/7")
ts.set_task_status("task-g", "success")
check("success 任务不可暂停", ts.request_pause("task-g") is False)

# ── 8. 尝试记录 ──
print("── 8. 尝试记录（docs/22 §9.3）──")
aid = ts.start_attempt("task-e", eitems[0]["id"], "platform_subtitle")
check("尝试返回 attempt_id", aid > 0)
ts.finish_attempt(aid, "failed", error="下载中断", error_class=ts.ERROR_RETRYABLE,
                 artifact_paths=["data/transcripts/a.md"])
conn = get_conn()
row = conn.execute("SELECT * FROM ingest_attempt WHERE id=?", (aid,)).fetchone()
conn.close()
check("尝试状态已落库", row and row["status"] == "failed")
check("尝试策略已落库", row and row["strategy"] == "platform_subtitle")
check("尝试错误分类已落库", row and row["error_class"] == ts.ERROR_RETRYABLE)
check("尝试产物路径已落库", row and "a.md" in (row["artifact_paths_json"] or ""))
check("空 attempt_id 安全忽略", ts.finish_attempt(0, "success") is None)

# 尝试表不写敏感信息（不变量 #9）
check("产物索引不含敏感字段", row and "cookie" not in (row["artifact_paths_json"] or "").lower())

# ── 9. 待处理判断（断点续跑依据，V0.14）──
print("── 9. 待处理判断（断点续跑依据）──")
ts.create_task("task-h", mode="single", platform="douyin", raw_input="https://v/8")
check("无明细 → 无需续跑", ts.has_pending_items("task-h") is False)

ts.add_items("task-h", [{"platform_vid": "h1", "title": "待处理"}])
check("有 queued 明细 → 需续跑", ts.has_pending_items("task-h") is True)

hitems = ts.list_items("task-h")
ts.update_item(hitems[0]["id"], status="running")
check("running 明细也算待处理", ts.has_pending_items("task-h") is True)

ts.update_item(hitems[0]["id"], status="completed", inc_attempt=True)
check("明细完成后 → 无需续跑", ts.has_pending_items("task-h") is False)

ts.create_task("task-i", mode="all", platform="douyin", raw_input="https://v/9")
ts.add_items("task-i", [{"platform_vid": "i1"}, {"platform_vid": "i2"}])
iitems = ts.list_items("task-i")
ts.update_item(iitems[0]["id"], status="failed", error_class=ts.ERROR_RETRYABLE, inc_attempt=True)
ts.update_item(iitems[1]["id"], status="completed", inc_attempt=True)
check("只剩失败项时 → 不自动续跑（需用户显式重试）",
      ts.has_pending_items("task-i") is False)

ts.retry_failed_items("task-i")
check("重试失败项后 → 可续跑", ts.has_pending_items("task-i") is True)

# ── 10. 自动重试策略（docs/23 §2/§3）──
print("── 10. 自动重试策略（docs/23 §3）──")
check("自动重试上限为 3 次", ts.MAX_AUTO_ATTEMPTS == 3)
check("退避序列 5s → 20s → 60s",
      [ts.retry_backoff_seconds(n) for n in (0, 1, 2)] == [5, 20, 60])
check("退避超出序列后取最后一档", ts.retry_backoff_seconds(9) == 60)

ts.create_task("task-r", mode="all", platform="douyin", raw_input="https://v/10")
ts.add_items("task-r", [{"platform_vid": "m1", "title": "可重试"},
                        {"platform_vid": "m2", "title": "需人工"},
                        {"platform_vid": "m3", "title": "不可重试"}])
mitems = ts.list_items("task-r")
m_retry, m_action, m_dead = mitems[0]["id"], mitems[1]["id"], mitems[2]["id"]
check("新明细自动重试额度为 0", ts.find_item(m_retry)["auto_retry_count"] == 0)

# 临时故障（retryable）→ 允许自动重试
ts.update_item(m_retry, status="failed", error="连接超时",
               error_class=ts.ERROR_RETRYABLE, inc_attempt=True)
check("临时故障 → 允许自动重试", ts.should_auto_retry(ts.find_item(m_retry)) is True)

# 需人工（needs_action）→ 绝不自动重试（§7 不可绕过）
ts.update_item(m_action, status="failed", error="登录已失效，请重新扫码",
               error_class=ts.ERROR_NEEDS_ACTION, inc_attempt=True)
check("登录失效 → 禁止自动重试",
      ts.should_auto_retry(ts.find_item(m_action)) is False)

# 确定性失败（non_retryable）→ 重试无意义
ts.update_item(m_dead, status="failed", error="视频不存在或已删除",
               error_class=ts.ERROR_NON_RETRYABLE, inc_attempt=True)
check("作品已删除 → 禁止自动重试",
      ts.should_auto_retry(ts.find_item(m_dead)) is False)

# 自动重试：退回 queued + 累加额度 + 保留失败原因
ts.mark_auto_retry(m_retry)
item_r = ts.find_item(m_retry)
check("自动重试后回 queued", item_r["status"] == "queued")
check("自动重试额度 +1", item_r["auto_retry_count"] == 1)
check("保留失败原因供排查", item_r["error"] == "连接超时")
check("保留错误分类", item_r["error_class"] == ts.ERROR_RETRYABLE)

# 用满 3 次额度后不再自动重试
for _ in range(2):
    ts.update_item(m_retry, status="failed", error="连接超时",
                   error_class=ts.ERROR_RETRYABLE)
    ts.mark_auto_retry(m_retry)
check("用满 3 次额度", ts.find_item(m_retry)["auto_retry_count"] == 3)
check("额度用尽 → 停止自动重试",
      ts.should_auto_retry(ts.find_item(m_retry)) is False)

# 额度只由自动重试消耗：重启恢复 / 人工重试不计入
ts.update_item(m_retry, status="running")
before_auto = ts.find_item(m_retry)["auto_retry_count"]
ts.recover_interrupted()
check("重启恢复不改变自动重试额度",
      ts.find_item(m_retry)["auto_retry_count"] == before_auto)
ts.retry_failed_items("task-r")
check("人工重试不消耗自动重试额度",
      ts.find_item(m_retry)["auto_retry_count"] == before_auto)

# 吸收态：completed 不被自动重试拉回
ts.update_item(m_dead, status="completed")
ts.mark_auto_retry(m_dead)
check("completed 明细不被自动重试拉回",
      ts.find_item(m_dead)["status"] == "completed")

print(f"\n{'=' * 46}")
print(f"V0.8 任务持久化与恢复：{PASS} 通过 / {FAIL} 失败")
print(f"{'=' * 46}")
sys.exit(1 if FAIL else 0)
