"""V0.3 测试：硬预测自动过 + 24h 撤销窗口 + 博主画像（分桶/Brier 校准）。

覆盖：
1. eligible + 确定性判定 + AI 高置信 → 自动锁定为 final
2. partial / 置信不足 → 不自动过，留在 human_review
3. auto_apply 开关关闭 → 不自动过
4. 24h 撤销窗口内 undo → 退回 human_review 并解锁
5. 超过 24h 窗口 → 拒绝撤销
6. 未锁定的预测 → 拒绝撤销
7. 博主画像：基础正确率、领域/置信度分桶、Brier 校准点、撤销后重算
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["CI_APP__DATA_DIR"] = tempfile.mkdtemp(prefix="ci_v03_")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.adapters.base import ContentInfo  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.models import Magnitude, PredictionIn, Subject, TimeWindow  # noqa: E402
from app.services import verification as v  # noqa: E402
from app.services.pipeline import ingest_pipeline  # noqa: E402
from app.services.verification import (  # noqa: E402
    get_creator_profile,
    review_prediction,
    run_verification,
    scan_due_predictions,
    undo_auto_apply,
)

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


# 测试配置：auto_apply 开、撤销窗口 24h、样本量门槛调低便于画像统计
v.config._data.setdefault("verification", {})
v.config._data["verification"]["auto_apply"] = True
v.config._data["verification"]["auto_apply_min_confidence"] = 0.85
v.config._data["verification"]["auto_apply_undo_hours"] = 24
v.config._data["verification"]["sample_size_threshold"] = 5

init_db()
now = int(time.time())
due_at = now + 3600


def make_pred(raw_text: str, stype: str, symbol: str, name: str,
              conf_raw: str, conf_score: float) -> PredictionIn:
    return PredictionIn(
        raw_text=raw_text,
        subject=Subject(type=stype, symbol=symbol, name=name),
        direction="up",
        magnitude=Magnitude(min=0.10, max=0.10, unit="ratio", is_relative=True),
        time_expression_raw="未来 30 天",
        time_window=TimeWindow(
            raw="未来 30 天", parsed_end=due_at, granularity="day", is_fuzzy=False,
        ),
        confidence_raw=conf_raw,
        confidence_score=conf_score,
    )


PREDS = [
    # P1: 应自动过（correct + 0.90 高置信）
    make_pred("黄金未来 30 天上涨超过 10%", "commodity", "XAU", "黄金", "我敢打赌", 0.90),
    # P2: partial，不应自动过
    make_pred("苹果未来 30 天上涨超过 10%", "stock", "AAPL", "苹果", "我敢打赌", 0.90),
    # P3: correct 但置信不足 0.50，不应自动过
    make_pred("原油未来 30 天上涨超过 10%", "commodity", "WTI", "原油", "我敢打赌", 0.90),
    # P4: 应自动过（incorrect + 0.95 高置信），用于超窗撤销测试
    make_pred("标普 500 未来 30 天上涨超过 10%", "index", "SPX", "标普500", "肯定", 0.95),
    # P5: 开关关闭场景（correct + 0.90），不应自动过
    make_pred("欧元兑美元未来 30 天上涨超过 10%", "fx", "EURUSD", "欧元兑美元", "我敢打赌", 0.90),
]

content = ContentInfo(
    platform="bilibili",
    platform_vid="BVV03TEST",
    title="V0.3 测试视频",
    url="https://www.bilibili.com/video/BVV03TEST",
    creator_platform_id="v03_creator",
    creator_name="V0.3 测试博主",
    published_at=now,
)


class FakeIngestAI:
    def chat(self, system, user, task, json_mode=True, **kwargs):
        if task == "summarize":
            return {"summary": "测试", "key_points": []}, "fake"
        if task == "extract_claim":
            return {"claims": []}, "fake"
        if task == "extract_prediction":
            return {"predictions": [p.model_dump() for p in PREDS]}, "fake"
        raise AssertionError(task)


import app.services.pipeline as pipeline  # noqa: E402
pipeline.ai_gateway = FakeIngestAI()

VERIFY_RESULTS = [
    {"ai_verdict": "correct", "ai_score": 1.0, "ai_confidence": 0.90,
     "ai_reasoning": {"post_time_facts": [], "judgment": "fake", "uncertainty_reasons": []}},
    {"ai_verdict": "partial", "ai_score": 0.5, "ai_confidence": 0.90,
     "ai_reasoning": {"post_time_facts": [], "judgment": "fake", "uncertainty_reasons": []}},
    {"ai_verdict": "correct", "ai_score": 1.0, "ai_confidence": 0.50,
     "ai_reasoning": {"post_time_facts": [], "judgment": "fake", "uncertainty_reasons": []}},
    {"ai_verdict": "incorrect", "ai_score": 0.0, "ai_confidence": 0.95,
     "ai_reasoning": {"post_time_facts": [], "judgment": "fake", "uncertainty_reasons": []}},
    {"ai_verdict": "correct", "ai_score": 1.0, "ai_confidence": 0.90,
     "ai_reasoning": {"post_time_facts": [], "judgment": "fake", "uncertainty_reasons": []}},
]


class FakeVerifyAI:
    def __init__(self):
        self.queue = [dict(r) for r in VERIFY_RESULTS]

    def chat(self, system, user, task, json_mode=True, **kwargs):
        item = self.queue.pop(0) if self.queue else {
            "ai_verdict": "inconclusive", "ai_score": None, "ai_confidence": 0.0,
            "ai_reasoning": {"post_time_facts": [], "judgment": "fallback", "uncertainty_reasons": []},
        }
        return item, "fake"


v.ai_gateway = FakeVerifyAI()

print("== 1. 入库与确认 ==")
result = ingest_pipeline(content, "测试逐字稿内容……黄金原油苹果标普欧元。")
pids = [r["id"] for r in result["predictions"]]
check("入库 5 条预测", len(pids) == 5)
for pid in pids:
    review_prediction(pid, "active", due_at=due_at)
conn = get_conn()
check("5 条全部进入 active",
      conn.execute("SELECT COUNT(*) AS n FROM prediction WHERE status='active'").fetchone()["n"] == 5)
check("5 条均标记硬预测可自动",
      conn.execute("SELECT COUNT(*) AS n FROM prediction WHERE auto_apply_eligible=1").fetchone()["n"] == 5)
conn.close()
changed = scan_due_predictions(now=due_at + 1)
check("全部标记为 due", len(changed) == 5)

print("== 2. 硬预测自动过 ==")
d1 = run_verification(pids[0])
check("P1 correct+高置信 → final", d1["prediction"]["status"] == "final")
check("P1 自动锁定", d1["verification"]["locked"] == 1)
check("P1 记录 auto_applied_at", d1["verification"]["auto_applied_at"] is not None)
check("P1 human_verdict 系统代填", d1["verification"]["human_verdict"] == "correct")
check("P1 final_verdict=correct", d1["verification"]["final_verdict"] == "correct")
check("P1 返回 auto_applied=True", d1["auto_applied"] is True)

d2 = run_verification(pids[1])
check("P2 partial 不自动过 → human_review", d2["prediction"]["status"] == "human_review")
check("P2 未锁定", d2["verification"]["locked"] == 0)

d3 = run_verification(pids[2])
check("P3 置信不足不自动过 → human_review", d3["prediction"]["status"] == "human_review")
check("P3 未锁定", d3["verification"]["locked"] == 0)

d4 = run_verification(pids[3])
check("P4 incorrect+高置信 → final", d4["prediction"]["status"] == "final")
check("P4 自动锁定", d4["verification"]["locked"] == 1)

conn = get_conn()
n = conn.execute(
    "SELECT COUNT(*) AS n FROM event_log WHERE entity_type='prediction' AND event_type='auto_applied'"
).fetchone()["n"]
conn.close()
check("auto_applied 事件记录 2 条", n == 2)

print("== 3. 博主画像（分桶 + Brier 校准）==")
conn = get_conn()
creator_row = conn.execute(
    "SELECT id FROM creator WHERE platform='bilibili' AND platform_id='v03_creator'"
).fetchone()
conn.close()
check("创建者已入库", creator_row is not None)
if not creator_row:
    sys.exit(1)
creator_id = creator_row["id"]
profile = get_creator_profile(creator_id)
rel = profile["reliability"]
check("画像包含可靠性统计", rel is not None)
check("已验证 2 条", rel["verified_count"] == 2)
check("correct 1 条", rel["correct_count"] == 1)
check("incorrect 1 条", rel["incorrect_count"] == 1)
check("基础正确率 0.5", rel["base_accuracy"] == 0.5)
check("Brier 校准分 ≈0.4563", abs((rel["calibration_score"] or 0) - 0.4563) < 1e-3)
check("领域分桶-市场 accuracy=1.0", rel["accuracy_by_domain"].get("市场", {}).get("accuracy") == 1.0)
check("领域分桶-指数 accuracy=0.0", rel["accuracy_by_domain"].get("指数", {}).get("accuracy") == 0.0)
check("置信度分桶 80-100% n=2", rel["accuracy_by_confidence_band"].get("80-100%", {}).get("n") == 2)
check("样本不足提示", rel["sample_size_warning"] is not None)
check("画像预测明细 2 条", profile["prediction_count"] == 2)
points = [p["calibration_point"] for p in profile["predictions"]]
check("校准点含 [0.9,1.0]", [0.9, 1.0] in points)
check("校准点含 [0.95,0.0]", [0.95, 0.0] in points)

print("== 4. 24h 撤销窗口 ==")
du = undo_auto_apply(pids[0])
check("P1 撤销成功 → human_review", du["prediction"]["status"] == "human_review")
check("P1 撤销后解锁", du["verification"]["locked"] == 0)
check("P1 撤销清除 auto_applied_at", du["verification"]["auto_applied_at"] is None)
check("P1 撤销清除 human_verdict", du["verification"]["human_verdict"] is None)
check("P1 撤销后 final_verdict 回退 AI", du["verification"]["final_verdict"] == "correct")

conn = get_conn()
conn.execute(
    "UPDATE verification SET auto_applied_at=? WHERE prediction_id=?",
    (now - 25 * 3600, pids[3]),
)
conn.commit()
conn.close()
try:
    undo_auto_apply(pids[3])
    check("P4 超窗撤销被拒绝", False)
except ValueError as exc:
    check("P4 超窗撤销被拒绝", "撤销窗口" in str(exc))

try:
    undo_auto_apply(pids[1])
    check("P2 未锁定不可撤销", False)
except ValueError as exc:
    check("P2 未锁定不可撤销", "未锁定" in str(exc))

profile2 = get_creator_profile(creator_id)
check("撤销后画像重算 verified_count=1", profile2["reliability"]["verified_count"] == 1)
check("撤销后明细 1 条", profile2["prediction_count"] == 1)

print("== 5. auto_apply 开关关闭 ==")
v.config._data["verification"]["auto_apply"] = False
try:
    d5 = run_verification(pids[4])
    check("P5 开关关闭不自动过 → human_review", d5["prediction"]["status"] == "human_review")
    check("P5 未锁定", d5["verification"]["locked"] == 0)
    check("P5 返回 auto_applied=False", d5["auto_applied"] is False)
finally:
    v.config._data["verification"]["auto_apply"] = True

print(f"\n==== V0.3 result: {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
