"""V0.4 测试：通知中心（站内 local / Webhook / 邮件）+ 触发点集成。

覆盖：
1. local 通道：通知写入 notification 表（含 payload 序列化）
2. 多通道：webhook 未配置 / email 未配置 → 明确 failed 且不影响 local
3. webhook 已配置：本地 HTTP 服务器接收 JSON 成功
4. 通知中心：列表 / unread 统计 / 单条与全部已读 / 清空
5. send_test_with 自定义测试通知
6. 触发点-自动锁定：硬预测自动过 → 生成 auto_applied 通知
7. 触发点-新视频：订阅发现新视频 → 生成 new_videos 通知
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import tempfile
import threading
import time

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["CI_APP__DATA_DIR"] = tempfile.mkdtemp(prefix="ci_v04_")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.adapters.base import ContentInfo  # noqa: E402
from app.config import config  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.models import Magnitude, PredictionIn, Subject, TimeWindow  # noqa: E402
from app.services import creator_monitor, notify  # noqa: E402

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


# 测试配置：默认 local 通道
config._data["notify"] = {
    "enabled": True,
    "channels": ["local"],
    "webhook_url": "",
    "smtp": {},
}
config._data.setdefault("verification", {})
config._data["verification"]["auto_apply"] = True
config._data["verification"]["auto_apply_min_confidence"] = 0.85
config._data["verification"]["auto_apply_undo_hours"] = 24

init_db()

print("== 1. local 通道 ==")
res = notify.send_notification("标题测试", "内容测试", category="new_videos", payload={"a": 1})
check("local 通道 sent", res[0]["status"] == "sent")
conn = get_conn()
row = conn.execute("SELECT * FROM notification").fetchone()
check("通知入库", row is not None and row["title"] == "标题测试")
check("payload 序列化", json.loads(row["payload_json"]) == {"a": 1})
check("category 记录", row["category"] == "new_videos")
conn.close()

print("== 2. 多通道（webhook/email 未配置）==")
config._data["notify"]["channels"] = ["local", "webhook", "email"]
res = notify.send_notification("多通道", "内容", category="manual")
by = {r["channel"]: r for r in res}
check("local sent", by["local"]["status"] == "sent")
check("webhook 未配置 → failed", by["webhook"]["status"] == "failed")
check("email 未配置 → failed", by["email"]["status"] == "failed")
check("failed 有原因", "未配置" in (by["webhook"]["error"] or "") and "未配置" in (by["email"]["error"] or ""))

print("== 3. webhook 已配置 ==")
received: list[dict] = []


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        received.append(json.loads(self.rfile.read(length)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
config._data["notify"]["channels"] = ["webhook"]
config._data["notify"]["webhook_url"] = f"http://127.0.0.1:{port}/hook"
res = notify.send_notification("Webhook 测试", "内容", category="manual")
check("webhook 发送成功", res[0]["status"] == "sent")
time.sleep(0.3)
check("webhook 收到 JSON", len(received) == 1 and received[0]["title"] == "Webhook 测试")
check("webhook 带 category/payload/sent_at",
      received[0].get("category") == "manual" and received[0].get("sent_at"))
server.shutdown()

print("== 4. 通知中心：已读/清空 ==")
config._data["notify"]["channels"] = ["local"]
lst = notify.list_notifications()
check("列表返回 unread 统计", lst["unread"] >= 1 and len(lst["items"]) >= 1)
r = notify.mark_read()
lst2 = notify.list_notifications()
check("全部标记已读", lst2["unread"] == 0)
notify.send_notification("新消息", "内容")
lst3 = notify.list_notifications()
nid = lst3["items"][0]["id"]
check("新通知未读计数=1", lst3["unread"] == 1)
notify.mark_read(nid)
lst4 = notify.list_notifications()
check("单条已读后 unread=0", lst4["unread"] == 0)
notify.clear_notifications()
lst5 = notify.list_notifications()
check("清空后列表为空", len(lst5["items"]) == 0 and lst5["unread"] == 0)

print("== 5. send_test_with ==")
res = notify.send_test_with("自定义标题", "自定义内容")
lst = notify.list_notifications()
check("测试通知入库", res[0]["status"] == "sent"
      and lst["items"][0]["title"] == "自定义标题"
      and lst["items"][0]["category"] == "manual")

print("== 6. 触发点：硬预测自动锁定 ==")
from app.services import pipeline, verification  # noqa: E402
from app.services.verification import (  # noqa: E402
    review_prediction,
    run_verification,
    scan_due_predictions,
)


class FakeIngestAI:
    def chat(self, system, user, task, json_mode=True, **kwargs):
        if task == "summarize":
            return {"summary": "s", "key_points": []}, "fake"
        if task == "extract_claim":
            return {"claims": []}, "fake"
        if task == "extract_prediction":
            pred = PredictionIn(
                raw_text="黄金未来 30 天上涨超过 10%",
                subject=Subject(type="commodity", symbol="XAU", name="黄金"),
                direction="up",
                magnitude=Magnitude(min=0.10, max=0.10, unit="ratio", is_relative=True),
                time_expression_raw="未来 30 天",
                time_window=TimeWindow(raw="未来 30 天", parsed_end=int(time.time()) + 3600,
                                       granularity="day", is_fuzzy=False),
                confidence_raw="我敢打赌", confidence_score=0.90,
            )
            return {"predictions": [pred.model_dump()]}, "fake"
        raise AssertionError(task)


class FakeVerifyAI:
    def chat(self, system, user, task, json_mode=True, **kwargs):
        return {"ai_verdict": "correct", "ai_score": 1.0, "ai_confidence": 0.90,
                "ai_reasoning": {"post_time_facts": [], "judgment": "fake",
                                 "uncertainty_reasons": []}}, "fake"


pipeline.ai_gateway = FakeIngestAI()
verification.ai_gateway = FakeVerifyAI()
content = ContentInfo(
    platform="bilibili", platform_vid="BVV04TEST", title="V0.4 测试视频",
    url="https://www.bilibili.com/video/BVV04TEST",
    creator_platform_id="v04_creator", creator_name="V0.4 测试博主",
    published_at=int(time.time()),
)
result = pipeline.ingest_pipeline(content, "黄金原油测试内容。")
pid = result["predictions"][0]["id"]
review_prediction(pid, "active", due_at=int(time.time()) + 3600)
scan_due_predictions(now=int(time.time()) + 3601)
detail = run_verification(pid)
check("自动过触发", detail["auto_applied"] is True)
lst = notify.list_notifications()
cats = [i["category"] for i in lst["items"]]
check("自动锁定通知已生成", "auto_applied" in cats)
aa = next(i for i in lst["items"] if i["category"] == "auto_applied")
check("自动锁定通知标题正确", "硬预测自动锁定" in aa["title"])

print("== 7. 触发点：订阅发现新视频 ==")
sub_row = {"id": "s_v04", "platform": "bilibili", "source_key": "v04_creator",
           "display_name": "V0.4 测试博主"}
creator_monitor._notify_new_videos(sub_row, [{"platform_vid": "v1", "title": "新视频一"}])
lst = notify.list_notifications()
cats = [i["category"] for i in lst["items"]]
check("新视频通知已生成", "new_videos" in cats)
nv = next(i for i in lst["items"] if i["category"] == "new_videos")
check("新视频通知内容正确", "V0.4 测试博主" in nv["title"] and "1 个新视频" in nv["title"])

print(f"\n==== V0.4 result: {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
