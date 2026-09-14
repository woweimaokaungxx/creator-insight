"""V0.5 测试：异步 ingest 任务（提交即返回 + 进度 + 完成通知）。

覆盖：
1. 任务创建：pending 状态，id/created_at 正确
2. 任务状态机：pending → running → success / error
3. 进度更新：stage / progress / message 随阶段变化
4. 成功路径：result 含 content_id/title/predictions，且触发完成通知
5. 失败路径：异常时 status=error + error 字段
6. 任务列表：按时间倒序
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["CI_APP__DATA_DIR"] = tempfile.mkdtemp(prefix="ci_v05_")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.main as main  # noqa: E402
from app.adapters.base import ContentInfo, ParsedURL  # noqa: E402
from app.config import config  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.services import notify  # noqa: E402

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


config._data["notify"] = {"enabled": True, "channels": ["local"], "webhook_url": "", "smtp": {}}
init_db()


# ── 假 adapter：解析快，直接返回已转写的 transcript，不下载 ──
class FakeAdapter:
    def parse_input(self, raw_input: str) -> ParsedURL:
        return ParsedURL(platform="douyin", content_id="fake123", url="https://douyin/video/fake123")

    def fetch_content_meta(self, parsed: ParsedURL) -> ContentInfo:
        return ContentInfo(
            platform="douyin", platform_vid="fake123", title="异步测试视频",
            url="https://douyin/video/fake123", creator_platform_id="fake_creator",
            creator_name="异步测试博主",
        )

    def fetch_transcript(self, content: ContentInfo):
        # 有字幕，直接返回，跳过下载/转写
        return type("T", (), {
            "source": "platform_subtitle", "language": "zh",
            "text_full": "这是一段测试文本。", "segments": [],
        })()


import app.services.pipeline as pipeline  # noqa: E402


class FakePipeline:
    def __init__(self):
        self.pred = {"id": "p1", "raw_text": "测试预测", "confidence_score": 0.9}

    def ingest_pipeline(self, content, text, segments=None, transcript_source="whisper_local",
                        on_stage=None, **kwargs):
        return {
            "content_id": "c_fake", "creator_id": "cr_fake",
            "summary": "测试总结", "claims": [{"text": "观点"}],
            "predictions": [self.pred],
            "transcript_simplified": "简体测试文本",
        }


print("== 1. 任务创建 ==")
t1 = main._new_ingest_task()
t = main.get_ingest_task(t1)
check("创建后 status=queued（V0.13 契约）", t["status"] == "queued")
check("有 id", t["id"] == t1)
check("created_at 有值", t["created_at"] > 0)
check("初始 stage=parse", t["stage"] == "parse")
check("初始 progress=0", t["progress"] == 0.0)

print("== 2. 状态机 + 进度更新 ==")
main._update_ingest_task(t1, status="running", stage="download", progress=0.15, message="下载中")
t = main.get_ingest_task(t1)
check("running", t["status"] == "running")
check("stage=download", t["stage"] == "download")
check("progress=0.15", t["progress"] == 0.15)
main._update_ingest_task(t1, status="running", stage="analyze", progress=0.7, message="AI 分析")
t = main.get_ingest_task(t1)
check("stage 更新为 analyze", t["stage"] == "analyze")
check("progress 更新为 0.7", t["progress"] == 0.7)

print("== 3. 成功路径（模拟后台任务）==")
# 注入 fake adapter 与 fake pipeline（main 里用的是 import 进来的名字 ingest_pipeline）
orig_get_adapter = main.get_adapter
orig_guess_platform = main.guess_platform
orig_ingest_pipeline = main.ingest_pipeline
main.get_adapter = lambda p: FakeAdapter()
main.guess_platform = lambda s: "douyin"
fp = FakePipeline()
main.ingest_pipeline = fp.ingest_pipeline
# 测试环境 obsidian.active 本身为 False（root 为空），无需处理
main._run_ingest_job(t1, "https://v.douyin.com/fake/", True)
# 恢复
main.get_adapter = orig_get_adapter
main.guess_platform = orig_guess_platform
main.ingest_pipeline = orig_ingest_pipeline

t = main.get_ingest_task(t1)
check("任务成功", t["status"] == "success")
check("stage=done", t["stage"] == "done")
check("progress=1.0", t["progress"] == 1.0)
check("finished_at 有值", t["finished_at"] > 0)
r = t["result"]
check("result 含 content_id", r["content_id"] == "c_fake")
check("result 含 title", r["title"] == "异步测试视频")
check("result 含 predictions", len(r["predictions"]) == 1)
check("result 含 creator 名", r["creator"]["name"] == "异步测试博主")
check("result 含简体文案", r.get("transcript_simplified") == "简体测试文本")
check("保存了文案文件", len(r.get("saved_files") or []) >= 1)

# 完成通知已发
conn = get_conn()
n = conn.execute("SELECT COUNT(*) AS n FROM notification WHERE category='auto_process'").fetchone()["n"]
conn.close()
check("完成通知已推送", n >= 1)
notify.send_notification = None  # noqa 避免误用

print("== 4. 失败路径 ==")
t2 = main._new_ingest_task()
main.get_adapter = lambda p: (_ for _ in ()).throw(ValueError("平台错误"))
main.guess_platform = lambda s: "douyin"
main._run_ingest_job(t2, "bad input", True)
main.get_adapter = orig_get_adapter
t = main.get_ingest_task(t2)
check("失败 status=failed（V0.13 契约）", t["status"] == "failed")
check("失败有 error 信息", t["error"] and "平台错误" in t["error"])
check("失败 finished_at 有值", t["finished_at"] > 0)

print("== 5. 任务列表 ==")
lst = main.get_ingest_tasks()
check("任务列表含 2 个任务", len(lst) >= 2)
ids = {t["id"] for t in lst}
check("列表包含 t1 与 t2", t1 in ids and t2 in ids)
check("按创建时间倒序（最新在前）", lst[0]["created_at"] >= lst[1]["created_at"])

print(f"\n==== V0.5 result: {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
