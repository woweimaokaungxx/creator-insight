"""V0.1 冒烟测试：管线核心逻辑（不依赖真实 AI 密钥）

覆盖：
1. 平台解析（抖音文案 / 抖音长链 / B站链接）
2. 模拟 AI 输出 → ingest_pipeline 入库
3. Prediction 字段落库与 auto_apply 判定
4. 时间模糊标记
"""
import json
import os
import sys
import tempfile

# 确保能 import app 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 使用临时数据目录，避免污染真实库
os.environ["CI_APP__DATA_DIR"] = tempfile.mkdtemp(prefix="ci_test_")

from app.db import get_conn, init_db  # noqa: E402
from app.models import PredictionIn, Magnitude, Subject, TimeWindow  # noqa: E402
from app.adapters.base import ContentInfo  # noqa: E402
from app.services.pipeline import compute_auto_apply, _insert_prediction, ingest_pipeline  # noqa: E402

PASS = 0
FAIL = 0

def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")

print("== 1. 平台解析 ==")
from app.adapters import guess_platform
check("抖音文案识别", guess_platform("看看【张三讲财经】的作品 https://v.douyin.com/abc/ 复制此链接") == "douyin")
check("抖音长链识别", guess_platform("https://www.douyin.com/video/7351234567890123456") == "douyin")
check("B站识别", guess_platform("https://www.bilibili.com/video/BV1xx411c7mD") == "bilibili")
check("未知识别", guess_platform("https://www.youtube.com/watch?v=abc") is None)

print("== 2. auto_apply 判定 ==")
hard = PredictionIn(
    raw_text="黄金未来 30 天上涨超过 10%",
    subject=Subject(type="commodity", symbol="XAU", name="黄金"),
    direction="up",
    magnitude=Magnitude(min=0.10, max=0.10, unit="ratio", is_relative=True),
    time_expression_raw="未来 30 天",
    time_window=TimeWindow(raw="未来 30 天", parsed_end=1770000000, granularity="day", is_fuzzy=False),
    confidence_raw="我敢打赌", confidence_score=0.8,
)
check("硬预测可自动", compute_auto_apply(hard) is True)

soft = PredictionIn(
    raw_text="黄金可能会上涨",
    subject=Subject(type="commodity", symbol="XAU", name="黄金"),
    direction="up",
    time_expression_raw="未来 30 天",
    time_window=TimeWindow(raw="未来 30 天", parsed_end=1770000000, granularity="day", is_fuzzy=False),
    confidence_raw="可能", confidence_score=0.35,
)
check("模糊语气不可自动", compute_auto_apply(soft) is False)

no_time = PredictionIn(
    raw_text="黄金会上涨超过 10%",
    subject=Subject(type="commodity", symbol="XAU", name="黄金"),
    direction="up",
    magnitude=Magnitude(min=0.10, max=0.10, unit="ratio", is_relative=True),
    time_window=TimeWindow(raw="", is_fuzzy=True, requires_human_confirmation=True),
    confidence_raw="肯定", confidence_score=0.92,
)
check("时间模糊不可自动", compute_auto_apply(no_time) is False)

print("== 3. 入库管线（模拟 AI）==")
init_db()
conn = get_conn()
content = ContentInfo(
    platform="bilibili", platform_vid="BV1TEST",
    title="测试视频：黄金走势分析", url="https://www.bilibili.com/video/BV1TEST",
    creator_platform_id="12345", creator_name="测试博主",
    published_at=1700000000, duration_sec=300,
)
# 模拟 ingest_pipeline 的 AI 环节：monkeypatch ai_gateway
import app.services.pipeline as pipe
class FakeAI:
    def __init__(self, preds):
        self.preds = preds
        self.calls = []
    def chat(self, system, user, task, json_mode=True, **kw):
        self.calls.append(task)
        if task == "summarize":
            return {"summary": "测试总结", "key_points": ["点1"]}, "fake"
        if task == "extract_claim":
            return {"claims": [{"text": "黄金长期看好", "category": "opinion"}]}, "fake"
        if task == "extract_prediction":
            return {"predictions": [p.model_dump() for p in self.preds]}, "fake"
        return {"ok": True}, "fake"

fake = FakeAI([hard, soft, no_time])
pipe.ai_gateway = fake

result = ingest_pipeline(content, "测试逐字稿内容……黄金未来30天上涨超过10%，黄金可能上涨，黄金会上涨超过10%。")
check("入库返回 predictions=3", len(result["predictions"]) == 3)
check("入库返回 claims=1", len(result["claims"]) == 1)

rows = conn.execute("SELECT * FROM prediction").fetchall()
check("DB predictions=3", len(rows) == 3)

# 检查入库字段
hard_row = [r for r in rows if r["confidence_raw"] == "我敢打赌"]
check("硬预测 auto_apply=1", hard_row and hard_row[0]["auto_apply_eligible"] == 1)
check("硬预测 due_at 已设置", hard_row and hard_row[0]["due_at"] == 1770000000)

soft_row = [r for r in rows if r["confidence_raw"] == "可能"]
check("软预测 auto_apply=0", soft_row and soft_row[0]["auto_apply_eligible"] == 0)

no_time_row = [r for r in rows if r["time_expression_raw"] == ""]
check("模糊时间 due_at 为空", no_time_row and no_time_row[0]["due_at"] is None)

creator_rows = conn.execute("SELECT * FROM creator").fetchall()
check("creator 已入库", len(creator_rows) == 1)
check("creator 名称", creator_rows[0]["name"] == "测试博主")
conn.close()

print(f"\n==== 结果: {PASS} 通过, {FAIL} 失败 ====")
sys.exit(1 if FAIL else 0)
