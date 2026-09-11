"""V0.2+ 测试：AI 生成证据链（无外部搜索时的兜底证据）

场景：预测已到期、没有任何外部证据 → AI 凭公开知识生成证据链并判定。
验证：
1. ai_generated_evidence 入库（source=ai_knowledge, credibility=0.4）
2. 发布时间不低于 prediction_at（防 AI 幻觉时间）
3. AI 判定可用 AI 知识完成（对知识范围内的预测）
4. 前端数据接口能读到 AI 生成证据
"""
import json
import os
import sys
import tempfile
import time

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["CI_APP__DATA_DIR"] = tempfile.mkdtemp(prefix="ci_ai_ev_")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import datetime

from app.adapters.base import ContentInfo  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.models import Magnitude, PredictionIn, Subject, TimeWindow  # noqa: E402
from app.services.pipeline import ingest_pipeline  # noqa: E402
from app.services.verification import (  # noqa: E402
    get_verification_detail,
    review_prediction,
    run_verification,
    submit_human_review,
    undo_auto_apply,
)


def ts(y, m, d):
    return int(datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc).timestamp())


PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


init_db()

# 一条 2024 年的预测（AI 知识范围内，能凭知识验证）
prediction = PredictionIn(
    raw_text="2024年黄金会上涨超过20%",
    subject=Subject(type="commodity", symbol="XAU", name="黄金"),
    direction="up",
    magnitude=Magnitude(min=0.20, max=0.20, unit="ratio", is_relative=True),
    time_expression_raw="2024年",
    time_window=TimeWindow(raw="2024年", parsed_end=ts(2024, 12, 31),
                           granularity="year", is_fuzzy=False),
    confidence_raw="我敢打赌",
    confidence_score=0.8,
)
content = ContentInfo(
    platform="bilibili", platform_vid="BVAIEV",
    title="AI 生成证据测试", url="https://www.bilibili.com/video/BVAIEV",
    creator_platform_id="ai_ev_creator", creator_name="AI证据测试博主",
    published_at=ts(2024, 1, 10),
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

result = ingest_pipeline(content, "2024年黄金会上涨超过20%。")
conn = get_conn()
row = conn.execute(
    "SELECT id, status, prediction_at FROM prediction ORDER BY created_at DESC LIMIT 1"
).fetchone()
prediction_id = row["id"]
conn.close()

# 确认 active + 设置 due_at 为过去（已到期）
review_prediction(prediction_id, "active", due_at=ts(2025, 1, 1), notes="确认")
conn = get_conn()
conn.execute("UPDATE prediction SET status='due' WHERE id=?", (prediction_id,))
conn.commit()
conn.close()

# 真实 AI 验证（无外部证据 → AI 生成证据链）
print("运行 AI 验证（真实 DeepSeek，预计 30-60s）...")
detail = run_verification(prediction_id)
v = detail.get("verification") or {}
ai_ev = [e for e in detail.get("evidence", []) if e.get("source") == "ai_knowledge"]

check("AI 生成了证据链", len(ai_ev) >= 1)
check("AI 证据 credibility=0.4", all(e.get("credibility") == 0.4 for e in ai_ev))
check("AI 证据发布时间 >= prediction_at",
      all((e.get("published_at") or 0) >= row["prediction_at"] for e in ai_ev))
check("AI 判定有结果", v.get("ai_verdict") in {"correct", "partial", "incorrect", "inconclusive"})

# V0.3 硬预测自动过可能已直接锁定（correct/incorrect + 高置信 + 确定性预测）
auto_applied = v.get("locked") == 1 and v.get("auto_applied_at") is not None
if auto_applied:
    check("V0.3 自动过 → final", detail["prediction"]["status"] == "final")
    check("自动过记录 auto_applied_at", v.get("auto_applied_at") is not None)
    detail = undo_auto_apply(prediction_id)
    check("撤销自动过后回 human_review", detail["prediction"]["status"] == "human_review")
else:
    check("预测进入 human_review", detail["prediction"]["status"] == "human_review")

# 提交人工复核
final = submit_human_review(prediction_id, "correct", human_notes="人工核实 AI 证据")
check("人工复核后 final", final["prediction"]["status"] == "final")

print(f"\n==== AI 证据测试: {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
