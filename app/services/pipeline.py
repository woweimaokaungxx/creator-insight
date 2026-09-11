"""抽取 Pipeline：Transcript → 总结/Claim/Prediction → 入库 → Baseline Snapshot"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Optional

from ..adapters.base import ContentInfo
from ..ai import (
    EXTRACT_CLAIM_SYSTEM, EXTRACT_PREDICTION_SYSTEM, PARSE_TIME_SYSTEM,
    SIMPLIFY_SYSTEM, SUMMARIZE_SYSTEM, TASK_EXTRACT_CLAIM,
    TASK_EXTRACT_PREDICTION, TASK_PARSE_TIME, TASK_SIMPLIFY,
    TASK_SUMMARIZE, ai_gateway,
)
from ..config import config
from ..db import get_conn, log_event
from ..models import Magnitude, PredictionIn
from .verification import _ensure_baseline

# 置信度语气词 → 估计概率（校准用：只作为初始值，后续由 calibration 修正）
CONFIDENCE_MAP = {
    "可能": 0.35, "或许": 0.3, "也许": 0.3, "说不定": 0.35, "大概": 0.45,
    "我觉得": 0.4, "我认为": 0.5, "倾向于": 0.55, "大概率": 0.7, "很可能": 0.75,
    "高概率": 0.8, "基本确定": 0.85, "肯定": 0.9, "一定": 0.92, "我敢打赌": 0.8,
}
FUZZY_WORDS = ["可能", "或许", "大概", "说不定", "也许", "我觉得", "我猜"]

# 告诫/劝阻/反讽词：命中说明是观点而非可证伪预测（P0 后处理过滤）
NEGATION_WORDS = [
    "别幻想", "别指望", "别想", "不要以为", "别以为", "想多了", "做梦吧",
    "别想太多", "不抱幻想", "不要幻想", "别期待着", "别期待",
]
# 无时间锚点的泛泛断言（P0 后处理过滤，可验证性不足）
VAGUE_TIME_WORDS = ["长期", "迟早", "终究", "总有一天", "早晚", "最终会", "总会"]
# 否定/保留表达：命中说明博主的确定性低，AI 置信度需要下调（P1-1）
CONFIDENCE_DOWNGRADE_WORDS = [
    "不会", "不可能", "不至于", "很难说", "不一定", "未必", "不好说",
    "说不准", "难说", "够呛", "悬", "别指望",
]


def simplify_transcript(text_full: str) -> str:
    """将繁体/带错字的逐字稿交由 AI 转成简体校对版。

    Whisper 直出常为繁体，且可能有识别错字。这里让 AI 统一转简体、
    修错字，产出干净的简体校对版文案。失败时返回原文本兜底。
    """
    text_full = (text_full or "").strip()
    if not text_full:
        return ""
    try:
        system = SIMPLIFY_SYSTEM
        user = f"以下是视频逐字稿：\n\n{text_full[:12000]}"
        result, _ = ai_gateway.chat(system, user, TASK_SIMPLIFY)
        simplified = (result.get("simplified") or "").strip()
        return simplified or text_full
    except Exception as e:
        print(f"[simplify] 简体化失败，使用原文: {e}")
        return text_full


def summarize_transcript(text_full: str) -> dict:
    system = SUMMARIZE_SYSTEM
    user = f"以下是视频逐字稿：\n\n{text_full[:8000]}"
    result, provider = ai_gateway.chat(system, user, TASK_SUMMARIZE)
    result["provider"] = provider
    # 字数校验兜底：AI 返回 word_count 异常或 summary 过短时，提示但不阻断
    summary = (result.get("summary") or "").strip()
    actual = len(summary)
    result["word_count_actual"] = actual
    if summary and actual < 150:
        result["word_count_warning"] = (
            f"AI 返回 summary 仅 {actual} 字，未达到 200 字目标，可能存在内容省略。"
        )
        print(f"[summarize] 警告: {result['word_count_warning']}")
    return result


def extract_claims(text_full: str, published_at: int | None = None) -> list[dict]:
    system = EXTRACT_CLAIM_SYSTEM
    time_ctx = _video_time_context(published_at)
    user = (
        (time_ctx + "\n\n" if time_ctx else "")
        + f"以下是视频逐字稿（含时间戳段）：\n\n{_with_offsets(text_full)}"
    )
    result, provider = ai_gateway.chat(system, user, TASK_EXTRACT_CLAIM)
    claims = result.get("claims", [])
    for c in claims:
        c["provider"] = provider
        # 规范化新增元信息：保证范围与默认值，避免脏数据入库
        try:
            c["importance"] = max(0.0, min(1.0, float(c.get("importance") or 0.0)))
        except (TypeError, ValueError):
            c["importance"] = 0.0
        try:
            c["confidence"] = max(0.0, min(1.0, float(c.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            c["confidence"] = 0.0
        if c.get("stance") not in {"支持", "反对", "中性", "unknown", None}:
            c["stance"] = "unknown"
        if not c.get("topic"):
            c["topic"] = "general"
    # 按 importance 降序
    claims.sort(key=lambda x: x.get("importance", 0.0), reverse=True)
    return claims


def _sanitize_prediction_dict(p: dict) -> dict:
    """清洗 AI 返回的预测 dict：null 顶替字符串/嵌套结构缺字段等常见瑕疵兜底，
    避免 pydantic 因显式 None 校验失败而丢预测（P1-2 修复）。"""
    p = dict(p or {})
    # 顶层字符串字段：None → 默认空串
    for key, default in (("raw_text", ""), ("interpreted_intent", ""),
                         ("speaker", "博主本人"), ("time_expression_raw", ""),
                         ("confidence_raw", ""), ("inference_notes", ""),
                         ("direction_source", None)):
        if p.get(key) is None:
            p[key] = default
    # subject：整体 None 或内部 name/symbol None
    subj = p.get("subject")
    if subj is None:
        p["subject"] = {}
    elif isinstance(subj, dict):
        s = dict(subj)
        if s.get("name") is None:
            s["name"] = ""
        if s.get("symbol") is None:
            s["symbol"] = ""
        p["subject"] = s
    # time_window：整体 None 或 raw/granularity None
    tw = p.get("time_window")
    if tw is None:
        p["time_window"] = {}
    elif isinstance(tw, dict):
        t = dict(tw)
        if t.get("raw") is None:
            t["raw"] = ""
        if t.get("granularity") is None:
            t["granularity"] = "fuzzy"
        p["time_window"] = t
    # magnitude：内部 unit/is_relative None
    mag = p.get("magnitude")
    if isinstance(mag, dict):
        m = dict(mag)
        if m.get("unit") is None:
            m["unit"] = ""
        if m.get("is_relative") is None:
            m["is_relative"] = True
        p["magnitude"] = m
    # conditions：None → 空数组
    if p.get("conditions") is None:
        p["conditions"] = []
    # 枚举兜底：subject.type / direction 非法值 → 交给模型默认值
    if p.get("subject") and isinstance(p["subject"], dict):
        if p["subject"].get("type") not in {"stock", "index", "fx", "commodity",
                                            "macro_indicator", "policy_event",
                                            "company_event", "unknown"}:
            p["subject"]["type"] = "unknown"
    if p.get("direction") not in {"up", "down", "flat", "range", "above", "below",
                                  "event_yes", "event_no", "unknown"}:
        p["direction"] = "unknown"
    return p


def extract_predictions(text_full: str, creator_name: str | None = None,
                        published_at: int | None = None) -> list[PredictionIn]:
    system = EXTRACT_PREDICTION_SYSTEM
    time_ctx = _video_time_context(published_at)
    user = (
        (time_ctx + "\n\n" if time_ctx else "")
        + f"以下是视频逐字稿：\n\n{_with_offsets(text_full)}"
    )
    # P2-2 博主画像上下文：历史验证记录校准置信度
    if creator_name:
        profile = _creator_profile_context(creator_name)
        if profile:
            system = system + "\n\n" + profile
    result, provider = ai_gateway.chat(system, user, TASK_EXTRACT_PREDICTION)
    preds = []
    for p in result.get("predictions", []):
        try:
            p = _sanitize_prediction_dict(p)
            pred = PredictionIn(**p)
            # 补充语气词概率
            if pred.confidence_score is None:
                pred.confidence_score = _estimate_confidence(pred.confidence_raw)
            if pred.confidence_raw in FUZZY_WORDS:
                pred.needs_human_confirmation = True
            # P1-2 幅度兜底：AI 漏填幅度时从原话规则提取
            pred = _normalize_magnitude(pred)
            # 意图推断标记：direction 是否为推断、AI 把握是否足够、时间是否模糊
            pred = _apply_intent_heuristics(pred)
            # 后处理过滤：只丢弃无预测意图的（告诫/完全不可验证）
            if not _is_plausible_prediction(pred):
                print(f"[extract_predictions] 过滤非预测: {pred.raw_text[:40]}")
                continue
            preds.append(pred)
        except Exception as e:
            print(f"[extract_predictions] 跳过异常条目: {e} raw={p}")
    return preds


def _is_plausible_prediction(pred: PredictionIn) -> bool:
    """后处理校验：只丢弃【没有预测意图】的条目，不丢弃【说得模糊】的条目。

    设计原则（用户要求"判断意图"而非"摘录原话"）：
    · 告诫/劝阻/反讽 → 博主意图是评论，不是预言 → 丢弃
    · 时间模糊（"长期会涨"）→ 有预测意图，只是缺时间 → **保留**，标 fuzzy + 待人工
    · 完全不可验证（无方向+无幅度+无时间）→ 丢弃
    """
    raw = pred.raw_text or ""
    time_expr = pred.time_expression_raw or ""
    # 1) 告诫/劝阻/反讽句 → 不是预测意图
    if any(w in raw for w in NEGATION_WORDS):
        return False
    # 2) 完全不可验证：方向未知 + 无幅度 + 无时间
    has_direction = pred.direction not in ("unknown", "")
    has_magnitude = bool(pred.magnitude and pred.magnitude.has_value)
    has_time = bool(time_expr.strip())
    if not has_direction and not has_magnitude and not has_time:
        return False
    return True


def _apply_intent_heuristics(pred: PredictionIn) -> PredictionIn:
    """按意图推断结果补充标记：推断成分越多，越需要人工确认。

    · direction 是 AI 推断的 → 标待确认
    · AI 对"这是预测"把握低（intent_confidence < 0.7）→ 标待确认
    · 时间模糊（缺时间词或命中"长期/迟早"等）→ 标 fuzzy + 待确认
    """
    # direction 推断而来
    if pred.direction_source == "inferred":
        pred.needs_human_confirmation = True
    # AI 自身把握不足
    if pred.intent_confidence is not None and pred.intent_confidence < 0.7:
        pred.needs_human_confirmation = True
    # P1-1：否定/保留表达 → 置信下调（即使 AI 已给 confidence_score 也执行）
    raw = pred.raw_text or ""
    if any(w in raw for w in CONFIDENCE_DOWNGRADE_WORDS) and pred.confidence_score is not None:
        pred.confidence_score = round(pred.confidence_score * 0.5, 2)
    # P1-1：direction 是推断的 → 置信上限 0.6
    if pred.direction_source == "inferred" and pred.confidence_score is not None:
        pred.confidence_score = min(pred.confidence_score, 0.6)
    # 时间模糊
    time_expr = (pred.time_expression_raw or "").strip()
    tw = pred.time_window
    vague_hit = any(w in (pred.raw_text or "") for w in VAGUE_TIME_WORDS)
    if not time_expr or vague_hit or tw.is_fuzzy:
        tw.is_fuzzy = True
        tw.requires_human_confirmation = True
        pred.needs_human_confirmation = True
    return pred


def _creator_profile_context(creator_name: str) -> str | None:
    """P2-2：按博主名查历史验证记录，生成给 prompt 的画像上下文。

    返回形如：
    【博主历史校准】博主「老陈讲财经」历史已验证 12 条预测，正确率 67%。
    请据此校准 confidence_score：历史准确的博主可给略高置信，反之略低。
    若无可查记录则返回 None（不影响抽取）。
    """
    try:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT cr.id, "
                "(SELECT COUNT(*) FROM prediction p JOIN verification v ON v.prediction_id=p.id "
                " WHERE p.content_id IN (SELECT id FROM content WHERE creator_id=cr.id) "
                " AND v.final_verdict IS NOT NULL) AS verified, "
                "(SELECT COUNT(*) FROM prediction p JOIN verification v ON v.prediction_id=p.id "
                " WHERE p.content_id IN (SELECT id FROM content WHERE creator_id=cr.id) "
                " AND v.final_verdict='correct') AS correct "
                "FROM creator cr WHERE cr.name=? OR cr.platform_id=? LIMIT 1",
                (creator_name, creator_name),
            ).fetchone()
        finally:
            conn.close()
        if not row or not row["verified"]:
            return None
        accuracy = (row["correct"] or 0) / row["verified"]
        hint = "较高" if accuracy >= 0.6 else "偏低"
        return (
            f"【博主历史校准】博主「{creator_name}」历史已验证 {row['verified']} 条预测，"
            f"正确率 {accuracy:.0%}（{hint}）。"
            f"请据此校准 confidence_score：历史准确的博主，同样语气可给略高置信；反之略低。"
        )
    except Exception as exc:
        print(f"[ingest] 博主画像上下文获取失败: {exc}")
        return None


def _normalize_magnitude(pred: PredictionIn) -> PredictionIn:
    """P1-2 幅度兜底：AI 漏填幅度时，从 raw_text 规则提取（不依赖 AI 输出）。

    只处理明确数字表达；无数字则保持 null（不编造）。
    """
    if pred.magnitude and pred.magnitude.has_value:
        return pred  # AI 已给幅度，不动
    import re as _re
    raw = pred.raw_text or ""
    mag = pred.magnitude if pred.magnitude else Magnitude()

    # 1) 涨跌幅：涨/跌/升/降 + 百分比
    m = _re.search(r"(?:涨|升|增|跌|降|回落|反弹)(?:超|达|了|约|至)?\s*([0-9.]+)\s*%", raw)
    if m:
        val = float(m.group(1)) / 100.0
        mag.min = val
        mag.unit = "ratio"
        mag.is_relative = True
        pred.magnitude = mag
        return pred

    # 2) 目标位：站上/突破/涨到/升至 + 数字（threshold）
    m = _re.search(r"(?:站上|突破|涨到|升至|触及|来到|回到|达到)\s*([0-9]+(?:\.[0-9]+)?)", raw)
    if m:
        val = float(m.group(1))
        mag.min = val
        mag.unit = "threshold"
        mag.is_relative = False
        pred.magnitude = mag
        return pred

    # 3) 跌破/下探 + 数字（threshold，方向 down）
    m = _re.search(r"(?:跌破|下探|跌到|回落至|回落到)\s*([0-9]+(?:\.[0-9]+)?)", raw)
    if m:
        val = float(m.group(1))
        mag.max = val
        mag.unit = "threshold"
        mag.is_relative = False
        pred.magnitude = mag
        return pred

    # 4) 区间：X~Y / X到Y
    m = _re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:~|～|至|到|和)\s*([0-9]+(?:\.[0-9]+)?)", raw)
    if m:
        mag.min = float(m.group(1))
        mag.max = float(m.group(2))
        mag.unit = "range"
        mag.is_relative = False
        pred.magnitude = mag
        return pred

    # 5) 翻倍/翻番
    if "翻倍" in raw or "翻番" in raw:
        mag.min = 1.0
        mag.unit = "ratio"
        mag.is_relative = True
        pred.magnitude = mag
        return pred

    return pred


def _with_offsets(text_full: str) -> str:
    """传给 AI 的逐字稿：优先全文（P0：长视频不截断，避免漏抽后半段预测）。

    超过 30000 字时按段落切分，保留含明确时间词/预测词的后半部分，
    并标注当前处理的是第几段。
    """
    text_full = (text_full or "").strip()
    if not text_full:
        return ""
    if len(text_full) <= 30000:
        return text_full
    # 超长：按段落分块，返回最后一段（预测常在结尾）+ 提示
    chunks = [c for c in text_full.split("\n\n") if c.strip()]
    tail = "\n\n".join(chunks[-3:])  # 取最后三段（预测通常在结尾总结处）
    return f"[逐字稿过长，以下为结尾部分（前文已省略）]\n\n{tail}"


def _video_time_context(published_at: int | None) -> str:
    """生成视频发布时间上下文，供 AI 抽取时判断相对时间词与回顾/预言。

    例：Video published at 2026-08-24 01:38（Unix 1786937400）
    """
    if not published_at:
        return ""
    try:
        local = time.localtime(published_at)
        human = time.strftime("%Y-%m-%d %H:%M", local)
        return f"视频发布时间：{human}（博主在此时点发表，请据此判断\"今年/明年/本季度\"等相对时间）"
    except Exception:
        return ""


def _estimate_confidence(confidence_raw: str) -> Optional[float]:
    if not confidence_raw:
        return None
    for word, score in CONFIDENCE_MAP.items():
        if word in confidence_raw:
            return score
    return None


def parse_time(raw_time: str, prediction_at: int) -> dict:
    system = PARSE_TIME_SYSTEM
    user = json.dumps({"raw_time": raw_time, "prediction_at": prediction_at}, ensure_ascii=False)
    result, provider = ai_gateway.chat(system, user, TASK_PARSE_TIME)
    result["provider"] = provider
    return result


def compute_auto_apply(pred: PredictionIn) -> bool:
    """硬预测判定（docs/05）：结构化标的+幅度+方向+非模糊时间+非模糊语气"""
    if pred.subject.type not in {"stock", "index", "fx", "commodity", "macro_indicator"}:
        return False
    if not pred.magnitude or (pred.magnitude.min is None and pred.magnitude.max is None):
        return False
    if pred.direction in {"unknown", ""}:
        return False
    if pred.time_window.is_fuzzy:
        return False
    if pred.confidence_raw and any(w in pred.confidence_raw for w in FUZZY_WORDS):
        return False
    return True


def ingest_pipeline(content: ContentInfo, transcript_text: str,
                    segments: list[dict] | None = None,
                    transcript_source: str = "whisper_local",
                    on_stage: callable = None) -> dict:
    """执行完整抽取管线并入库，返回摘要

    注意：AI 调用耗时数分钟，期间绝不持有写事务（否则 uvicorn 服务会
    报 database is locked）。因此拆成两段：
      段1（短）: creator/content/transcript 写入并立即 commit，释放锁
      段2（长）: AI 抽取（无事务），完成后新开连接写入 claims/predictions

    on_stage: 可选回调 on_stage(stage_key, stage_cn)，用于批量任务时
              实时反馈该视频正在执行哪一步（简体校对/AI总结/观点抽取/预测抽取）。
    """
    def _report(stage: str, cn: str) -> None:
        if on_stage:
            try:
                on_stage(stage, cn)
            except Exception:
                pass

    now = int(time.time())
    # ── 段1：基础数据（快速提交） ──
    conn = get_conn()
    try:
        creator_id = _upsert_creator(conn, content, now)
        content_id = _upsert_content(conn, content, creator_id, now)
        _upsert_transcript(conn, content_id, transcript_text, segments, transcript_source, now)
        conn.commit()   # ← 立即释放写锁
    finally:
        conn.close()

    # ── 段2：AI 抽取（不持事务；NVIDIA 免费 key 有限流，调用间加间隔） ──
    import time as _time
    # 简体校对：Whisper 直出常为繁体，交由 AI 转简体
    simplified_text = ""
    try:
        _report("simplify", "简体校对")
        simplified_text = simplify_transcript(transcript_text)
        _time.sleep(3)
    except Exception as e:
        print(f"[ingest] 简体化失败: {e}")
    # 用简体文本作为后续 AI 抽取的输入，保证产出全部为简体
    ai_text = simplified_text or transcript_text
    summary = None
    try:
        _report("summarize", "AI 总结")
        summary = summarize_transcript(ai_text)
        _time.sleep(3)   # 限流缓冲
    except Exception as e:
        print(f"[ingest] 总结失败: {e}")
    claims = []
    try:
        _report("claims", "观点抽取")
        claims = extract_claims(ai_text, published_at=content.published_at)
        _time.sleep(3)
    except Exception as e:
        print(f"[ingest] Claim 抽取失败: {e}")
    preds = []
    try:
        _report("predictions", "预测抽取")
        preds = extract_predictions(ai_text, creator_name=content.creator_name,
                                    published_at=content.published_at)
    except Exception as e:
        print(f"[ingest] Prediction 抽取失败: {e}")

    # ── 段3：写入抽取结果（新连接，短事务） ──
    conn = get_conn()
    try:
        if simplified_text:
            conn.execute(
                "UPDATE transcript SET text_full_simplified=? WHERE content_id=?",
                (simplified_text, content_id),
            )
        _insert_claims(conn, content_id, claims, now)
        stored_predictions = [
            _insert_prediction(conn, content_id, p, now) for p in preds
        ]
        conn.commit()
        log_event(conn, "content", content_id, "ingested",
                  json.dumps({"summary": bool(summary), "claims": len(claims),
                              "predictions": len(preds),
                              "simplified": bool(simplified_text)},
                             ensure_ascii=False))
    finally:
        conn.close()

    # 头像下载（best-effort，失败不影响入库结果）
    try:
        sync_creator_avatar(creator_id)
    except Exception:
        pass

    return {
        "content_id": content_id,
        "creator_id": creator_id,
        "summary": summary,
        "claims": claims,
        "predictions": stored_predictions,
        "transcript_simplified": simplified_text,
    }


def _upsert_creator(conn, content: ContentInfo, now: int) -> str:
    import sqlite3
    cid = str(uuid.uuid4())
    pid = content.creator_platform_id or content.creator_name or content.platform_vid
    conn.execute(
        "INSERT OR IGNORE INTO creator (id, platform, platform_id, name, url, "
        "avatar_url, domain_tags, added_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, content.platform, pid, content.creator_name or "未知博主",
         f"{content.platform}://creator/{pid}",
         content.creator_avatar_url or None, "[]", now),
    )
    row = conn.execute(
        "SELECT id FROM creator WHERE platform=? AND platform_id=?",
        (content.platform, pid),
    ).fetchone()
    if not row:
        conn.execute("UPDATE creator SET name=? WHERE id=?",
                     (content.creator_name or "未知博主", cid))
        return cid
    # 已存在：头像缺失时补上远程地址
    creator_id = row["id"]
    if content.creator_avatar_url:
        cur = conn.execute(
            "SELECT avatar_url FROM creator WHERE id=?", (creator_id,)
        ).fetchone()
        if cur and not cur["avatar_url"]:
            conn.execute("UPDATE creator SET avatar_url=? WHERE id=?",
                         (content.creator_avatar_url, creator_id))
    return creator_id


def sync_creator_avatar(creator_id: str) -> str | None:
    """把创作者的远程头像下载到本地并回写 avatar_path（best-effort）。"""
    from ..db import get_conn
    from .avatar import download_avatar

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, platform, platform_id, avatar_url, avatar_path, avatar_src "
            "FROM creator WHERE id=?", (creator_id,)
        ).fetchone()
        if not row or not row["avatar_url"]:
            return None
        # 已下载过 且 来源 URL 未变 且 文件非小图残片 → 跳过（避免每次处理都重复下载）
        if row["avatar_path"]:
            from .avatar import avatar_file_path
            local = avatar_file_path(row["avatar_path"])
            if local and row["avatar_src"] == row["avatar_url"]:
                # 历史遗留小图（<8KB）强制重下为高清档（V0.10 修复）
                if local.stat().st_size >= 8 * 1024:
                    return row["avatar_path"]
        # 首次下载 / URL 变化 / 本地是小图残片 → 重新下载（download_avatar 内部会升格高清档）
        rel = download_avatar(row["avatar_url"], row["platform"], row["platform_id"])
        if rel:
            conn.execute(
                "UPDATE creator SET avatar_path=?, avatar_src=? WHERE id=?",
                (rel, row["avatar_url"], creator_id),
            )
            conn.commit()
        return rel
    except Exception as exc:
        print(f"[avatar] 同步失败 {creator_id}: {exc}")
        return None
    finally:
        conn.close()


def _upsert_content(conn, content: ContentInfo, creator_id: str, now: int) -> str:
    import sqlite3
    cid = str(uuid.uuid4())
    conn.execute(
        "INSERT OR IGNORE INTO content (id, creator_id, platform, platform_vid, title, url, "
        "published_at, duration_sec, fetched_at, raw_meta_json, "
        "digg_count, comment_count, share_count, collect_count) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, creator_id, content.platform, content.platform_vid, content.title,
         content.url, content.published_at, content.duration_sec, now,
         json.dumps(content.raw_meta, ensure_ascii=False, default=str),
         content.digg_count, content.comment_count,
         content.share_count, content.collect_count),
    )
    row = conn.execute(
        "SELECT id FROM content WHERE platform=? AND platform_vid=?",
        (content.platform, content.platform_vid),
    ).fetchone()
    if row:
        # 已存在时回填互动数据（新抓取到的覆盖缺失值）
        conn.execute(
            "UPDATE content SET digg_count=COALESCE(?, digg_count), "
            "comment_count=COALESCE(?, comment_count), "
            "share_count=COALESCE(?, share_count), "
            "collect_count=COALESCE(?, collect_count) WHERE id=?",
            (content.digg_count, content.comment_count,
             content.share_count, content.collect_count, row["id"]),
        )
        return row["id"]
    return cid


def _upsert_transcript(conn, content_id: str, text_full: str,
                       segments: list[dict] | None, source: str, now: int,
                       text_full_simplified: str | None = None) -> None:
    import sqlite3
    tid = str(uuid.uuid4())
    conn.execute(
        "INSERT OR IGNORE INTO transcript "
        "(id, content_id, source, text_full, text_full_simplified, segments_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tid, content_id, source, text_full, text_full_simplified,
         json.dumps(segments or [], ensure_ascii=False), now),
    )


def _insert_claims(conn, content_id: str, claims: list[dict], now: int) -> None:
    for c in claims:
        conn.execute(
            "INSERT OR IGNORE INTO claim (id, content_id, text, speaker, start_offset, end_offset, "
            "category, topic, stance, importance, confidence, support, key_phrase, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), content_id, c.get("text", ""), "博主本人",
             c.get("start_offset"), c.get("end_offset"), c.get("category", "opinion"),
             c.get("topic"), c.get("stance"), c.get("importance"), c.get("confidence"),
             c.get("support"), c.get("key_phrase"), now),
        )


_CN_NUM = r"(?:[一二两三四五六七八九十几多]|\d+)"

_TIME_HINT_PATTERNS = [
    # 1) 固定短语优先（避免被数字模式拆错："两三年"→"三年"）
    (r"今年下半年|明年上半年|今年底|明年底|本季度|下季度|未来半年|下半年", lambda m: m.group(0)),
    (r"两三年|三五年|中短期|长期|本轮周期", lambda m: m.group(0)),
    (r"今年|明年|后年", lambda m: m.group(0)),
    # 2) "未来/今后 X天/周/个月/月/年（内）"——阿拉伯或中文数字，保留"内"
    (rf"(未来|今后|接下来|往后)\s*({_CN_NUM})\s*(天|周|个月|月|年)(内|之内|里)",
     lambda m: f"未来{m.group(2)}{m.group(3)}{m.group(4)}"),
    (rf"(未来|今后|接下来|往后)\s*({_CN_NUM})\s*(天|周|个月|月|年)",
     lambda m: f"未来{m.group(2)}{m.group(3)}"),
    # 3) "X年内 / X个月内"
    (rf"({_CN_NUM})\s*年(内|之内)", lambda m: f"{m.group(1)}年内"),
    (rf"({_CN_NUM})\s*个月?(内|之内)", lambda m: f"{m.group(1)}个月内"),
]


def _extract_time_hint(text: str) -> str | None:
    """V0.9：从 AI 总结（interpreted_intent）中提取上下文时间词。

    原话没给明确时间时，AI 总结里可能带了推断的时间范围（如"未来一年内""明年"）。
    这里用规则提取，命中则作为 time_expression_raw 传给 parse_time 做绝对时间解析。
    """
    text = text or ""
    for pattern, fmt in _TIME_HINT_PATTERNS:
        m = re.search(pattern, text)
        if m:
            return fmt(m)
    return None


def _insert_prediction(conn, content_id: str, p: PredictionIn, now: int) -> dict:
    """入库 + 时间解析 + 预测时点 Baseline 快照"""
    content_row = conn.execute(
        "SELECT published_at FROM content WHERE id=?", (content_id,)
    ).fetchone()
    prediction_at = (
        content_row["published_at"] if content_row and content_row["published_at"] else now
    )
    tw = p.time_window

    # P2-1 时间解析：AI 抽取时若未解析，这里真正调用 parse_time 推断
    time_expr = (p.time_expression_raw or "").strip()
    # V0.9 兜底：原话没给时间词时，尝试从 AI 总结（interpreted_intent）中提取上下文时间词
    if not time_expr and p.interpreted_intent:
        intent_time = _extract_time_hint(p.interpreted_intent)
        if intent_time:
            time_expr = intent_time
    if not tw.parsed_end and time_expr:
        try:
            parsed = parse_time(time_expr, prediction_at)
            if parsed.get("parsed_end"):
                tw.parsed_start = parsed.get("parsed_start") or tw.parsed_start
                tw.parsed_end = parsed.get("parsed_end")
                tw.granularity = parsed.get("granularity") or tw.granularity
                tw.is_fuzzy = bool(parsed.get("is_fuzzy", tw.is_fuzzy))
                tw.requires_human_confirmation = bool(
                    parsed.get("requires_human_confirmation", False)
                )
            elif parsed.get("is_fuzzy"):
                tw.is_fuzzy = True
                tw.requires_human_confirmation = True
        except Exception as exc:
            print(f"[ingest] 时间解析失败 {time_expr!r}: {exc}")
            tw.is_fuzzy = True
            tw.requires_human_confirmation = True

    # 模糊的标记人工，不猜
    if tw.is_fuzzy or tw.requires_human_confirmation or not tw.parsed_end:
        due_at = None
        tw.requires_human_confirmation = True
        tw.is_fuzzy = True
    else:
        due_at = tw.parsed_end

    auto_apply = compute_auto_apply(p)
    pid = str(uuid.uuid4())
    conn.execute(
        "INSERT OR IGNORE INTO prediction (id, content_id, revision_no, raw_text, "
        "interpreted_intent, intent_confidence, direction_source, speaker, "
        "start_offset, end_offset, subject, direction, magnitude, time_expression_raw, time_window, "
        "conditions, confidence_raw, confidence_score, status, prediction_at, due_at, "
        "auto_apply_eligible, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, content_id, 1, p.raw_text,
         p.interpreted_intent or None, p.intent_confidence, p.direction_source,
         p.speaker, p.start_offset, p.end_offset,
         p.subject.model_dump_json(), p.direction,
         p.magnitude.model_dump_json() if p.magnitude else None,
         p.time_expression_raw, tw.model_dump_json(),
         json.dumps(p.conditions, ensure_ascii=False), p.confidence_raw, p.confidence_score,
         "pending_review", prediction_at, due_at, int(auto_apply), now, now),
    )

    row = conn.execute(
        "SELECT p.*, c.url AS video_url FROM prediction p "
        "JOIN content c ON c.id=p.content_id WHERE p.id=?",
        (pid,),
    ).fetchone()
    if row:
        _ensure_baseline(conn, row, str(uuid.uuid4()))
    baseline_row = conn.execute(
        "SELECT prediction_baseline_evidence_id FROM prediction WHERE id=?", (pid,)
    ).fetchone()

    # 记录事件
    log_event(conn, "prediction", pid, "created",
              json.dumps({
                  "auto_apply": auto_apply,
                  "fuzzy": tw.is_fuzzy,
                  "status": "pending_review",
              }, ensure_ascii=False))
    stored = p.model_dump()
    stored.update({
        "id": pid,
        "content_id": content_id,
        "status": "pending_review",
        "prediction_at": prediction_at,
        "due_at": due_at,
        "auto_apply_eligible": auto_apply,
        "prediction_baseline_evidence_id": (
            baseline_row["prediction_baseline_evidence_id"] if baseline_row else None
        ),
        "created_at": now,
        "updated_at": now,
    })
    return stored
