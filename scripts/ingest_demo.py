"""演示数据入库：中文财经内容走真实 NVIDIA AI 抽取管线"""
import sys
import traceback

sys.path.insert(0, ".")

from app.adapters.base import ContentInfo
from app.services.pipeline import ingest_pipeline

content = ContentInfo(
    platform="bilibili", platform_vid="BV1DEMO",
    title="【演示】黄金与A股下半年走势分析", url="https://www.bilibili.com/video/BV1DEMO",
    creator_platform_id="demo_laochen", creator_name="老陈讲财经",
    published_at=1787000000, duration_sec=300,
)
transcript = """大家好我是老陈。今天聊黄金。我认为黄金未来30天会上涨超过10%，因为美联储大概率要降息，美元指数会走弱。
另外，A股方面，我觉得上证指数明年可能突破4500点，科创50弹性最大。
还有一个判断，如果房地产政策全面放开限购，一线城市房价一年内会企稳回升。
今天就讲这些，以上不构成投资建议。"""

print("开始入库...", flush=True)
try:
    r = ingest_pipeline(content, transcript, transcript_source="platform_subtitle")
    print("入库完成!", flush=True)
    print("claims:", len(r["claims"]), "| predictions:", len(r["predictions"]), flush=True)
    for p in r["predictions"]:
        print(f"  - {p['raw_text'][:40]} | {p['subject']['type']} | {p['direction']} | {p['time_expression_raw']}", flush=True)
except Exception:
    traceback.print_exc()
    sys.exit(2)
