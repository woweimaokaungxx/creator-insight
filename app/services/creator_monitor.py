"""关注监控服务（V0.4）——参考 douyin-creator-distill 的「关注与更新」设计

把博主「加入关注」后：
  1) 解析博主主页（如抖音 sec_user_id）并创建订阅
  2) 定时/手动抓取主页作品目录（web API 分页）
  3) JSON 审核：videoId 唯一、必填字段、异作者检测、数量对账
  4) 增量发现：与基线对比，仅上报新视频并写入 content 表（UNIQUE 去重）
  5) 可选 auto_process：新视频自动串行走完整 ingest（下载+转写+AI）
"""
from __future__ import annotations

import json
import threading
import time
import uuid

from ..adapters import get_adapter
from ..adapters.base import ContentInfo
from ..config import config
from ..db import get_conn, log_event
from .pipeline import _upsert_content, _upsert_creator, ingest_pipeline
from .transcribe import whisper_transcribe

# 监控 tick 间隔（秒）
MONITOR_TICK_SEC = 60

# 各订阅抓取锁（防止并发重复抓取同一账号）
_locks: dict[str, threading.Lock] = {}
_monitor_thread: threading.Thread | None = None
_stop_flag = threading.Event()


# ─── 工具 ──────────────────────────────────────────────────
def _parse_json(s: str | None) -> dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


def _parse_video_ids(s: str | None) -> set[str]:
    try:
        return set(json.loads(s or "[]"))
    except Exception:
        return set()


def _parse_video_ids_list(s: str | None) -> list[str]:
    try:
        return json.loads(s or "[]")
    except Exception:
        return []


def _lock_for(sub_id: str) -> threading.Lock:
    return _locks.setdefault(sub_id, threading.Lock())


# ─── 订阅（关注）管理 ──────────────────────────────────────
def add_subscription(raw_input: str, platform: str = "douyin",
                     check_interval_hours: int = 24,
                     auto_process: bool = False) -> dict:
    """把博主加入关注：解析主页 → 建订阅 → 立即抓一次目录建立基线"""
    adapter = get_adapter(platform)
    sec_user_id, nickname, profile_url = adapter.resolve_creator(raw_input)
    now = int(time.time())
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT * FROM subscription WHERE platform=? AND source_key=?",
            (platform, sec_user_id),
        ).fetchone()
        if existing and existing["deleted_at"] is None:
            raise ValueError("该博主已在关注列表中")
        sub_id = str(uuid.uuid4())
        if existing and existing["deleted_at"] is not None:
            # 重新启用（取消关注后再次加入，保留历史基线）
            sub_id = existing["id"]
            conn.execute(
                "UPDATE subscription SET deleted_at=NULL, enabled=1, display_name=?, "
                "source=?, check_interval_hours=?, auto_process=?, next_check_at=?, updated_at=? "
                "WHERE id=?",
                (nickname, profile_url, check_interval_hours, int(auto_process),
                 now + check_interval_hours * 3600, now, sub_id),
            )
        else:
            conn.execute(
                "INSERT INTO subscription (id, platform, source_key, source, display_name, "
                "enabled, check_interval_hours, auto_process, next_check_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
                (sub_id, platform, sec_user_id, profile_url, nickname,
                 check_interval_hours, int(auto_process),
                 now + check_interval_hours * 3600, now, now),
            )
        conn.commit()
        log_event(conn, "subscription", sub_id, "created",
                  json.dumps({"name": nickname}, ensure_ascii=False))
    finally:
        conn.close()

    # 加入后立即抓一次目录，建立基线
    result = run_check(sub_id, trigger="manual")
    return get_subscription(sub_id, with_result=result)


def list_subscriptions() -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM content c JOIN creator cr ON cr.id=c.creator_id "
            "  WHERE cr.platform=s.platform AND cr.platform_id=s.source_key) AS content_count "
            "FROM subscription s WHERE s.deleted_at IS NULL "
            "ORDER BY s.created_at DESC"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["last_result"] = _parse_json(d.pop("last_result_json"))
            d["baseline_count"] = len(_parse_video_ids(d.get("baseline_video_ids_json")))
            result.append(d)
        return result
    finally:
        conn.close()


def get_subscription(sub_id: str, with_result: dict | None = None) -> dict:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM subscription WHERE id=? AND deleted_at IS NULL", (sub_id,)
        ).fetchone()
        if not row:
            raise ValueError("订阅不存在或已取消关注")
        d = dict(row)
        d["last_result"] = _parse_json(d.pop("last_result_json"))
        d["baseline_count"] = len(_parse_video_ids(d.get("baseline_video_ids_json")))
        last_task = conn.execute(
            "SELECT * FROM crawl_task WHERE subscription_id=? ORDER BY created_at DESC LIMIT 1",
            (sub_id,),
        ).fetchone()
        d["last_task"] = dict(last_task) if last_task else {}
        if with_result is not None:
            d["check_result"] = with_result
        return d
    finally:
        conn.close()


def update_subscription(sub_id: str, *, enabled: bool | None = None,
                        check_interval_hours: int | None = None,
                        auto_process: bool | None = None) -> dict:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM subscription WHERE id=? AND deleted_at IS NULL", (sub_id,)
        ).fetchone()
        if not row:
            raise ValueError("订阅不存在或已取消关注")
        fields, params = [], []
        if enabled is not None:
            fields.append("enabled=?")
            params.append(1 if enabled else 0)
        if check_interval_hours is not None:
            fields.append("check_interval_hours=?")
            params.append(int(check_interval_hours))
        if auto_process is not None:
            fields.append("auto_process=?")
            params.append(1 if auto_process else 0)
        if fields:
            fields.append("updated_at=?")
            params.append(int(time.time()))
            params.append(sub_id)
            conn.execute(
                f"UPDATE subscription SET {', '.join(fields)} WHERE id=?", params
            )
            conn.commit()
        return dict(conn.execute(
            "SELECT * FROM subscription WHERE id=?", (sub_id,)
        ).fetchone())
    finally:
        conn.close()


def get_subscription_videos(sub_id: str) -> list[dict]:
    """该关注源下已入库的视频列表（含是否已转写）"""
    conn = get_conn()
    try:
        sub = conn.execute(
            "SELECT * FROM subscription WHERE id=? AND deleted_at IS NULL", (sub_id,)
        ).fetchone()
        if not sub:
            raise ValueError("订阅不存在或已取消关注")
        rows = conn.execute(
            "SELECT co.*, cr.name AS creator_name, "
            "  EXISTS(SELECT 1 FROM transcript t WHERE t.content_id=co.id) AS has_transcript "
            "FROM content co JOIN creator cr ON cr.id=co.creator_id "
            "WHERE cr.platform=? AND cr.platform_id=? "
            "ORDER BY co.published_at DESC LIMIT 200",
            (sub["platform"], sub["source_key"]),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def remove_subscription(sub_id: str) -> dict:
    """取消关注 = 软删除，保留已抓取的历史资产"""
    now = int(time.time())
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM subscription WHERE id=? AND deleted_at IS NULL", (sub_id,)
        ).fetchone()
        if not row:
            raise ValueError("订阅不存在或已取消关注")
        conn.execute(
            "UPDATE subscription SET deleted_at=?, enabled=0, updated_at=? WHERE id=?",
            (now, now, sub_id),
        )
        conn.commit()
        log_event(conn, "subscription", sub_id, "removed")
        return {"ok": True, "id": sub_id}
    finally:
        conn.close()


# ─── 目录抓取 + 审核 + 增量 ───────────────────────────────
def run_check(sub_id: str, trigger: str = "manual") -> dict:
    """抓取一次博主主页目录：审核 → 增量 → 新视频入库 →（可选）自动处理"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM subscription WHERE id=? AND deleted_at IS NULL", (sub_id,)
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError("订阅不存在或已取消关注")

    lock = _lock_for(sub_id)
    if not lock.acquire(blocking=False):
        return {"ok": False, "busy": True, "message": "该博主正在抓取中，请稍候"}

    task_id = str(uuid.uuid4())
    now = int(time.time())
    try:
        _insert_crawl_task(task_id, sub_id, trigger, now)

        adapter = get_adapter(row["platform"])
        videos = adapter.fetch_creator_videos(row["source_key"])

        # JSON 审核（参考 json-audit：唯一性/必填字段/异作者/数量对账）
        audit = _audit_catalog(videos)
        if not audit["ok"]:
            raise RuntimeError(audit["error"])

        baseline = _parse_video_ids(row["baseline_video_ids_json"])
        new_videos = [v for v in videos if v.platform_vid not in baseline]
        # 合并基线：保留历史，避免主页删除视频后旧视频被重复报新
        merged_baseline = baseline | {v.platform_vid for v in videos}

        # 新视频元数据入库（已有记录跳过，仅推进基线）
        stored = _store_new_contents(row, new_videos)

        # 昵称兜底：若订阅名是 sec_user_id（主页链接解析失败），用目录里的真实昵称修正
        display_name = row["display_name"]
        if not display_name or display_name == row["source_key"]:
            real_name = next((v.creator_name for v in videos if v.creator_name), "")
            if real_name and real_name != row["source_key"]:
                display_name = real_name

        conn = get_conn()
        try:
            conn.execute(
                "UPDATE subscription SET last_checked_at=?, last_success_at=?, last_error=NULL, "
                "next_check_at=?, baseline_video_ids_json=?, last_result_json=?, updated_at=?, "
                "display_name=? WHERE id=?",
                (now, now, now + int(row["check_interval_hours"] or 24) * 3600,
                 json.dumps(sorted(merged_baseline), ensure_ascii=False),
                 json.dumps({"trigger": trigger, "total": len(videos),
                             "new": len(stored), "audit": audit}, ensure_ascii=False),
                 now, display_name, sub_id),
            )
            conn.commit()
        finally:
            conn.close()
        _finish_crawl_task(
            task_id, "success", total=len(videos),
            new_ids=[c["platform_vid"] for c in stored],
        )

        # 通知：发现新视频
        if stored:
            _notify_new_videos(row, stored)

        # auto_process：后台串行走完整管线（下载+转写+AI）
        if row["auto_process"] and stored:
            threading.Thread(
                target=_auto_process_videos, args=(sub_id, stored), daemon=True
            ).start()

        return {
            "ok": True, "total": len(videos), "new": len(stored),
            "new_videos": stored, "audit": audit,
        }
    except Exception as exc:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE subscription SET last_checked_at=?, last_error=?, updated_at=? WHERE id=?",
                (int(time.time()), str(exc), int(time.time()), sub_id),
            )
            conn.commit()
        finally:
            conn.close()
        _finish_crawl_task(task_id, "error", error=str(exc))
        return {"ok": False, "error": str(exc)}
    finally:
        lock.release()


def _audit_catalog(videos: list[ContentInfo]) -> dict:
    """审核目录 JSON：唯一性、必填字段、异作者、数量对账、互动数据完整度。

    参考 douyin-creator-distill 的 json-audit：
      - blocking 字段缺失 → 整体失败（锁定入库）
      - 互动数据（赞/评/转/藏）缺失 → 记录缺失统计（警告，不阻塞，因为部分内容可能无互动）
    """
    seen: dict[str, int] = {}
    for v in videos:
        if v.platform_vid:
            seen[v.platform_vid] = seen.get(v.platform_vid, 0) + 1
    dupes = [k for k, n in seen.items() if n > 1]
    missing = [v.platform_vid for v in videos if not v.platform_vid or not v.url]
    authors = {v.creator_platform_id for v in videos if v.creator_platform_id}
    # 互动数据完整度统计
    stats_missing = {
        "likes": sum(1 for v in videos if v.digg_count is None),
        "comments": sum(1 for v in videos if v.comment_count is None),
        "shares": sum(1 for v in videos if v.share_count is None),
        "collects": sum(1 for v in videos if v.collect_count is None),
    }
    # blocking 字段（参考 json-audit）：ID、URL、作者
    if dupes:
        return {"ok": False,
                "error": f"目录审核失败：出现重复作品 {len(dupes)} 条（示例 {dupes[:3]}）"}
    if missing:
        return {"ok": False,
                "error": f"目录审核失败：{len(missing)} 条作品缺少必填字段（ID/URL）"}
    if not videos:
        return {"ok": False,
                "error": "目录为空：可能被风控或博主无作品，请检查 monitor.douyin_cookie 配置"}
    if len(authors) > 1:
        return {"ok": False,
                "error": f"目录审核失败：检测到 {len(authors)} 个不同作者，数据异常"}
    return {
        "ok": True, "total": len(videos), "authors": len(authors),
        "stats_missing": stats_missing,
        "warning": f"互动数据缺失: 赞{stats_missing['likes']} 评{stats_missing['comments']} "
                   f"转{stats_missing['shares']} 藏{stats_missing['collects']}" if any(stats_missing.values()) else "",
    }


def _store_new_contents(sub_row, videos: list[ContentInfo]) -> list[dict]:
    now = int(time.time())
    conn = get_conn()
    try:
        stored: list[dict] = []
        for v in videos:
            exists = conn.execute(
                "SELECT id FROM content WHERE platform=? AND platform_vid=?",
                (v.platform, v.platform_vid),
            ).fetchone()
            if exists:
                continue  # 已在库（可能手动 ingest 过），仅推进基线
            creator_id = _upsert_creator(conn, v, now)
            content_id = _upsert_content(conn, v, creator_id, now)
            row = conn.execute(
                "SELECT co.*, cr.name AS creator_name FROM content co "
                "JOIN creator cr ON cr.id=co.creator_id WHERE co.id=?",
                (content_id,),
            ).fetchone()
            if row:
                stored.append(dict(row))
        conn.commit()
        if stored:
            log_event(conn, "subscription", sub_row["id"], "new_videos",
                      json.dumps([c["platform_vid"] for c in stored], ensure_ascii=False))
        return stored
    finally:
        conn.close()


# ─── 自动处理（下载+转写+AI 完整管线）──────────────────────
def _row_to_content_info(c: dict, sub_row) -> ContentInfo:
    return ContentInfo(
        platform=c["platform"],
        platform_vid=c["platform_vid"],
        title=c["title"] or "",
        url=c["url"],
        creator_platform_id=sub_row["source_key"],
        creator_name=sub_row["display_name"],
        published_at=c["published_at"],
        duration_sec=c["duration_sec"],
        raw_meta=json.loads(c["raw_meta_json"] or "{}"),
    )


def _auto_process_videos(sub_id: str, contents: list[dict]) -> None:
    """后台串行对每个新视频执行完整 ingest（下载+Whisper 转写+AI 抽取）"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM subscription WHERE id=?", (sub_id,)).fetchone()
    conn.close()
    if not row:
        return
    adapter = get_adapter(row["platform"])
    media_dir = config.data_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    for c in contents:
        try:
            conn = get_conn()
            has_tr = conn.execute(
                "SELECT 1 FROM transcript WHERE content_id=?", (c["id"],)
            ).fetchone()
            conn.close()
            if has_tr:
                continue
            content = _row_to_content_info(c, row)
            media_path = adapter.download_media(content, media_dir)
            if not media_path:
                print(f"[monitor] {c['platform_vid']} 下载失败，跳过自动处理")
                continue
            result = whisper_transcribe(media_path)
            if not result:
                print(f"[monitor] {c['platform_vid']} 转写失败，跳过自动处理")
                continue
            ingest_result = ingest_pipeline(
                content, result["text_full"],
                segments=result["segments"], transcript_source="whisper_local",
            )
            _maybe_write_obsidian(content, ingest_result)
            _notify_auto_process(row, c, success=True,
                                 detail=f"提取预测 {len(ingest_result.get('predictions', []))} 条")
            print(f"[monitor] 自动处理完成 {c['platform_vid']}")
        except Exception as exc:
            _notify_auto_process(row, c, success=False, detail=str(exc))
            print(f"[monitor] 自动处理 {c['platform_vid']} 失败: {exc}")


def _maybe_write_obsidian(content: ContentInfo, result: dict) -> None:
    try:
        from .obsidian import obsidian
    except Exception:
        return
    if not obsidian.active:
        return
    for p in result.get("predictions", []):
        obsidian.write_prediction(
            creator_name=content.creator_name or "未知博主",
            content={"title": content.title, "url": content.url},
            prediction=p,
        )


# ─── 通知辅助（V0.4）──────────────────────────────────────
def _notify_new_videos(sub_row, stored: list[dict]) -> None:
    """订阅源发现新视频 → 站内/Webhook/邮件通知"""
    try:
        from .notify import send_notification
        titles = "、".join((c.get("title") or c["platform_vid"])[:30] for c in stored[:5])
        more = f"等 {len(stored)} 个" if len(stored) > 5 else ""
        send_notification(
            f"{sub_row['display_name']} 发布 {len(stored)} 个新视频",
            f"{titles}{more}",
            category="new_videos",
            payload={"subscription_id": sub_row["id"],
                     "platform": sub_row["platform"],
                     "new": len(stored)},
        )
    except Exception as exc:
        print(f"[monitor] 新视频通知失败: {exc}")


def _notify_auto_process(sub_row, c: dict, *, success: bool, detail: str) -> None:
    """自动处理（下载+转写+AI）完成/失败 → 通知"""
    try:
        from .notify import send_notification
        name = c.get("title") or c["platform_vid"]
        if success:
            send_notification(
                f"自动处理完成 · {sub_row['display_name']}",
                f"{name}：{detail}",
                category="auto_process",
                payload={"subscription_id": sub_row["id"],
                         "video": c["platform_vid"], "ok": True},
            )
        else:
            send_notification(
                f"自动处理失败 · {sub_row['display_name']}",
                f"{name}：{detail}",
                category="auto_process",
                payload={"subscription_id": sub_row["id"],
                         "video": c["platform_vid"], "ok": False},
            )
    except Exception as exc:
        print(f"[monitor] 自动处理通知失败: {exc}")


# ─── 抓取任务记录 ─────────────────────────────────────────
def _insert_crawl_task(task_id: str, sub_id: str, trigger: str, created_at: int) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO crawl_task (id, subscription_id, trigger, status, created_at) "
            "VALUES (?, ?, ?, 'running', ?)",
            (task_id, sub_id, trigger, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def _finish_crawl_task(task_id: str, status: str, *, total: int | None = None,
                       new_ids: list[str] | None = None,
                       error: str | None = None) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE crawl_task SET status=?, total_videos=?, new_video_ids_json=?, "
            "error=?, finished_at=? WHERE id=?",
            (status, total, json.dumps(new_ids or [], ensure_ascii=False),
             error, int(time.time()), task_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_crawl_tasks(limit: int = 20) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT t.*, s.display_name FROM crawl_task t "
            "LEFT JOIN subscription s ON s.id=t.subscription_id "
            "ORDER BY t.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["new_video_ids"] = _parse_video_ids_list(d.pop("new_video_ids_json"))
            result.append(d)
        return result
    finally:
        conn.close()


# ─── 调度器 ───────────────────────────────────────────────
def monitor_tick() -> list[dict]:
    """每次 tick：取一个到期订阅串行抓取，避免并发触发风控"""
    if not config.get("monitor", "enabled", default=True):
        return []
    now = int(time.time())
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id FROM subscription WHERE enabled=1 AND deleted_at IS NULL "
            "AND next_check_at <= ? ORDER BY next_check_at ASC LIMIT 1",
            (now,),
        ).fetchall()
    finally:
        conn.close()
    results = []
    for row in rows:
        results.append(run_check(row["id"], trigger="schedule"))
    return results


def _monitor_loop() -> None:
    while not _stop_flag.is_set():
        try:
            monitor_tick()
        except Exception as exc:
            print(f"[monitor] tick 失败: {exc}")
        _stop_flag.wait(MONITOR_TICK_SEC)


def start_monitor() -> None:
    global _monitor_thread, _stop_flag
    if _monitor_thread and _monitor_thread.is_alive():
        return
    _stop_flag.clear()
    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
    _monitor_thread.start()


def stop_monitor() -> None:
    global _monitor_thread
    _stop_flag.set()
    if _monitor_thread:
        _monitor_thread.join(timeout=5)
        _monitor_thread = None
