"""V0.2 验证状态机测试：确认 → 到期 → AI 初判 → 人工锁定。"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["CI_APP__DATA_DIR"] = tempfile.mkdtemp(prefix="ci_v02_")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.adapters.base import ContentInfo  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.models import Magnitude, PredictionIn, Subject, TimeWindow  # noqa: E402
from app.services.pipeline import ingest_pipeline  # noqa: E402
from app.services.verification import (  # noqa: E402
    add_manual_evidence,
    get_verification_detail,
    list_verification_queue,
    review_prediction,
    run_verification,
    scan_due_predictions,
    submit_human_review,
)


PASS = 0
FAIL = 0


def check(name: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


init_db()
now = int(time.time())
due_at = now + 3600
prediction = PredictionIn(
    raw_text="黄金未来 30 天上涨超过 10%",
    subject=Subject(type="commodity", symbol="XAU", name="黄金"),
    direction="up",
    magnitude=Magnitude(min=0.10, max=0.10, unit="ratio", is_relative=True),
    time_expression_raw="未来 30 天",
    time_window=TimeWindow(
        raw="未来 30 天",
        parsed_end=due_at,
        granularity="day",
        is_fuzzy=False,
    ),
    confidence_raw="我敢打赌",
    confidence_score=0.8,
)
content = ContentInfo(
    platform="bilibili",
    platform_vid="BVV02TEST",
    title="V0.2 测试视频",
    url="https://www.bilibili.com/video/BVV02TEST",
    creator_platform_id="v02_creator",
    creator_name="V0.2 测试博主",
    published_at=now,
)


class FakeIngestAI:
    def chat(self, system, user, task, json_mode=True, **kwargs):
        if task == "summarize":
            return {"summary": "测试", "key_points": []}, "fake"
        if task == "extract_claim":
            return {"claims": []}, "fake"
        if task == "extract_prediction":
            return {"predictions": [prediction.model_dump()]}, "fake"
        raise AssertionError(task)


import app.services.pipeline as pipeline  # noqa: E402
pipeline.ai_gateway = FakeIngestAI()

result = ingest_pipeline(content, "黄金未来 30 天上涨超过 10%。")
prediction_id = result["predictions"][0]["id"] if "id" in result["predictions"][0] else None
conn = get_conn()
row = conn.execute(
    "SELECT id, status, due_at FROM prediction ORDER BY created_at DESC LIMIT 1"
).fetchone()
prediction_id = row["id"]
check("抽取结果进入 pending_review", row["status"] == "pending_review")
check("入库同时生成 baseline", conn.execute(
    "SELECT COUNT(*) AS n FROM evidence WHERE prediction_id=? AND relation='baseline'",
    (prediction_id,),
).fetchone()["n"] == 1)
conn.close()

review_prediction(prediction_id, "active", due_at=due_at, notes="确认是明确预测")
conn = get_conn()
row = conn.execute("SELECT status, due_at FROM prediction WHERE id=?", (prediction_id,)).fetchone()
check("人工确认后进入 active", row["status"] == "active")
check("人工确认写入 due_at", row["due_at"] == due_at)
conn.close()

check("scheduler 将到期预测标为 due",
      prediction_id in scan_due_predictions(now=due_at + 1))
manual = add_manual_evidence(
    prediction_id,
    title="黄金到期行情",
    summary="到期时黄金较预测时上涨 12%。",
    published_at=due_at,
    relation="corroborating",
    source_type="market_data",
)
check("可添加带时间戳的人工证据", manual["source"] == "manual")


class FakeVerifyAI:
    def chat(self, system, user, task, json_mode=True, **kwargs):
        return {
            "ai_verdict": "partial",
            "ai_score": 0.5,
            "ai_confidence": 0.7,
            "ai_reasoning": {
                "post_time_facts": [],
                "judgment": "测试 AI 判定",
                "uncertainty_reasons": [],
            },
        }, "fake"


import app.services.verification as verification  # noqa: E402
verification.ai_gateway = FakeVerifyAI()
detail = run_verification(prediction_id)
check("验证完成后进入 human_review",
      detail["prediction"]["status"] == "human_review")
check("verification 写入 AI verdict",
      detail["verification"]["ai_verdict"] == "partial")
check("验证详情包含 baseline",
      any(e["relation"] == "baseline" for e in detail["evidence"]))
check("队列包含待人工复核项",
      any(item["id"] == prediction_id for item in list_verification_queue()))

final_detail = submit_human_review(
    prediction_id, "correct", human_notes="人工确认达到方向目标"
)
check("人工复核后进入 final",
      final_detail["prediction"]["status"] == "final")
check("人工复核锁定 verification",
      final_detail["verification"]["locked"] == 1)
check("最终 verdict 采用 human verdict",
      final_detail["verification"]["final_verdict"] == "correct")

conn = get_conn()
reliability = conn.execute(
    "SELECT verified_count, correct_count, base_accuracy FROM creator_reliability"
).fetchone()
check("人工锁定后重算可靠性", reliability is not None)
check("正确率为 100%", reliability and reliability["base_accuracy"] == 1.0)
conn.close()

print(f"\n==== V0.2 result: {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
