"""FastAPI 主入口：粘链接 → 解析 → 转写 → 抽取 → 入库 → Obsidian → 列表"""
from __future__ import annotations

import json
import asyncio
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from .adapters import get_adapter, guess_platform
from .config import config
from .db import get_conn, init_db
from .services import (
    account as account_service,
    content_store,
    creator_monitor,
    notify as notify_service,
    settings as settings_service,
    summary_store,
    task_store,
    transcript_store,
)
from .services.semantic import semantic_service
from .services.obsidian import obsidian
from .services.pipeline import ingest_pipeline
from .services.transcribe import whisper_transcribe
from .services.verification import (
    add_manual_evidence,
    backfill_missing_baselines,
    get_creator_profile,
    get_verification_detail,
    list_verification_queue,
    review_prediction,
    run_verification,
    scan_due_predictions,
    submit_human_review,
    undo_auto_apply,
)
from .api_semantic import router as semantic_router

app = FastAPI(title="creator-insight", version="0.6.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# V0.6 模块化前端（Vue 构建产物）：挂载 /assets 并作为默认首页
VUE_DIR = STATIC_DIR / "vue"
if (VUE_DIR / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=VUE_DIR / "assets"), name="vue-assets")

# 头像缓存目录（data/avatars）作为静态文件暴露，供前端直接引用
AVATAR_DIR = Path(config.data_dir) / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/avatars", StaticFiles(directory=AVATAR_DIR), name="avatars")

app.include_router(semantic_router)


class IngestRequest(BaseModel):
    input: str          # URL 或分享文案
    use_whisper: bool = True   # 字幕缺失时是否走 Whisper 兜底
    mode: Literal["single", "all"] = "single"   # single=仅解析该视频；all=解析该博主全部视频


class PredictionReviewRequest(BaseModel):
    decision: Literal["active", "invalid"]
    due_at: int | None = None
    notes: str = ""


class HumanVerificationRequest(BaseModel):
    human_verdict: Literal["correct", "partial", "incorrect", "inconclusive", "invalid"]
    human_notes: str = ""
    human_score: float | None = None


class ManualEvidenceRequest(BaseModel):
    title: str
    summary: str
    url: str | None = None
    published_at: int
    relation: Literal["primary", "contradicting", "corroborating", "context"] = "primary"
    source_type: Literal["news", "official", "market_data", "filings", "regulatory"] = "news"
    publisher: str = ""
    credibility: float = 0.8


class SubscriptionRequest(BaseModel):
    input: str                    # 博主主页链接 / 分享文案 / 任意作品链接
    platform: str = "douyin"
    check_interval_hours: int = 24
    auto_process: bool = False    # 新视频是否自动进入转写+AI 管线


class SubscriptionUpdateRequest(BaseModel):
    enabled: bool | None = None
    check_interval_hours: int | None = None
    auto_process: bool | None = None


class NotificationReadRequest(BaseModel):
    id: str | None = None  # 传 id 标记单条已读；不传则全部已读


class NotificationTestRequest(BaseModel):
    title: str = "测试通知"
    body: str = "如果你能看到这条消息，说明通知通道工作正常。"


class SearchRequest(BaseModel):
    query: str
    limit: int = 20


class IndexRequest(BaseModel):
    content_ids: list[str] | None = None
    all: bool = True


# V0.7 抖音账号与登录态
class AccountConfigRequest(BaseModel):
    profile_path: str = ""   # 登录态浏览器目录；留空表示清除


class AccountCookieRequest(BaseModel):
    cookie: str              # "k1=v1; k2=v2"


class AccountLoginRequest(BaseModel):
    profile_path: str = ""   # 可选；留空时用配置值或项目默认目录


# ─── 异步 ingest 任务管理（V0.13 已持久化，契约见 docs/22）─────────
# 长视频转写耗时可达十几分钟，同步接口必然超时。
# 改为：提交即返回 task_id → 后台线程处理 → 前端轮询进度。
#
# V0.13：任务状态由进程内存迁移到 SQLite（app/services/task_store.py），
# 支持「重启断点恢复 / 错误三分类 / 失败项重试 / 单条失败不中断批次」。
# 以下四个函数保留旧签名与返回结构，调用点无需改动。
_LEGACY_STATUS_MAP = {"pending": "queued", "error": "failed"}

# 各处理阶段对应的总体进度（供前端进度条平滑推进）
_STAGE_PROGRESS: dict[str, float] = {
    "parse": 0.02,
    "fetch_meta": 0.08,
    "transcript": 0.15,
    "download": 0.30,
    "transcribe": 0.45,
    "analyze": 0.75,
    "simplify": 0.80,
    "summarize": 0.85,
    "claims": 0.88,
    "predictions": 0.92,
    "save": 0.97,
    "done": 1.0,
}


def _new_ingest_task(mode: str = "single", raw_input: str = "") -> str:
    task_id = str(uuid.uuid4())
    return task_store.create_task(task_id, mode=mode, raw_input=raw_input)


def _update_ingest_task(task_id: str, **fields) -> None:
    """更新任务状态与字段（兼容旧调用签名）。

    旧实现可传 `items` / `current_index` —— 现在明细由 task_store 管理，此处忽略。
    """
    fields.pop("items", None)
    fields.pop("current_index", None)
    status = fields.pop("status", None)
    if status is not None:
        status = _LEGACY_STATUS_MAP.get(status, status)
    task_store.set_task_status(task_id, status, **fields)


def get_ingest_tasks(limit: int = 10) -> list[dict]:
    return task_store.list_tasks(limit)


def get_ingest_task(task_id: str) -> dict | None:
    return task_store.get_task(task_id)


def _process_one_video(adapter, content, use_whisper: bool,
                       on_stage: callable | None = None,
                       on_progress: callable | None = None,
                       task_id: str | None = None) -> dict:
    """处理单条视频：字幕 → 转写 → AI 抽取 → Obsidian → 保存文件。

    on_stage: 可选回调 on_stage(stage_key, stage_cn)，实时反馈该视频内部步骤。
    on_progress: 可选回调 on_progress(ratio)，反馈**当前阶段内**的完成比例
                 （目前仅 Whisper 转写会上报，0~1）。
    task_id: 所属 ingest 任务，记入 content_summary.task_id 便于追溯来源版本。
    返回 {content_id, creator, title, summary, claims, predictions, ...}
    """
    def _report(stage: str, cn: str) -> None:
        if on_stage:
            try:
                on_stage(stage, cn)
            except Exception:
                pass

    # 字幕
    _report("transcript", "获取字幕")
    transcript = adapter.fetch_transcript(content)
    transcript_source = "platform_subtitle"
    if not transcript:
        if not use_whisper:
            raise ValueError(f"未获取到字幕，且未启用 Whisper 兜底（{content.title or ''}）")
        media_dir = config.data_dir / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        _report("download", "下载视频")
        media_path = adapter.download_media(content, media_dir)
        if not media_path:
            raise ValueError(f"视频下载失败（可能被平台风控或链接失效）: {content.title or ''}")
        _report("transcribe", "Whisper 转写")
        result = whisper_transcribe(media_path, on_progress=on_progress)
        if not result:
            raise ValueError(f"Whisper 转写失败: {content.title or ''}")
        transcript = type("T", (), {
            "source": "whisper_local", "language": result["language"],
            "text_full": result["text_full"], "segments": result["segments"],
        })()
        transcript_source = "whisper_local"
    if not transcript:
        raise ValueError("未获取到字幕，且 Whisper 兜底失败")

    # AI 抽取 + 入库
    _report("analyze", "AI 分析")
    result = ingest_pipeline(
        content, transcript.text_full,
        segments=transcript.segments, transcript_source=transcript_source,
        on_stage=on_stage, task_id=task_id,
    )

    # Obsidian 写出（best-effort）
    obsidian_files = []
    if obsidian.active:
        try:
            for p in result["predictions"]:
                f = obsidian.write_prediction(
                    creator_name=content.creator_name or "未知博主",
                    content={"title": content.title, "url": content.url},
                    prediction=p,
                )
                obsidian_files.append(str(f))
        except Exception as e:
            print(f"[obsidian] 写出失败: {e}")

    # 保存简体校对版文案 + AI 总结
    saved_files = _save_transcript_files(content, result, transcript)

    return {
        "content_id": result["content_id"],
        "creator": {"name": content.creator_name, "id": content.creator_platform_id},
        "title": content.title,
        "summary": result["summary"],
        "claims": result["claims"],
        "predictions": result["predictions"],
        "transcript_source": transcript_source,
        "transcript_simplified": result.get("transcript_simplified") or "",
        "obsidian_files": obsidian_files,
        "saved_files": saved_files,
    }


def _process_with_auto_retry(*, process_fn, item_id: int | None, task_id: str,
                             attempt_strategy: str,
                             on_retry: callable | None = None) -> dict:
    """执行单条处理；失败时按 docs/23 §3 自动重试。

    规则（与 `task_store` 的策略常量一致）：

    - **只有 `retryable` 才重试**（超时 / 连接中断等临时故障）；
    - **最多 `MAX_AUTO_ATTEMPTS`(3) 次**，退避 **5s → 20s → 60s**；
    - `needs_action`（登录失效 / 验证码 / 403 / 429 / 配额）与 `non_retryable`
      （链接失效 / 作品删除）**立即抛出**，绝不自动重试、也不切换通道绕过；
    -     自动重试额度只记在 `auto_retry_count`，**重启恢复与人工重试不消耗**它；
    - 每次都单独写一条 `ingest_attempt`（策略 / 状态 / 错误分类），便于事后排查；
    - **`attempt_count`（执行次数）由本函数单点维护**：成功、可重试的失败、终局失败
      都会 +1，调用方**不应**再自行 `inc_attempt`（否则重复计数）。

    重试耗尽或不可重试时**重新抛出最后一次异常**，由调用方决定后续
    （批量：只标记该条并继续后续；单条：任务置 `failed` / `waiting_for_action`）。
    """
    auto_retries = 0          # 本进程内已自动重试次数（与库内值取大，防止无明细时失守）
    while True:
        attempt_id = task_store.start_attempt(task_id, item_id, attempt_strategy)
        try:
            payload = process_fn()
        except Exception as exc:
            err_class = task_store.classify_error(exc)
            task_store.finish_attempt(attempt_id, "failed", error=str(exc),
                                      error_class=err_class)
            item = task_store.find_item(item_id) if item_id else None
            # 本进程内的重试次数也要计入：无明细 id 时库里没有计数来源，
            # 只依赖库内值会导致「额度恒为 0 → 无限重试」。
            used = max(auto_retries, int((item or {}).get("auto_retry_count") or 0))
            if err_class != task_store.ERROR_RETRYABLE or used >= task_store.MAX_AUTO_ATTEMPTS:
                # 终局失败：记一次执行（不再重试），随后交由调用方决定任务状态
                if item_id:
                    task_store.update_item(item_id, error=str(exc)[:300],
                                           error_class=err_class, inc_attempt=True)
                raise
            wait = task_store.retry_backoff_seconds(used)
            auto_retries = used + 1
            if item_id:
                # 记录失败原因并累加自动重试额度，然后退回待执行
                task_store.update_item(item_id, error=str(exc)[:300],
                                       error_class=err_class, inc_attempt=True)
                task_store.mark_auto_retry(item_id)
            print(f"[ingest] 第 {used + 1}/{task_store.MAX_AUTO_ATTEMPTS} 次自动重试"
                  f"（{err_class}），{wait}s 后重试: {str(exc)[:80]}")
            if on_retry:
                try:
                    on_retry(used + 1, wait, exc)
                except Exception:
                    pass
            time.sleep(wait)
        else:
            if item_id:
                task_store.update_item(item_id, inc_attempt=True)
            task_store.finish_attempt(attempt_id, "success",
                                      artifact_paths=payload.get("saved_files") or [])
            return payload


def _run_ingest_job(task_id: str, raw_input: str, use_whisper: bool,
                    mode: str = "single") -> None:
    """后台线程执行的完整 ingest 流程，带进度回调、错误分类与尝试记录。

    mode=single：仅解析输入的单条视频。
    mode=all：解析输入链接对应的博主，批量抓取其全部视频并逐个处理。
    """
    _update_ingest_task(task_id, status="running", started_at=int(time.time()))
    attempt_id = task_store.start_attempt(task_id, None, "single")
    try:
        _update_ingest_task(task_id, stage="parse", progress=0.02,
                            message="识别平台并解析链接")
        platform = guess_platform(raw_input)
        if not platform:
            raise ValueError(f"无法识别平台（支持: douyin, bilibili）: {raw_input[:50]}")
        _update_ingest_task(task_id, platform=platform)
        adapter = get_adapter(platform)

        if mode == "all":
            _run_ingest_job_all(task_id, adapter, raw_input, use_whisper, platform)
            task_store.finish_attempt(attempt_id, "success")
            return

        parsed = adapter.parse_input(raw_input)
        _update_ingest_task(task_id, stage="fetch_meta",
                            progress=_STAGE_PROGRESS["fetch_meta"],
                            message="抓取视频元数据")
        content = adapter.fetch_content_meta(parsed)
        # V0.14：**开始处理时就登记明细**（此前只在成功后登记），这样长视频中途
        # 被中断（服务重启）也能被断点续跑识别到，同时统计口径与批量一致。
        task_store.add_items(task_id, [{
            "content_id": None,
            "platform_vid": getattr(content, "platform_vid", None),
            "title": content.title or "",
        }])

        # V0.14 修复：单条模式此前**漏传** on_stage/on_progress，导致长视频在
        # 「下载 / 转写」阶段任务状态一直停在「抓取视频元数据」8%，前端看不到
        # 真实进度（84 分钟视频表现尤为明显）。
        _transcribe_started = [0.0]        # 转写阶段起点，用于估算剩余时间

        def _on_stage(stage_key: str, stage_cn: str) -> None:
            if stage_key == "transcribe":
                _transcribe_started[0] = time.time()
            _update_ingest_task(task_id, stage=stage_key, message=stage_cn,
                                progress=_STAGE_PROGRESS.get(stage_key, 0.1))

        def _on_progress(ratio: float, done_sec: float = 0.0,
                         total_sec: float = 0.0) -> None:
            """转写进度：显示「已转写/总时长 + 预计剩余」，避免长视频无反馈"""
            base = _STAGE_PROGRESS["transcribe"]
            span = _STAGE_PROGRESS["analyze"] - base
            msg = "Whisper 转写"
            if done_sec and total_sec:
                msg = f"Whisper 转写 {done_sec / 60:.1f}/{total_sec / 60:.1f} 分钟"
                started = _transcribe_started[0]
                if started and ratio > 0.01:
                    eta = (time.time() - started) / ratio * (1 - ratio)
                    msg += f"（约剩 {eta / 60:.0f} 分钟）"
            _update_ingest_task(task_id, progress=round(base + span * float(ratio), 4),
                                message=msg)

        # 明细已在开始时登记，这里只更新状态（唯一键 platform_vid 保证不重复插入）
        rows = task_store.list_items(task_id)
        item_id = rows[0]["id"] if rows else None

        def _run_single() -> dict:
            if item_id:
                task_store.update_item(item_id, status="running", stage="running",
                                       stage_cn="开始处理")
            return _process_one_video(adapter, content, use_whisper,
                                      on_stage=_on_stage, on_progress=_on_progress,
                                      task_id=task_id)

        # V0.13 自动重试（docs/23 §3）：失败且可重试时退避后重跑本条
        payload = _process_with_auto_retry(
            process_fn=_run_single, item_id=item_id, task_id=task_id,
            attempt_strategy="whisper_local" if use_whisper else "platform_subtitle",
            on_retry=lambda n, w, e: _update_ingest_task(
                task_id, stage="retry",
                message=f"处理失败，{w}s 后自动重试（{n}/{task_store.MAX_AUTO_ATTEMPTS}）"),
        )
        payload.update({"ok": True, "task_id": task_id, "platform": platform})

        # 执行次数（attempt_count）已由 _process_with_auto_retry 计数，这里不重复 inc
        if item_id:
            task_store.update_item(item_id, status="completed", stage="done",
                                   stage_cn="完成",
                                   content_id=payload.get("content_id"))

        _update_ingest_task(task_id, status="success", stage="done", progress=1.0,
                            message="处理完成", finished_at=int(time.time()),
                            result=payload, total=1, succeeded=1, failed_count=0)
        try:
            n_pred = len(payload["predictions"] or [])
            notify_service.send_notification(
                "视频处理完成",
                f"《{(payload.get('title') or '')[:30]}》转写+AI 分析完成，提取 {n_pred} 条预测",
                category="auto_process",
                payload={"task_id": task_id, "content_id": payload.get("content_id"),
                         "title": payload.get("title"), "predictions": n_pred},
            )
        except Exception:
            pass
    except Exception as exc:
        # V0.13 错误三分类（docs/23 §1）：需人工处理的不得当作普通失败
        err_class = task_store.classify_error(exc)
        task_store.finish_attempt(attempt_id, "failed", error=str(exc), error_class=err_class)
        new_status = ("waiting_for_action"
                      if err_class == task_store.ERROR_NEEDS_ACTION else "failed")
        _update_ingest_task(task_id, status=new_status, error=str(exc),
                            message="需人工处理" if new_status == "waiting_for_action" else "处理失败",
                            finished_at=int(time.time()))
        print(f"[ingest] 任务 {task_id} → {new_status}（{err_class}）: {exc}")


def _run_ingest_job_all(task_id: str, adapter, raw_input: str,
                        use_whisper: bool, platform: str) -> None:
    """mode=all：解析博主 → 批量抓取全部视频 → 逐个处理。

    V0.13 契约要点（docs/22、docs/23）：

    - 单条失败只标记该条，批次继续（不整批中断）
    - 待处理集合过滤：跳过已完成 / 正在处理的作品
    - 遇到 `needs_action`（登录失效 / 验证码 / 限流 / 配额）立即停止整批，不绕过
    - 每条开始前检查暂停请求（安全暂停）
    - 批次最终状态由明细汇总（不变量 #4）
    """
    try:
        # 解析博主（拿 sec_user_id + 昵称）
        _update_ingest_task(task_id, stage="parse", progress=0.03,
                            message="解析博主身份")
        creator_key, creator_name, _ = adapter.resolve_creator(raw_input)

        # 批量抓取博主全部视频目录
        _update_ingest_task(task_id, stage="download", progress=0.05,
                            message=f"抓取「{creator_name or creator_key}」的视频目录")
        videos = adapter.fetch_creator_videos(creator_key, max_items=0)

        # JSON 审核（参考 json-audit）
        from .services.creator_monitor import _audit_catalog
        audit = _audit_catalog(videos)
        if not audit["ok"]:
            raise ValueError(audit["error"])
        if not videos:
            raise ValueError("未获取到该博主的任何视频")

        # V0.13 待处理集合过滤（docs/22 §6）
        videos, skipped = task_store.filter_pending_videos(platform, videos)
        if not videos:
            _update_ingest_task(task_id, status="success", stage="done", progress=1.0,
                                message=f"所选作品均已完成或正在处理中（跳过 {skipped} 条）",
                                finished_at=int(time.time()), total=0, succeeded=0,
                                failed_count=0)
            return

        total = len(videos)
        ok_count = 0
        fail_count = 0
        errors: list[str] = []
        processed: list[dict] = []

        # V0.13 明细落库（替代旧内存 items）
        task_store.add_items(task_id, [
            {"content_id": None,
             "platform_vid": getattr(v, "platform_vid", None),
             "title": (v.title or getattr(v, "platform_vid", "") or f"视频{i}")}
            for i, v in enumerate(videos, start=1)
        ])
        by_vid = {r["platform_vid"]: r for r in task_store.list_items(task_id)}
        _update_ingest_task(task_id, mode="all", total=total,
                            message=f"共 {total} 条待处理" + (f"（跳过 {skipped}）" if skipped else ""))

        stopped_for_action = False
        progress = 0.05
        for i, content in enumerate(videos):
            # 安全暂停：当前条处理完后不再取下一条（docs/23 §4）
            if task_store.is_pause_requested(task_id):
                task_store.mark_paused(task_id)
                print(f"[ingest-all] 任务 {task_id} 已暂停，剩余 {total - i} 条")
                return

            done = i + 1
            progress = 0.05 + 0.9 * (done / total)
            row = by_vid.get(getattr(content, "platform_vid", None))
            item_id = row["id"] if row else None
            _update_ingest_task(task_id, stage="analyze", progress=progress,
                                message=f"[{done}/{total}] 处理《{(content.title or '')[:20]}》")

            try:
                def _on_stage(stage_key: str, stage_cn: str) -> None:
                    if item_id:
                        task_store.update_item(item_id, stage=stage_key,
                                               stage_cn=stage_cn, status="running")

                def _on_progress(ratio: float, done_sec: float = 0.0,
                                 total_sec: float = 0.0) -> None:
                    # 批量：把本条占用的 0.9/total 进度区间按子进度插值
                    per = 0.9 / total
                    base = progress - per
                    msg = f"[{done}/{total}] 处理《{(content.title or '')[:20]}》"
                    if done_sec and total_sec:
                        msg += f" · 转写 {done_sec / 60:.1f}/{total_sec / 60:.1f} 分钟"
                    _update_ingest_task(
                        task_id, progress=round(base + per * float(ratio), 4),
                        message=msg)

                def _run_one() -> dict:
                    if item_id:
                        task_store.update_item(item_id, status="running", stage="running",
                                               stage_cn="开始处理")
                    return _process_one_video(adapter, content, use_whisper,
                                              on_stage=_on_stage, on_progress=_on_progress,
                                              task_id=task_id)

                # V0.13 自动重试（docs/23 §3）：临时故障退避重试，最多 3 次；
                # needs_action / non_retryable 会被立即抛出，交由下面分支处理
                payload = _process_with_auto_retry(
                    process_fn=_run_one, item_id=item_id, task_id=task_id,
                    attempt_strategy="whisper_local" if use_whisper else "platform_subtitle",
                    on_retry=lambda n, w, e: _update_ingest_task(
                        task_id, stage="retry",
                        message=f"[{done}/{total}] 失败，{w}s 后自动重试"
                                f"（{n}/{task_store.MAX_AUTO_ATTEMPTS}）"),
                )
                if item_id:
                    task_store.update_item(item_id, status="completed", stage="done",
                                           stage_cn="完成",
                                           content_id=payload.get("content_id"))
                processed.append(payload)
                ok_count += 1
            except Exception as exc:
                err_class = task_store.classify_error(exc)
                if item_id:
                    task_store.update_item(item_id, status="failed", stage="error",
                                           stage_cn="失败", error=str(exc)[:300],
                                           error_class=err_class)
                fail_count += 1
                errors.append(str(exc)[:120])
                print(f"[ingest-all] 第{done}条失败（{err_class}）: {exc}")

                # 需人工处理：立即停止整批，不继续、不切换通道绕过（docs/23 §7）
                if err_class == task_store.ERROR_NEEDS_ACTION:
                    stopped_for_action = True
                    break

        summary = task_store.summarize_task(task_id)
        result_payload = {
            "ok": True,
            "task_id": task_id,
            "platform": platform,
            "mode": "all",
            "creator": {"name": creator_name, "id": creator_key},
            "total": total,
            "processed": ok_count,
            "failed": fail_count,
            "skipped": skipped,
            "errors": errors[:20],
            "results": processed,
        }
        final_status = "waiting_for_action" if stopped_for_action else summary["status"]
        message = f"完成：成功 {ok_count}，失败 {fail_count}（共 {total}）"
        if skipped:
            message += f"，跳过已完成 {skipped}"
        if stopped_for_action:
            message = f"已停止，需人工处理（登录/验证码/限流/配额）：成功 {ok_count}，失败 {fail_count}"
        _update_ingest_task(task_id, status=final_status, stage="done",
                            progress=1.0 if not stopped_for_action else progress,
                            message=message, finished_at=int(time.time()),
                            result=result_payload, total=total,
                            succeeded=ok_count, failed_count=fail_count)
        try:
            notify_service.send_notification(
                "博主视频处理完成",
                f"「{creator_name or creator_key}」{total} 条视频：成功 {ok_count}，失败 {fail_count}",
                category="auto_process",
                payload={"task_id": task_id, "mode": "all", "total": total,
                         "processed": ok_count, "failed": fail_count},
            )
        except Exception:
            pass
    except Exception as exc:
        err_class = task_store.classify_error(exc)
        new_status = ("waiting_for_action"
                      if err_class == task_store.ERROR_NEEDS_ACTION else "failed")
        _update_ingest_task(task_id, status=new_status, error=str(exc),
                            message="需人工处理" if new_status == "waiting_for_action" else "处理失败",
                            finished_at=int(time.time()))
        print(f"[ingest-all] 任务 {task_id} → {new_status}（{err_class}）: {exc}")


def _safe_filename(name: str, max_len: int = 60) -> str:
    """把视频标题转成安全的文件名（去非法字符、截断）"""
    import re as _re
    s = _re.sub(r'[\\/:*?"<>|\n\r\t]', "", name or "未命名").strip()
    return s[:max_len] or "未命名"


def _save_transcript_files(content, result: dict, transcript) -> list[str]:
    """把简体校对版文案和 AI 总结保存为 markdown 文件到 data/transcripts/。

    返回保存的文件路径列表（best-effort，失败不影响主流程）。
    """
    saved = []
    try:
        out_dir = config.data_dir / "transcripts"
        out_dir.mkdir(parents=True, exist_ok=True)
        base = _safe_filename(content.title or content.platform_vid)
        simplified = result.get("transcript_simplified") or ""
        # 1) 简体校对版逐字稿
        if simplified:
            f1 = out_dir / f"{base}_简体校对版.md"
            f1.write_text(f"# {content.title}\n\n> 简体校对版（AI 转换）\n\n{simplified}\n",
                          encoding="utf-8")
            saved.append(str(f1))
        # 2) AI 总结（兼容 dict 或纯字符串）
        summary = result.get("summary") or {}
        if isinstance(summary, dict):
            summary_text = summary.get("summary") or ""
            key_points = summary.get("key_points") or []
        else:
            summary_text = str(summary)
            key_points = []
        if summary_text:
            f2 = out_dir / f"{base}_AI总结.md"
            body = f"# {content.title}\n\n## AI 总结\n\n{summary_text}\n"
            if key_points:
                body += "\n## 要点\n\n" + "\n".join(f"- {k}" for k in key_points) + "\n"
            f2.write_text(body, encoding="utf-8")
            saved.append(str(f2))
    except Exception as e:
        print(f"[ingest] 保存文案文件失败: {e}")
    return saved


@app.post("/api/ingest")
async def api_ingest(req: IngestRequest):
    """入口：粘贴链接/分享文案 → 提交异步任务，立即返回 task_id。

    长视频处理可能耗时十几分钟，改为后台线程执行，前端轮询
    GET /api/tasks/{id} 获取进度，完成后推送通知。
    """
    if not req.input.strip():
        raise HTTPException(400, "输入不能为空")
    task_id = _new_ingest_task(req.mode, req.input)
    threading.Thread(
        target=_run_ingest_job, args=(task_id, req.input, req.use_whisper, req.mode),
        daemon=True,
    ).start()
    return JSONResponse({
        "ok": True,
        "task_id": task_id,
        "status": "pending",
        "message": "任务已提交，正在后台处理",
        "task": get_ingest_task(task_id),
    })


@app.get("/api/tasks/{task_id}")
async def api_task_status(task_id: str):
    task = get_ingest_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@app.get("/api/tasks")
async def api_tasks_list(limit: int = 10):
    return {"tasks": get_ingest_tasks(limit)}


# ─── V0.13 任务控制（docs/23 §8）：重试失败项 / 继续剩余 / 暂停 ────────
def _restart_task_flow(task_id: str, use_whisper: bool = True) -> None:
    """重新驱动任务流程：会重新解析链接并按「待处理集合」过滤已完成的条目"""
    task = get_ingest_task(task_id)
    if not task:
        return
    threading.Thread(
        target=_run_ingest_job,
        args=(task_id, task.get("raw_input") or "", use_whisper, task.get("mode") or "single"),
        daemon=True,
    ).start()


@app.post("/api/tasks/{task_id}/retry-failed")
async def api_task_retry_failed(task_id: str):
    """重排失败条目后重启流程（已完成条目会被待处理过滤跳过）"""
    if not get_ingest_task(task_id):
        raise HTTPException(404, "任务不存在")
    n = await asyncio.to_thread(task_store.retry_failed_items, task_id)
    if n:
        _restart_task_flow(task_id)
    return {"ok": True, "requeued": n, "task": get_ingest_task(task_id)}


@app.post("/api/tasks/{task_id}/resume")
async def api_task_resume(task_id: str):
    """继续：处理剩余条目（跳过已完成）"""
    if not get_ingest_task(task_id):
        raise HTTPException(404, "任务不存在")
    ok = await asyncio.to_thread(task_store.request_resume, task_id)
    if not ok:
        raise HTTPException(409, "当前状态不可继续")
    _restart_task_flow(task_id)
    return {"ok": True, "task": get_ingest_task(task_id)}


@app.post("/api/tasks/{task_id}/pause")
async def api_task_pause(task_id: str):
    """安全暂停：当前视频处理完后停止（docs/23 §4）"""
    if not get_ingest_task(task_id):
        raise HTTPException(404, "任务不存在")
    ok = await asyncio.to_thread(task_store.request_pause, task_id)
    if not ok:
        raise HTTPException(409, "当前状态不可暂停")
    return {"ok": True, "task": get_ingest_task(task_id)}


@app.get("/api/predictions")
async def api_predictions(status: str = "", creator_id: str = ""):
    conn = get_conn()
    try:
        sql = (
            "SELECT p.*, cr.name AS creator_name, "
            "       c.title AS content_title, c.fetched_at AS content_fetched_at, "
            "       c.url AS content_url, c.platform AS content_platform, "
            "       v.ai_verdict, v.ai_confidence, v.ai_score "
            "FROM prediction p "
            "LEFT JOIN content c ON c.id = p.content_id "
            "LEFT JOIN creator cr ON cr.id = c.creator_id "
            "LEFT JOIN verification v ON v.prediction_id = p.id "
            "WHERE 1=1"
        )
        params: list = []
        if status:
            sql += " AND p.status=?"
            params.append(status)
        if creator_id:
            sql += " AND c.creator_id=?"
            params.append(creator_id)
        sql += " ORDER BY p.created_at DESC LIMIT 200"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/predictions/{prediction_id}")
async def api_prediction_detail(prediction_id: str):
    try:
        return get_verification_detail(prediction_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/predictions/{prediction_id}/review")
async def api_review_prediction(prediction_id: str, req: PredictionReviewRequest):
    """人工确认抽取结果是否为有效预测，并补齐/确认到期时间。"""
    try:
        return review_prediction(prediction_id, req.decision, req.due_at, req.notes)
    except ValueError as exc:
        if "不存在" in str(exc):
            raise HTTPException(404, str(exc)) from exc
        raise HTTPException(409, str(exc)) from exc


@app.delete("/api/predictions/{prediction_id}")
async def api_delete_prediction(prediction_id: str):
    """删除一条预测及其关联数据（验证/证据/语义索引/事件）。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM prediction WHERE id=?", (prediction_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"预测不存在: {prediction_id}")
        # 清理关联数据（事务）
        # 修订子预测
        conn.execute("UPDATE prediction SET parent_prediction_id=NULL WHERE parent_prediction_id=?", (prediction_id,))
        # 验证记录
        conn.execute("DELETE FROM verification WHERE prediction_id=?", (prediction_id,))
        # 证据
        conn.execute("DELETE FROM evidence WHERE prediction_id=?", (prediction_id,))
        # 语义索引
        conn.execute("DELETE FROM semantic_index WHERE target_type='prediction' AND target_id=?", (prediction_id,))
        # 事件日志
        conn.execute("DELETE FROM event_log WHERE entity_type='prediction' AND entity_id=?", (prediction_id,))
        # 预测本体
        conn.execute("DELETE FROM prediction WHERE id=?", (prediction_id,))
        conn.commit()
        return {"ok": True, "deleted": prediction_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"删除失败: {exc}") from exc
    finally:
        conn.close()


@app.post("/api/scheduler/run")
async def api_scheduler_run():
    changed = await asyncio.to_thread(scan_due_predictions)
    return {"ok": True, "marked_due": changed, "count": len(changed)}


@app.get("/api/verification/queue")
async def api_verification_queue(status: str = ""):
    return list_verification_queue(status)


@app.get("/api/verification/{prediction_id}")
async def api_verification_detail(prediction_id: str):
    try:
        return get_verification_detail(prediction_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/verification/{prediction_id}/run")
async def api_run_verification(prediction_id: str):
    try:
        result = await asyncio.to_thread(run_verification, prediction_id)
        _sync_verification_to_obsidian(result)
        return result
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"验证失败: {exc}") from exc


@app.post("/api/verification/{prediction_id}/evidence")
async def api_add_manual_evidence(prediction_id: str, req: ManualEvidenceRequest):
    try:
        return add_manual_evidence(
            prediction_id,
            title=req.title,
            summary=req.summary,
            url=req.url,
            published_at=req.published_at,
            relation=req.relation,
            source_type=req.source_type,
            publisher=req.publisher,
            credibility=req.credibility,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/verification/{prediction_id}/human")
async def api_submit_human_review(prediction_id: str, req: HumanVerificationRequest):
    try:
        result = submit_human_review(
            prediction_id,
            req.human_verdict,
            req.human_notes,
            req.human_score,
        )
        _sync_verification_to_obsidian(result)
        return result
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/verification/{prediction_id}/undo-auto-apply")
async def api_undo_auto_apply(prediction_id: str):
    """V0.3 硬预测自动过的 24h 撤销窗口"""
    try:
        return undo_auto_apply(prediction_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def _sync_verification_to_obsidian(result: dict) -> None:
    if not obsidian.active:
        return
    prediction = result.get("prediction") or {}
    verification = result.get("verification") or {}
    try:
        obsidian.write_verification(
            creator_name=prediction.get("creator_name") or "未知博主",
            video_title=prediction.get("video_title") or "",
            video_url=prediction.get("video_url") or "",
            prediction=prediction,
            evidence=result.get("evidence") or [],
            verification=verification,
        )
    except Exception as exc:
        print(f"[obsidian] 验证报告写出失败: {exc}")


@app.get("/api/creators")
async def api_creators():
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM content co WHERE co.creator_id=c.id) AS content_count "
            "FROM creator c ORDER BY c.name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/creators/{creator_id}/avatar/refresh")
async def api_refresh_avatar(creator_id: str):
    """重新下载该创作者头像（换头像/之前下载失败时用）。"""
    from .services.pipeline import sync_creator_avatar

    rel = await asyncio.to_thread(sync_creator_avatar, creator_id)
    if rel is None:
        raise HTTPException(400, "没有可用的头像地址（该创作者入库时未拿到头像）")
    return {"ok": True, "avatar_path": rel, "url": f"/avatars/{Path(rel).name}"}


@app.post("/api/creators/avatars/backfill")
async def api_backfill_avatars():
    """批量补齐所有缺头像的创作者（历史数据迁移用）。"""
    from .services.pipeline import sync_creator_avatar

    conn = get_conn()
    try:
        ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM creator WHERE avatar_path IS NULL AND avatar_url IS NOT NULL"
            ).fetchall()
        ]
    finally:
        conn.close()
    done = []
    for cid in ids:
        try:
            rel = await asyncio.to_thread(sync_creator_avatar, cid)
            if rel:
                done.append(cid)
        except Exception:
            continue
    return {"ok": True, "total": len(ids), "downloaded": len(done)}


@app.get("/api/creators/{creator_id}")
async def api_creator_profile(creator_id: str):
    """V0.3 博主画像：可靠性统计 + 已验证预测明细（含校准点）"""
    try:
        return get_creator_profile(creator_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/contents")
async def api_contents(limit: int = 50):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT co.*, cr.name AS creator_name FROM content co "
            "LEFT JOIN creator cr ON cr.id=co.creator_id ORDER BY co.fetched_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            raw = d.pop("raw_meta_json", None)
            try:
                d["raw_meta"] = json.loads(raw) if raw else {}
            except Exception:
                d["raw_meta"] = {}
            items.append(d)
        return items
    finally:
        conn.close()


@app.get("/api/contents/{content_id}/claims")
async def api_content_claims(content_id: str):
    """V0.9 返回某视频的全部观点（claim），按 importance 降序。

    供预测页视频分组展开时展示「全部观点」。
    """
    conn = get_conn()
    try:
        content = conn.execute(
            "SELECT id FROM content WHERE id=?", (content_id,)
        ).fetchone()
        if not content:
            raise HTTPException(404, "视频不存在")
        rows = conn.execute(
            "SELECT text, category, topic, stance, importance, confidence, "
            "support, key_phrase, start_offset, end_offset, created_at "
            "FROM claim WHERE content_id=? "
            "ORDER BY COALESCE(importance, 0) DESC, created_at DESC",
            (content_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── V0.15 AI 总结读取（版本化存储，见 docs/25-内容总结与版本.md）──────
@app.get("/api/contents/{content_id}/summary")
async def api_content_summary(content_id: str):
    """返回该视频**当前生效**版本的 AI 总结（含要点）。

    总结按版本化存储：重跑生成新版本、旧版保留不覆盖。
    此处只取 is_current=1 的那条；历史版本走 `/summary/versions` 对比。
    无总结时返回 `summary: null`（不视为错误，老视频可能尚未生成）。
    """
    record = await asyncio.to_thread(summary_store.get_current_summary, content_id)
    return {"content_id": content_id, "summary": record}


@app.get("/api/contents/{content_id}/summary/versions")
async def api_content_summary_versions(content_id: str):
    """列出该视频的全部总结版本（新→旧），供多模型 / 多提示词产出对比。"""
    versions = await asyncio.to_thread(summary_store.list_versions, content_id)
    return {"content_id": content_id, "count": len(versions), "versions": versions}


@app.get("/api/contents/{content_id}/transcript")
async def api_content_transcript(content_id: str):
    """返回该视频的逐字稿正文（供前端直接阅读）。

    优先返回 **AI 简体校对版**；老数据没有简体版时回退原始逐字稿，
    并用 `text_kind` 标明当前返回的是哪一种（见 `app/services/transcript_store.py`）。

    无逐字稿时返回 `text: null`（该视频可能只抓了目录、尚未处理）。
    """
    result = await asyncio.to_thread(transcript_store.get_transcript, content_id)
    return result if result else {"content_id": content_id, "text": None}


@app.get("/api/contents/{content_id}/delete-preview")
async def api_content_delete_preview(content_id: str):
    """删除前预览影响面：会连带删除多少条预测 / 观点 / 证据 / 总结。

    前端在二次确认弹窗中展示这些数字，避免误删带走分析数据。
    """
    result = await asyncio.to_thread(content_store.preview_delete, content_id)
    if not result:
        raise HTTPException(404, "视频不存在")
    return result


@app.delete("/api/contents/{content_id}")
async def api_delete_content(content_id: str):
    """删除视频及其全部下游数据（**不可恢复**）。

    连带删除：逐字稿 / 观点 / 预测 / 证据 / 验证 / AI 总结。
    任务与尝试历史**保留**（明细只断开 `content_id` 引用，不丢操作日志）。
    """
    result = await asyncio.to_thread(content_store.delete_content, content_id)
    if not result:
        raise HTTPException(404, "视频不存在")
    return {"ok": True, **result}


# ─── V0.4 关注监控（参考 douyin-creator-distill 的「关注与更新」）────────
@app.get("/api/subscriptions")
async def api_subscriptions():
    return creator_monitor.list_subscriptions()


@app.post("/api/subscriptions")
async def api_add_subscription(req: SubscriptionRequest):
    """加入关注：解析博主主页 → 建立订阅 → 立即抓一次目录建立基线"""
    try:
        result = await asyncio.to_thread(
            creator_monitor.add_subscription,
            req.input, req.platform, req.check_interval_hours, req.auto_process,
        )
        return result
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"加入关注失败: {exc}") from exc


@app.get("/api/subscriptions/{sub_id}")
async def api_subscription_detail(sub_id: str):
    try:
        return creator_monitor.get_subscription(sub_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.patch("/api/subscriptions/{sub_id}")
async def api_update_subscription(sub_id: str, req: SubscriptionUpdateRequest):
    try:
        return creator_monitor.update_subscription(
            sub_id,
            enabled=req.enabled,
            check_interval_hours=req.check_interval_hours,
            auto_process=req.auto_process,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/api/subscriptions/{sub_id}")
async def api_remove_subscription(sub_id: str):
    """取消关注（软删除，保留已抓取历史资产）"""
    try:
        return creator_monitor.remove_subscription(sub_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/subscriptions/{sub_id}/check")
async def api_check_subscription(sub_id: str):
    """立即抓取一次该博主主页目录"""
    try:
        result = await asyncio.to_thread(creator_monitor.run_check, sub_id, "manual")
        if result.get("busy"):
            raise HTTPException(409, result.get("message", "抓取进行中"))
        return result
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"抓取失败: {exc}") from exc


@app.get("/api/subscriptions/{sub_id}/videos")
async def api_subscription_videos(sub_id: str):
    """该关注源下已入库的视频列表（含是否已转写）"""
    try:
        return creator_monitor.get_subscription_videos(sub_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/crawl_tasks")
async def api_crawl_tasks(limit: int = 20):
    return creator_monitor.list_crawl_tasks(limit)


# ─── V0.4 通知中心（站内 + Webhook + 邮件）──────────────────
@app.get("/api/notifications")
async def api_notifications(limit: int = 50, unread_only: bool = False):
    return notify_service.list_notifications(limit, unread_only)


@app.post("/api/notifications/read")
async def api_notifications_read(req: NotificationReadRequest):
    return notify_service.mark_read(req.id)


@app.delete("/api/notifications")
async def api_notifications_clear():
    return notify_service.clear_notifications()


@app.post("/api/notifications/test")
async def api_notifications_test(req: NotificationTestRequest):
    """人工触发一条测试通知，验证各通道可用性"""
    results = await asyncio.to_thread(
        notify_service.send_test_with, req.title, req.body,
    )
    return {"ok": True, "results": results}


@app.post("/api/monitor/tick")
async def api_monitor_tick():
    """手动触发一次监控调度检查"""
    results = await asyncio.to_thread(creator_monitor.monitor_tick)
    return {"ok": True, "ran": len(results)}


# ─── V0.7 抖音账号与登录态（参考 douyin-creator-distill 的账号体系）──────
@app.get("/api/account")
async def api_account_overview():
    """账号总览（脱敏：不含 Cookie 明文与浏览器目录绝对路径）"""
    return account_service.overview()


@app.post("/api/account")
async def api_account_config(req: AccountConfigRequest):
    """写入账号配置（登录态浏览器目录）"""
    return account_service.save_profile_path(req.profile_path)


@app.post("/api/account/cookie")
async def api_account_cookie(req: AccountCookieRequest):
    """手动写入 Cookie：写配置 + 导出 Netscape 文件"""
    try:
        return await asyncio.to_thread(account_service.save_cookie, req.cookie)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/account/login")
async def api_account_login(req: AccountLoginRequest):
    """启动可见浏览器登录窗口（独立子进程，不阻塞服务）"""
    try:
        return await asyncio.to_thread(account_service.start_login, req.profile_path)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"启动登录窗口失败: {exc}") from exc


@app.get("/api/account/login/status")
async def api_account_login_status():
    """登录阶段（前端轮询：启动中/等待登录/已登录/已超时/窗口已关闭）"""
    return account_service.read_login_status()


@app.post("/api/account/export-cookie")
async def api_account_export_cookie():
    """从登录态浏览器目录导出 Cookie 文件（不返回明文）"""
    try:
        return await asyncio.to_thread(account_service.export_cookie_from_profile)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"导出 Cookie 失败: {exc}") from exc


# ─── V0.12 系统设置（AI / 搜索 / 通知 / Obsidian / 验证参数可视化配置）──────
@app.get("/api/settings")
async def api_get_settings():
    """读取全部可配置段（密钥脱敏）+ 运行时状态"""
    return settings_service.get_settings()


@app.patch("/api/settings")
async def api_update_settings(req: dict):
    """局部写入配置：密钥留空/掩码 → 保留原值，null → 清空；写入后热重载运行时"""
    try:
        return await asyncio.to_thread(settings_service.update_settings, req)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/settings/ai/test")
async def api_test_ai_connection():
    """发一次最小真实请求验证云端 AI 配置是否可用"""
    try:
        return await asyncio.to_thread(settings_service.test_ai_connection)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"AI 连接测试失败: {exc}") from exc


@app.get("/api/dashboard")
async def api_dashboard():
    conn = get_conn()
    try:
        counts = {}
        for status in ("pending_review", "active", "due", "human_review", "final"):
            counts[status] = conn.execute(
                "SELECT COUNT(*) AS n FROM prediction WHERE status=?", (status,)
            ).fetchone()["n"]
        # 仪表盘 featured 创作者：优先选已验证样本最多的，否则选最近有内容的创作者
        featured = None
        try:
            row = conn.execute(
                "SELECT c.id FROM creator c LEFT JOIN creator_reliability r ON r.creator_id=c.id "
                "WHERE c.id IN (SELECT DISTINCT creator_id FROM content)"
                "ORDER BY COALESCE(r.verified_count,0) DESC, c.added_at DESC LIMIT 1"
            ).fetchone()
            if row:
                prof = get_creator_profile(row["id"])
                rel = prof.get("reliability")
                if rel is None:
                    rel = {
                        "verified_count": 0, "base_accuracy": None,
                        "calibration_score": None, "sample_size_warning": None,
                    }
                points = [
                    p["calibration_point"] for p in (prof.get("predictions") or [])
                    if p.get("calibration_point")
                ]
                featured = {
                    "id": prof.get("id"),
                    "name": prof.get("name") or "未知博主",
                    "platform_id": prof.get("platform_id") or "",
                    "platform": prof.get("platform") or "",
                    "avatar_url": prof.get("avatar_url") or "",
                    "avatar_path": prof.get("avatar_path") or "",
                    "reliability": rel,
                    "prediction_count": prof.get("prediction_count") or 0,
                    "calibration_points": points[:50],
                }
        except Exception as exc:
            print(f"[dashboard] featured 创作者加载失败: {exc}")
            featured = None
        return {
            "counts": counts,
            "verification_queue": counts["due"] + counts["human_review"],
            "updated_at": int(time.time()),
            "featured_creator": featured,
        }
    finally:
        conn.close()


async def _scheduler_loop():
    interval = int(config.get("verification", "scheduler_interval_sec", default=3600))
    while True:
        try:
            await asyncio.to_thread(scan_due_predictions)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[scheduler] 扫描失败: {exc}")
        await asyncio.sleep(max(60, interval))


_scheduler_task: asyncio.Task | None = None


def _backfill_content_stats() -> int:
    """存量回填：把历史 content 的 raw_meta.stats 回填到互动字段。

    V0.6 之前的 content 只把互动数据存进 raw_meta_json，没有独立字段。
    启动时扫描一次，把 stats 里的 digg/comment/share/collect 回填。
    """
    conn = get_conn()
    fixed = 0
    try:
        rows = conn.execute(
            "SELECT id, raw_meta_json FROM content "
            "WHERE raw_meta_json IS NOT NULL AND raw_meta_json != '' "
            "AND digg_count IS NULL"
        ).fetchall()
        for r in rows:
            try:
                meta = json.loads(r["raw_meta_json"])
            except Exception:
                continue
            stats = meta.get("stats") if isinstance(meta, dict) else None
            if not isinstance(stats, dict):
                continue

            def _to_int(v):
                try:
                    return int(v) if v is not None else None
                except (TypeError, ValueError):
                    return None

            d = _to_int(stats.get("digg_count"))
            c = _to_int(stats.get("comment_count"))
            s = _to_int(stats.get("share_count"))
            co = _to_int(stats.get("collect_count"))
            if d is None and c is None and s is None and co is None:
                continue
            conn.execute(
                "UPDATE content SET digg_count=?, comment_count=?, "
                "share_count=?, collect_count=? WHERE id=?",
                (d, c, s, co, r["id"]),
            )
            fixed += 1
        conn.commit()
        if fixed:
            print(f"[backfill] 回填 {fixed} 条 content 的互动数据")
    finally:
        conn.close()
    return fixed


def _resume_interrupted_tasks(limit: int = 3) -> int:
    """V0.14 断点续跑：重启后重新驱动未完成的任务（docs/22 §5）。

    只处理「状态为 queued 且仍有待处理明细」的任务，并限制并发数量，
    避免重启瞬间惊群。无 `raw_input` 的历史任务跳过（无法重跑）。
    """
    resumed = 0
    for task in task_store.list_tasks(limit=20):
        if resumed >= limit:
            break
        if task["status"] != "queued" or not task_store.has_pending_items(task["id"]):
            continue
        raw = (task.get("raw_input") or "").strip()
        if not raw:
            continue
        print(f"[ingest] 断点续跑 {task['id'][:8]}（mode={task.get('mode')}）")
        threading.Thread(
            target=_run_ingest_job,
            args=(task["id"], raw, True, task.get("mode") or "single"),
            daemon=True,
        ).start()
        resumed += 1
    return resumed


@app.on_event("startup")
async def start_scheduler():
    global _scheduler_task
    await asyncio.to_thread(backfill_missing_baselines)
    await asyncio.to_thread(_backfill_content_stats)
    # V0.13 断点恢复：上次中断的 running 任务/明细退回 queued（docs/23 §5）
    await asyncio.to_thread(task_store.recover_interrupted)
    # V0.14 断点续跑：重新驱动仍有待处理明细的任务
    await asyncio.to_thread(_resume_interrupted_tasks)
    if config.get("verification", "scheduler_enabled", default=True):
        _scheduler_task = asyncio.create_task(_scheduler_loop())
    # V0.4 关注监控调度器
    creator_monitor.start_monitor()


@app.on_event("shutdown")
async def stop_scheduler():
    global _scheduler_task
    creator_monitor.stop_monitor()
    if _scheduler_task:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        _scheduler_task = None


@app.get("/")
async def index():
    """默认首页：Vue 模块化前端（static/vue/index.html）。"""
    vue_index = VUE_DIR / "index.html"
    if not vue_index.exists():
        raise HTTPException(503, "前端产物缺失，请先执行 npm run build")
    return FileResponse(vue_index)


# V0.7 SPA 路由回退：前端子路由（/monitoring、/predictions 等）直接访问或刷新时
# 返回前端入口，避免 404；API 与静态资源路径保持原样（仍返回 JSON / 404）
_SPA_EXCLUDE_PREFIXES = (
    "/api", "/assets", "/static", "/avatars", "/docs", "/redoc", "/openapi.json",
)


@app.exception_handler(StarletteHTTPException)
async def spa_fallback_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404 and not request.url.path.startswith(_SPA_EXCLUDE_PREFIXES):
        vue_index = VUE_DIR / "index.html"
        if vue_index.exists():
            return FileResponse(vue_index)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


# ─── 语义检索（参考 douyin-creator-distill 的智能检索）──────────
@app.get("/api/search")
async def api_search(query: str, limit: int = 20):
    """语义检索：视频 + 预测"""
    if not query.strip():
        return {"query": query, "results": [], "count": 0}
    try:
        result = await asyncio.to_thread(semantic_service.search, query, limit)
        result["count"] = len(result.get("results", []))
        return result
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/search/index")
async def api_search_index(req: IndexRequest):
    """为视频（content）建立语义索引"""
    try:
        result = await asyncio.to_thread(semantic_service.index_contents)
        return {"ok": True, **result}
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/search/index/predictions")
async def api_search_index_predictions():
    """为预测（prediction）建立语义索引"""
    try:
        result = await asyncio.to_thread(semantic_service.index_predictions)
        return {"ok": True, **result}
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/semantic/settings")
async def api_semantic_settings():
    """语义检索设置（模型列表/安装状态/激活）"""
    return semantic_service.get_settings()


@app.post("/api/semantic/select")
async def api_semantic_select(model_id: str):
    """切换激活的 embedding 模型"""
    try:
        return semantic_service.set_active_model(model_id)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/semantic/download")
async def api_semantic_download(model_id: str, source: str = "modelscope"):
    """后台下载 embedding 模型"""
    try:
        return semantic_service.start_download(model_id, source)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/semantic/download/{model_id}")
async def api_semantic_download_state(model_id: str):
    return semantic_service.get_download_state(model_id)


@app.post("/api/semantic/delete")
async def api_semantic_delete(model_id: str):
    """删除模型及其索引"""
    try:
        return semantic_service.delete_model(model_id)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/status")
async def api_status():
    rt = semantic_service.runtime()
    return {
        "app": "creator-insight",
        "version": "0.6.0",
        "ai_cloud": bool(config.get("ai", "cloud", "api_key")),
        "ai_local": bool(config.get("ai", "local_fallback", "enabled")),
        "obsidian_enabled": obsidian.active,
        "semantic": rt,
        "db": str(config.db_path),
    }


init_db()
