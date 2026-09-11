"""V0.2 验证闭环：预测确认、到期扫描、证据收集、AI 初判与人工锁定。"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from ..ai import VERIFY_SYSTEM, TASK_VERIFY, ai_gateway
from ..config import config
from ..db import get_conn, log_event

VERDICTS = {"correct", "partial", "incorrect", "inconclusive", "invalid"}
VERDICT_SCORE = {
    "correct": 1.0,
    "partial": 0.5,
    "incorrect": 0.0,
    "inconclusive": None,
    "invalid": None,
}


def _now() -> int:
    return int(time.time())


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _parse_published_at(value: Any) -> int | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    try:
        return int(float(text))
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except ValueError:
        try:
            return int(parsedate_to_datetime(text).timestamp())
        except (TypeError, ValueError):
            return None


def _prediction_row(conn, prediction_id: str):
    row = conn.execute(
        "SELECT p.*, co.title AS video_title, co.url AS video_url, "
        "co.platform, cr.id AS creator_id, cr.name AS creator_name "
        "FROM prediction p "
        "JOIN content co ON co.id=p.content_id "
        "LEFT JOIN creator cr ON cr.id=co.creator_id "
        "WHERE p.id=?",
        (prediction_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"预测不存在: {prediction_id}")
    return row


def _subject_text(row) -> str:
    subject = _loads(row["subject"], {})
    return str(subject.get("name") or subject.get("symbol") or "该标的")


def _magnitude_text(row) -> str:
    magnitude = _loads(row["magnitude"], {})
    if not magnitude:
        return ""
    values = [magnitude.get("min"), magnitude.get("max")]
    values = [v for v in values if v is not None]
    if not values:
        return ""
    unit = magnitude.get("unit") or ""
    if unit == "ratio":
        return f"{values[0] * 100:g}%"
    return "-".join(f"{v:g}" for v in values)


def _direction_text(direction: str) -> tuple[str, str]:
    mapping = {
        "up": ("上涨", "未上涨或下跌"),
        "down": ("下跌", "未下跌或上涨"),
        "above": ("突破目标位", "未突破目标位"),
        "below": ("跌破目标位", "未跌破目标位"),
        "flat": ("保持稳定", "出现明显波动"),
        "range": ("落在目标区间", "未落在目标区间"),
        "event_yes": ("事件发生", "事件未发生"),
        "event_no": ("事件不发生", "事件发生"),
    }
    return mapping.get(direction, ("预测成立", "预测未成立"))


def build_search_queries(row) -> list[tuple[str, str]]:
    """生成正反两面查询；relation 只是查询意图，最终仍由 AI/人工判断。"""
    subject = _subject_text(row)
    magnitude = _magnitude_text(row)
    direction = row["direction"] or "unknown"
    positive, negative = _direction_text(direction)
    horizon = _loads(row["time_window"], {}).get("raw") or row["time_expression_raw"] or ""
    suffix = f" {magnitude}" if magnitude else ""
    time_suffix = f" {horizon}" if horizon else ""
    return [
        (f"{subject} {positive}{suffix}{time_suffix} 最新数据 结果", "corroborating"),
        (f"{subject} {negative}{suffix}{time_suffix} 未达成 失败", "contradicting"),
    ]


def _insert_evidence(conn, *, prediction_id: str, content_id: str, batch: str,
                     source: str, source_type: str, url: str | None,
                     title: str | None, publisher: str | None,
                     published_at: int | None, is_official: bool,
                     is_direct: bool, credibility: float | None,
                     summary: str | None, raw_json: Any,
                     relation: str) -> str:
    evidence_id = str(uuid.uuid4())
    collected_at = _now()
    conn.execute(
        "INSERT INTO evidence (id, prediction_id, content_id, source, source_type, "
        "url, title, publisher, published_at, collected_at, is_official, is_direct, "
        "credibility, summary, raw_json, relation, collection_batch, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            evidence_id, prediction_id, content_id, source, source_type, url, title,
            publisher, published_at, collected_at, int(is_official), int(is_direct),
            credibility, summary,
            json.dumps(raw_json, ensure_ascii=False, default=str) if raw_json is not None else None,
            relation, batch, collected_at,
        ),
    )
    return evidence_id


def _ensure_baseline(conn, row, batch: str) -> str:
    existing = conn.execute(
        "SELECT id FROM evidence WHERE prediction_id=? AND relation='baseline' "
        "ORDER BY created_at LIMIT 1",
        (row["id"],),
    ).fetchone()
    if existing:
        return existing["id"]

    evidence_id = _insert_evidence(
        conn,
        prediction_id=row["id"],
        content_id=row["content_id"],
        batch=batch,
        source="baseline_snapshot",
        source_type="context",
        url=row["video_url"],
        title="预测发表时点基线（声明快照）",
        publisher="creator-insight",
        published_at=row["prediction_at"],
        is_official=False,
        is_direct=True,
        credibility=0.5,
        summary="记录预测发表时间与原始预测，作为时间隔离审计锚点；未填充事后行情。",
        raw_json={
            "prediction_id": row["id"],
            "prediction_at": row["prediction_at"],
            "raw_text": row["raw_text"],
            "subject": _loads(row["subject"], {}),
        },
        relation="baseline",
    )
    conn.execute(
        "UPDATE prediction SET prediction_baseline_evidence_id=?, updated_at=? WHERE id=?",
        (evidence_id, _now(), row["id"]),
    )
    return evidence_id


def _tavily_search(query: str) -> list[dict]:
    api_key = config.get("search", "tavily_api_key", default="")
    if not api_key:
        return []
    endpoint = config.get("search", "tavily_endpoint",
                          default="https://api.tavily.com/search")
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "topic": "news",
        "max_results": int(config.get("search", "max_results", default=5)),
        "include_answer": False,
        "include_raw_content": False,
    }
    try:
        with httpx.Client(timeout=30, trust_env=False) as client:
            response = client.post(endpoint, json=payload)
            response.raise_for_status()
            return response.json().get("results", [])
    except Exception as exc:
        print(f"[evidence] Tavily 搜索失败: {exc}")
        return []


def _market_symbol(row) -> str | None:
    subject = _loads(row["subject"], {})
    symbol = subject.get("symbol") or ""
    name = subject.get("name") or ""
    aliases = {
        "黄金": "GC=F",
        "白银": "SI=F",
        "比特币": "BTC-USD",
        "BTC": "BTC-USD",
        "原油": "CL=F",
        "纳斯达克": "^IXIC",
        "标普500": "^GSPC",
        "SPX": "^GSPC",
    }
    return aliases.get(symbol) or aliases.get(name) or symbol or None


def _collect_yfinance(conn, row, batch: str) -> list[str]:
    """可选结构化行情；未安装或不适配时安静跳过。"""
    subject = _loads(row["subject"], {})
    if subject.get("type") not in {"stock", "index", "fx", "commodity"}:
        return []
    symbol = _market_symbol(row)
    if not symbol:
        return []
    try:
        import yfinance as yf
    except ImportError:
        return []

    try:
        start = datetime.fromtimestamp(row["prediction_at"], tz=timezone.utc).date()
        end_ts = row["due_at"] or _now()
        end = datetime.fromtimestamp(end_ts, tz=timezone.utc).date()
        if end <= start:
            end = start
        history = yf.Ticker(symbol).history(
            start=start.isoformat(), end=end.isoformat(), auto_adjust=False
        )
        if history is None or history.empty:
            return []
        first = float(history["Close"].iloc[0])
        last = float(history["Close"].iloc[-1])
        change = (last - first) / first if first else None
        published_at = int(datetime.combine(
            history.index[-1].date(), datetime.min.time(), tzinfo=timezone.utc
        ).timestamp())
        summary = (
            f"{symbol} 结构化行情：起点收盘 {first:.4g}，"
            f"终点收盘 {last:.4g}，区间变化 "
            f"{change * 100:.2f}%." if change is not None else
            f"{symbol} 结构化行情已返回。"
        )
        evidence_id = _insert_evidence(
            conn,
            prediction_id=row["id"],
            content_id=row["content_id"],
            batch=batch,
            source="yfinance",
            source_type="market_data",
            url=f"https://finance.yahoo.com/quote/{symbol}",
            title=f"{symbol} 历史行情",
            publisher="Yahoo Finance",
            published_at=published_at,
            is_official=False,
            is_direct=True,
            credibility=0.9,
            summary=summary,
            raw_json={
                "symbol": symbol,
                "start": first,
                "end": last,
                "change_ratio": change,
            },
            relation="primary",
        )
        return [evidence_id]
    except Exception as exc:
        print(f"[evidence] yfinance 收集失败: {exc}")
        return []


def collect_evidence(prediction_id: str) -> dict:
    """收集一批证据并返回批次摘要。没有外部密钥时仍保留可审计基线。"""
    conn = get_conn()
    batch = str(uuid.uuid4())
    evidence_ids: list[str] = []
    search_count = 0
    try:
        row = _prediction_row(conn, prediction_id)
        _ensure_baseline(conn, row, batch)

        for query, relation in build_search_queries(row):
            for item in _tavily_search(query):
                url = item.get("url")
                dedupe_key = hashlib.sha1(
                    f"{prediction_id}|{url or item.get('title', '')}".encode("utf-8")
                ).hexdigest()
                # 同一批次避免重复写同一个搜索结果，跨批次仍保留采集审计。
                exists = conn.execute(
                    "SELECT id FROM evidence WHERE prediction_id=? AND raw_json LIKE ? LIMIT 1",
                    (prediction_id, f"%{dedupe_key}%"),
                ).fetchone()
                if exists:
                    continue
                raw = dict(item)
                raw["_dedupe_key"] = dedupe_key
                evidence_ids.append(_insert_evidence(
                    conn,
                    prediction_id=prediction_id,
                    content_id=row["content_id"],
                    batch=batch,
                    source="tavily",
                    source_type="news",
                    url=url,
                    title=item.get("title"),
                    publisher=item.get("publisher") or item.get("source"),
                    published_at=_parse_published_at(item.get("published_date")),
                    is_official=False,
                    is_direct=False,
                    credibility=0.75,
                    summary=item.get("content") or item.get("snippet"),
                    raw_json=raw,
                    relation=relation,
                ))
                search_count += 1

        evidence_ids.extend(_collect_yfinance(conn, row, batch))
        conn.commit()
        log_event(
            conn,
            "prediction",
            prediction_id,
            "evidence_collected",
            json.dumps({
                "batch": batch,
                "search_count": search_count,
                "evidence_count": len(evidence_ids),
            }, ensure_ascii=False),
        )
        return {
            "batch": batch,
            "baseline": True,
            "search_count": search_count,
            "evidence_count": len(evidence_ids),
        }
    finally:
        conn.close()


def add_manual_evidence(prediction_id: str, *, title: str, summary: str,
                        url: str | None = None, published_at: int | None = None,
                        relation: str = "primary", source_type: str = "news",
                        publisher: str | None = None,
                        credibility: float = 0.8) -> dict:
    """保存人工提供的可审计证据，供未配置搜索服务时使用。"""
    allowed_relations = {"primary", "contradicting", "corroborating", "context"}
    if relation not in allowed_relations:
        raise ValueError(f"不支持的证据关系: {relation}")
    if not title.strip() or not summary.strip():
        raise ValueError("证据标题和摘要不能为空")
    if published_at is None:
        raise ValueError("人工证据必须填写 published_at")
    if not 0 <= credibility <= 1:
        raise ValueError("credibility 必须在 0 到 1 之间")
    conn = get_conn()
    try:
        row = _prediction_row(conn, prediction_id)
        evidence_id = _insert_evidence(
            conn,
            prediction_id=prediction_id,
            content_id=row["content_id"],
            batch=str(uuid.uuid4()),
            source="manual",
            source_type=source_type,
            url=url,
            title=title.strip(),
            publisher=(publisher or "").strip() or None,
            published_at=published_at,
            is_official=source_type in {"official", "regulatory"},
            is_direct=source_type in {"official", "regulatory", "market_data", "filings"},
            credibility=credibility,
            summary=summary.strip(),
            raw_json={"entered_by": "human"},
            relation=relation,
        )
        log_event(
            conn,
            "evidence",
            evidence_id,
            "manual_evidence_added",
            json.dumps({"prediction_id": prediction_id, "relation": relation},
                       ensure_ascii=False),
            actor="human",
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM evidence WHERE id=?", (evidence_id,)
        ).fetchone())
    finally:
        conn.close()


def scan_due_predictions(now: int | None = None) -> list[str]:
    now = now or _now()
    conn = get_conn()
    changed: list[str] = []
    try:
        rows = conn.execute(
            "SELECT id FROM prediction WHERE status='active' AND due_at IS NOT NULL AND due_at<=?",
            (now,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE prediction SET status='due', updated_at=? "
                "WHERE id=? AND status='active'",
                (now, row["id"]),
            )
            changed.append(row["id"])
            log_event(conn, "prediction", row["id"], "became_due")
        conn.commit()
        return changed
    finally:
        conn.close()


def backfill_missing_baselines() -> int:
    """为 V0.1 历史预测补一份声明快照，不伪造当时行情。"""
    conn = get_conn()
    count = 0
    try:
        rows = conn.execute(
            "SELECT p.*, c.url AS video_url FROM prediction p "
            "JOIN content c ON c.id=p.content_id "
            "WHERE p.prediction_baseline_evidence_id IS NULL"
        ).fetchall()
        for row in rows:
            _ensure_baseline(conn, row, str(uuid.uuid4()))
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def review_prediction(prediction_id: str, decision: str, due_at: int | None = None,
                      notes: str = "") -> dict:
    """人工确认抽取结果：pending_review -> active/invalid。"""
    if decision not in {"active", "invalid"}:
        raise ValueError(f"不支持的确认动作: {decision}")
    conn = get_conn()
    try:
        row = _prediction_row(conn, prediction_id)
        if row["status"] not in {"extracted", "pending_review"}:
            raise ValueError(f"当前状态不可确认: {row['status']}")
        now = _now()
        if decision == "active":
            due_at = due_at or row["due_at"]
            if not due_at:
                raise ValueError("确认 active 时必须填写 due_at（Unix 秒）")
            if due_at < row["prediction_at"]:
                raise ValueError("due_at 不能早于 prediction_at")
            time_window = _loads(row["time_window"], {})
            time_window["parsed_end"] = due_at
            time_window["is_fuzzy"] = False
            time_window["requires_human_confirmation"] = False
            time_window["parsed_at"] = now
            conn.execute(
                "UPDATE prediction SET status='active', due_at=?, time_window=?, updated_at=? "
                "WHERE id=?",
                (due_at, json.dumps(time_window, ensure_ascii=False), now, prediction_id),
            )
        else:
            conn.execute(
                "UPDATE prediction SET status='invalid', updated_at=? WHERE id=?",
                (now, prediction_id),
            )
        log_event(
            conn,
            "prediction",
            prediction_id,
            "reviewed",
            json.dumps({"decision": decision, "due_at": due_at, "notes": notes},
                       ensure_ascii=False),
            actor="human",
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM prediction WHERE id=?", (prediction_id,)
        ).fetchone())
    finally:
        conn.close()


def run_verification(prediction_id: str) -> dict:
    """收集证据并执行 AI 初判，结果进入 human_review，不自动 final。"""
    conn = get_conn()
    try:
        row = _prediction_row(conn, prediction_id)
        if row["status"] not in {"due", "verifying", "ai_verified", "human_review"}:
            raise ValueError(f"当前状态不可开始验证: {row['status']}")
        conn.execute(
            "UPDATE prediction SET status='verifying', updated_at=? WHERE id=?",
            (_now(), prediction_id),
        )
        conn.commit()
    finally:
        conn.close()

    collection = collect_evidence(prediction_id)
    conn = get_conn()
    try:
        batch = str(uuid.uuid4())  # AI 生成证据单独一批
        row = _prediction_row(conn, prediction_id)
        evidence_rows = conn.execute(
            "SELECT * FROM evidence WHERE prediction_id=? "
            "ORDER BY COALESCE(published_at, 0), collected_at",
            (prediction_id,),
        ).fetchall()
        evidence = [dict(item) for item in evidence_rows]
        post_time = [
            item for item in evidence
            if item["relation"] != "baseline"
            and item["published_at"] is not None
            and item["published_at"] >= row["prediction_at"]
        ]
        prediction_time = [
            item for item in evidence
            if item["published_at"] is not None
            and item["published_at"] <= row["prediction_at"]
        ]
        prediction_payload = dict(row)
        prediction_payload["subject"] = _loads(row["subject"], {})
        prediction_payload["magnitude"] = _loads(row["magnitude"], None)
        prediction_payload["time_window"] = _loads(row["time_window"], {})
        prediction_payload["conditions"] = _loads(row["conditions"], [])
        verify_input = {
            "prediction": prediction_payload,
            "post_time_evidence": post_time,
            "prediction_time_evidence": prediction_time,
        }

        provider = "evidence_guard"
        if not post_time:
            # 无外部证据 → 仍让 AI 凭公开知识生成证据链并尝试判定（低可信度）
            try:
                ai_result, provider = ai_gateway.chat(
                    VERIFY_SYSTEM,
                    json.dumps(verify_input, ensure_ascii=False, default=str),
                    TASK_VERIFY,
                )
            except Exception as exc:
                provider = "fallback"
                ai_result = {
                    "ai_verdict": "inconclusive",
                    "ai_score": None,
                    "ai_confidence": 0.0,
                    "ai_reasoning": {
                        "post_time_facts": [],
                        "judgment": "无外部证据且 AI 生成证据失败，等待人工判断。",
                        "uncertainty_reasons": [str(exc)],
                    },
                }
        else:
            try:
                ai_result, provider = ai_gateway.chat(
                    VERIFY_SYSTEM,
                    json.dumps(verify_input, ensure_ascii=False, default=str),
                    TASK_VERIFY,
                )
            except Exception as exc:
                provider = "fallback"
                ai_result = {
                    "ai_verdict": "inconclusive",
                    "ai_score": None,
                    "ai_confidence": 0.0,
                    "ai_reasoning": {
                        "post_time_facts": [],
                        "judgment": "AI 验证服务不可用，等待人工判断。",
                        "uncertainty_reasons": [str(exc)],
                    },
                }

        verdict = ai_result.get("ai_verdict", "inconclusive")
        if verdict not in VERDICTS:
            verdict = "inconclusive"
        reasoning = ai_result.get("ai_reasoning") or ai_result.get("reasoning") or {}

        # ── AI 生成的证据链（未核实，低可信度，供人工参考） ──
        ai_evidence_ids: list[str] = []
        for item in ai_result.get("ai_generated_evidence") or []:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            published = _parse_published_at(item.get("published_at"))
            # 防 AI 幻觉：AI 生成证据的发布时间不允许早于预测发表时间
            if published is None or published < row["prediction_at"]:
                published = row["prediction_at"]
            relation = item.get("relation") or "context"
            if relation not in {"corroborating", "contradicting", "context"}:
                relation = "context"
            summary = item.get("summary") or item.get("title") or ""
            url = item.get("url")
            if url and not str(url).startswith(("http://", "https://")):
                url = None
            evidence_id = _insert_evidence(
                conn,
                prediction_id=prediction_id,
                content_id=row["content_id"],
                batch=batch,
                source="ai_knowledge",
                source_type="news",
                url=url,
                title=str(item.get("title"))[:200],
                publisher="AI 生成（未核实）",
                published_at=published,
                is_official=False,
                is_direct=False,
                credibility=0.4,
                summary=f"[AI 生成，未经核实] {summary}",
                raw_json={"ai_generated": True, "provider": provider},
                relation=relation,
            )
            ai_evidence_ids.append(evidence_id)

        # 引用证据 ID：真实证据优先，AI 生成证据补充
        evidence_ids = [
            str(item.get("evidence_id"))
            for item in reasoning.get("post_time_facts", [])
            if item.get("evidence_id")
        ]
        evidence_ids.extend(ai_evidence_ids)
        reviewed_at = _now()
        existing = conn.execute(
            "SELECT id, human_verdict, human_notes, human_reviewed_at, locked "
            "FROM verification WHERE prediction_id=?",
            (prediction_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE verification SET ai_verdict=?, ai_score=?, ai_confidence=?, "
                "ai_reasoning_json=?, ai_evidence_ids=?, ai_judged_at=?, "
                "final_verdict=?, updated_at=? WHERE prediction_id=?",
                (
                    verdict, ai_result.get("ai_score"), ai_result.get("ai_confidence"),
                    json.dumps(reasoning, ensure_ascii=False), json.dumps(evidence_ids),
                    reviewed_at,
                    existing["human_verdict"] or verdict,
                    reviewed_at, prediction_id,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO verification "
                "(id, prediction_id, ai_verdict, ai_score, ai_confidence, "
                "ai_reasoning_json, ai_evidence_ids, ai_judged_at, final_verdict, "
                "locked, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    str(uuid.uuid4()), prediction_id, verdict, ai_result.get("ai_score"),
                    ai_result.get("ai_confidence"),
                    json.dumps(reasoning, ensure_ascii=False), json.dumps(evidence_ids),
                    reviewed_at, verdict, reviewed_at, reviewed_at,
                ),
            )
        # ── V0.3 硬预测自动过：auto_apply_eligible + 确定性判定 + AI 高置信 → 自动锁定（24h 可撤销）──
        auto_applied = _maybe_auto_apply(
            conn, row, verdict, ai_result.get("ai_confidence"), reviewed_at,
        )
        conn.execute(
            "UPDATE prediction SET status=?, updated_at=? WHERE id=?",
            ("final" if auto_applied else "human_review", reviewed_at, prediction_id),
        )
        log_event(
            conn,
            "prediction",
            prediction_id,
            "ai_verified",
            json.dumps({
                "provider": provider,
                "verdict": verdict,
                "post_time_evidence": len(post_time),
                "prediction_time_evidence": len(prediction_time),
                "auto_applied": auto_applied,
            }, ensure_ascii=False),
        )
        conn.commit()
        if auto_applied and row["creator_id"]:
            recompute_creator_reliability(row["creator_id"], conn=conn)
            conn.commit()
        return get_verification_detail(prediction_id, conn=conn) | {
            "collection": collection,
            "provider": provider,
            "auto_applied": auto_applied,
        }
    finally:
        conn.close()


def _score_for(verdict: str, score: float | None) -> float | None:
    return score if score is not None else VERDICT_SCORE.get(verdict)


# ─── V0.3 硬预测自动过（docs/15: 24h 撤销窗口）──────────────────
def _maybe_auto_apply(conn, pred_row, ai_verdict: str,
                      ai_confidence: float | None, reviewed_at: int) -> bool:
    """硬预测自动过：结构化标的 + 确定性判定（correct/incorrect）+ AI 高置信 → 自动锁定。

    返回 True 表示已自动过。锁定后 human_verdict 由系统代填，24h 内可撤销。
    """
    if not pred_row["auto_apply_eligible"]:
        return False
    if ai_verdict not in {"correct", "incorrect"}:
        return False
    if not config.get("verification", "auto_apply", default=False):
        return False
    min_conf = float(config.get("verification", "auto_apply_min_confidence", default=0.85) or 0.85)
    if ai_confidence is None or ai_confidence < min_conf:
        return False
    undo_hours = int(config.get("verification", "auto_apply_undo_hours", default=24) or 24)
    final_score = VERDICT_SCORE.get(ai_verdict)
    conn.execute(
        "UPDATE verification SET human_verdict=?, human_score=?, human_notes=?, "
        "human_reviewed_at=?, final_verdict=?, locked=1, auto_applied_at=?, updated_at=? "
        "WHERE prediction_id=?",
        (ai_verdict, final_score,
         f"[V0.3] 硬预测自动过（{undo_hours}h 内可撤销）",
         reviewed_at, ai_verdict, reviewed_at, reviewed_at, pred_row["id"]),
    )
    log_event(
        conn, "prediction", pred_row["id"], "auto_applied",
        json.dumps({
            "verdict": ai_verdict, "ai_confidence": ai_confidence,
            "undo_hours": undo_hours,
        }, ensure_ascii=False),
        actor="system",
    )
    conn.commit()
    _notify_auto_applied(pred_row, ai_verdict, ai_confidence, undo_hours)
    return True


def _notify_auto_applied(pred_row, ai_verdict: str,
                         ai_confidence: float | None, undo_hours: int) -> None:
    """硬预测自动锁定 → 站内/Webhook/邮件通知（失败不影响验证主流程）"""
    try:
        from .notify import send_notification
        send_notification(
            "硬预测自动锁定",
            f"预测已自动判定为「{ai_verdict}」（AI 置信 "
            f"{ai_confidence:.0%}），{undo_hours}h 内可撤销",
            category="auto_applied",
            payload={"prediction_id": pred_row["id"], "verdict": ai_verdict,
                     "ai_confidence": ai_confidence,
                     "creator_id": pred_row["creator_id"]},
        )
    except Exception:
        pass


def undo_auto_apply(prediction_id: str) -> dict:
    """24h 撤销窗口内解除硬预测自动过，退回人工复核。"""
    conn = get_conn()
    try:
        row = _prediction_row(conn, prediction_id)
        verification = conn.execute(
            "SELECT * FROM verification WHERE prediction_id=?",
            (prediction_id,),
        ).fetchone()
        if not verification or not verification["locked"]:
            raise ValueError("该预测未锁定，无需撤销")
        applied_at = verification["auto_applied_at"]
        if applied_at is None:
            raise ValueError("该锁定不是硬预测自动过，请通过人工复核流程处理")
        undo_hours = int(config.get("verification", "auto_apply_undo_hours", default=24) or 24)
        if _now() > applied_at + undo_hours * 3600:
            raise ValueError(f"已超过 {undo_hours}h 撤销窗口，锁定不可篡改")
        now = _now()
        conn.execute(
            "UPDATE verification SET human_verdict=NULL, human_score=NULL, human_notes=NULL, "
            "human_reviewed_at=NULL, final_verdict=ai_verdict, locked=0, auto_applied_at=NULL, "
            "updated_at=? WHERE prediction_id=?",
            (now, prediction_id),
        )
        conn.execute(
            "UPDATE prediction SET status='human_review', updated_at=? WHERE id=?",
            (now, prediction_id),
        )
        log_event(conn, "prediction", prediction_id, "auto_apply_undone", actor="human")
        conn.commit()
        if row["creator_id"]:
            recompute_creator_reliability(row["creator_id"], conn=conn)
            conn.commit()
        return get_verification_detail(prediction_id, conn=conn)
    finally:
        conn.close()


def submit_human_review(prediction_id: str, human_verdict: str,
                        human_notes: str = "", human_score: float | None = None) -> dict:
    if human_verdict not in VERDICTS:
        raise ValueError(f"不支持的 verdict: {human_verdict}")
    conn = get_conn()
    try:
        row = _prediction_row(conn, prediction_id)
        verification = conn.execute(
            "SELECT * FROM verification WHERE prediction_id=?",
            (prediction_id,),
        ).fetchone()
        if not verification:
            raise ValueError("该预测尚未完成 AI 初判，无法提交人工复核")
        if verification["locked"]:
            raise ValueError("该验证已经锁定，不能修改")

        now = _now()
        final_verdict = human_verdict
        final_score = _score_for(human_verdict, human_score)
        conn.execute(
            "UPDATE verification SET human_verdict=?, human_score=?, human_notes=?, "
            "human_reviewed_at=?, final_verdict=?, locked=1, updated_at=? "
            "WHERE prediction_id=?",
            (
                human_verdict, final_score, human_notes, now, final_verdict, now,
                prediction_id,
            ),
        )
        next_status = "invalid" if human_verdict == "invalid" else "final"
        conn.execute(
            "UPDATE prediction SET status=?, updated_at=? WHERE id=?",
            (next_status, now, prediction_id),
        )
        log_event(
            conn,
            "prediction",
            prediction_id,
            "human_verified",
            json.dumps({
                "verdict": human_verdict,
                "score": final_score,
                "notes": human_notes,
            }, ensure_ascii=False),
            actor="human",
        )
        conn.commit()
        if row["creator_id"]:
            recompute_creator_reliability(row["creator_id"], conn=conn)
            conn.commit()
        return get_verification_detail(prediction_id, conn=conn)
    finally:
        conn.close()


# V0.3 博主画像：subject type → 领域 映射（docs/08）
DOMAIN_BY_SUBJECT = {
    "stock": "个股",
    "index": "指数",
    "fx": "市场",
    "commodity": "市场",
    "macro_indicator": "宏观",
    "policy": "政策",
    "real_estate": "楼市",
}
# 置信度分桶边界（docs/08: [0-0.3, 0.3-0.6, 0.6-0.8, 0.8-1.0]）
CONFIDENCE_BANDS = [
    ("0-30%", 0.0, 0.3),
    ("30-60%", 0.3, 0.6),
    ("60-80%", 0.6, 0.8),
    ("80-100%", 0.8, 1.001),
]


def _horizon_label(prediction_at: int, due_at: int | None) -> str | None:
    if not due_at:
        return None
    days = (due_at - prediction_at) / 86400
    if days < 30:
        return "短期(<1月)"
    if days <= 180:
        return "中期(1-6月)"
    return "长期(>6月)"


def _band_label(conf: float) -> str | None:
    for label, lo, hi in CONFIDENCE_BANDS:
        if lo <= conf < hi:
            return label
    return None


def _bucket_stats(values: dict[str, list[float]], threshold: int) -> dict:
    """按桶聚合：accuracy（correct*1 + partial*0.5）+ 样本数 + 样本不足标记"""
    result: dict[str, dict] = {}
    for key, scores in values.items():
        if not scores:
            continue
        n = len(scores)
        result[key] = {
            "accuracy": round(sum(scores) / n, 4),
            "n": n,
            "insufficient": n < threshold,
        }
    return result


def recompute_creator_reliability(creator_id: str, conn=None) -> dict:
    """V0.3 博主画像：基础正确率 + 分桶（领域/期限/标的/置信度）+ Brier 校准 + 样本量门槛"""
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(
            "SELECT v.final_verdict, p.subject, p.time_window, "
            "p.prediction_at, p.due_at, p.confidence_score "
            "FROM verification v "
            "JOIN prediction p ON p.id=v.prediction_id "
            "JOIN content c ON c.id=p.content_id "
            "WHERE c.creator_id=? AND v.locked=1",
            (creator_id,),
        ).fetchall()
        total_predictions = conn.execute(
            "SELECT COUNT(*) AS n FROM prediction p "
            "JOIN content c ON c.id=p.content_id WHERE c.creator_id=?",
            (creator_id,),
        ).fetchone()["n"]
        counts = {key: 0 for key in ("correct", "partial", "incorrect", "inconclusive")}
        by_domain: dict[str, list[float]] = {}
        by_horizon: dict[str, list[float]] = {}
        by_subject: dict[str, list[float]] = {}
        by_confidence_band: dict[str, list[float]] = {}
        brier_terms: list[float] = []
        for row in rows:
            verdict = row["final_verdict"]
            if verdict in counts:
                counts[verdict] += 1
            score = VERDICT_SCORE.get(verdict)
            if score is None:
                continue  # inconclusive / invalid 不计入正确率
            subject = _loads(row["subject"], {})
            subject_type = subject.get("type", "unknown")
            by_subject.setdefault(subject_type, []).append(score)
            by_domain.setdefault(DOMAIN_BY_SUBJECT.get(subject_type, "其他"), []).append(score)
            horizon = _horizon_label(row["prediction_at"], row["due_at"])
            if horizon:
                by_horizon.setdefault(horizon, []).append(score)
            conf = row["confidence_score"]
            band = _band_label(conf) if conf is not None else None
            if band:
                by_confidence_band.setdefault(band, []).append(score)
                brier_terms.append((VERDICT_SCORE[verdict] - min(max(conf, 0.0), 1.0)) ** 2)

        threshold = int(config.get("verification", "sample_size_threshold", default=30) or 30)
        verified_count = sum(counts[key] for key in ("correct", "partial", "incorrect"))
        base_accuracy = (
            (counts["correct"] + counts["partial"] * 0.5) / verified_count
            if verified_count else None
        )
        calibration_score = round(sum(brier_terms) / len(brier_terms), 4) if brier_terms else None
        now = _now()
        payload = (
            now, total_predictions, verified_count, counts["correct"], counts["partial"],
            counts["incorrect"], counts["inconclusive"], base_accuracy,
            json.dumps(_bucket_stats(by_domain, threshold), ensure_ascii=False),
            json.dumps(_bucket_stats(by_horizon, threshold), ensure_ascii=False),
            json.dumps(_bucket_stats(by_subject, threshold), ensure_ascii=False),
            json.dumps(_bucket_stats(by_confidence_band, threshold), ensure_ascii=False),
            calibration_score,
        )
        conn.execute(
            "INSERT INTO creator_reliability "
            "(creator_id, computed_at, total_predictions, verified_count, correct_count, "
            "partial_count, incorrect_count, inconclusive_count, base_accuracy, "
            "accuracy_by_domain, accuracy_by_horizon, accuracy_by_subject, "
            "accuracy_by_confidence_band, calibration_score) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(creator_id) DO UPDATE SET computed_at=excluded.computed_at, "
            "total_predictions=excluded.total_predictions, verified_count=excluded.verified_count, "
            "correct_count=excluded.correct_count, partial_count=excluded.partial_count, "
            "incorrect_count=excluded.incorrect_count, inconclusive_count=excluded.inconclusive_count, "
            "base_accuracy=excluded.base_accuracy, accuracy_by_domain=excluded.accuracy_by_domain, "
            "accuracy_by_horizon=excluded.accuracy_by_horizon, "
            "accuracy_by_subject=excluded.accuracy_by_subject, "
            "accuracy_by_confidence_band=excluded.accuracy_by_confidence_band, "
            "calibration_score=excluded.calibration_score",
            (creator_id, *payload),
        )
        if owns_conn:
            conn.commit()
        return {
            "creator_id": creator_id,
            "computed_at": now,
            "total_predictions": total_predictions,
            "verified_count": verified_count,
            "base_accuracy": base_accuracy,
            "sample_size_warning": None if verified_count >= threshold else
                f"已验证样本 {verified_count} < {threshold}，统计仅供参考",
            "accuracy_by_domain": _bucket_stats(by_domain, threshold),
            "accuracy_by_horizon": _bucket_stats(by_horizon, threshold),
            "accuracy_by_subject": _bucket_stats(by_subject, threshold),
            "accuracy_by_confidence_band": _bucket_stats(by_confidence_band, threshold),
            "calibration_score": calibration_score,  # Brier Score（越小越好）：<0.18 良好 / 0.25 一般 / >0.35 差
            "sample_size_threshold": threshold,
        }
    finally:
        if owns_conn:
            conn.close()


def get_creator_profile(creator_id: str) -> dict:
    """V0.3 博主画像：创作者信息 + 可靠性统计 + 已验证预测明细（含校准点）"""
    conn = get_conn()
    try:
        creator = conn.execute("SELECT * FROM creator WHERE id=?", (creator_id,)).fetchone()
        if not creator:
            raise ValueError("创作者不存在")
        rel = conn.execute(
            "SELECT * FROM creator_reliability WHERE creator_id=?", (creator_id,)
        ).fetchone()
        rows = conn.execute(
            "SELECT p.id, p.raw_text, p.interpreted_intent, p.confidence_score, p.confidence_raw, "
            "p.prediction_at, p.due_at, p.status, p.subject, p.direction, "
            "p.auto_apply_eligible, "
            "v.ai_verdict, v.ai_confidence, v.human_verdict, v.final_verdict, "
            "v.locked, v.auto_applied_at, v.human_notes "
            "FROM prediction p JOIN verification v ON v.prediction_id=p.id "
            "JOIN content c ON c.id=p.content_id "
            "WHERE c.creator_id=? AND v.locked=1 "
            "ORDER BY COALESCE(p.due_at, p.prediction_at) DESC LIMIT 200",
            (creator_id,),
        ).fetchall()
        result = dict(creator)
        if rel:
            rel_dict = dict(rel)
            for col in ("accuracy_by_domain", "accuracy_by_horizon",
                        "accuracy_by_subject", "accuracy_by_confidence_band"):
                rel_dict[col] = _loads(rel_dict.get(col), {})
            threshold = int(config.get("verification", "sample_size_threshold", default=30) or 30)
            vc = rel_dict.get("verified_count") or 0
            rel_dict["sample_size_warning"] = (
                None if vc >= threshold else f"已验证样本 {vc} < {threshold}，统计仅供参考"
            )
            result["reliability"] = rel_dict
        else:
            result["reliability"] = None
        predictions = []
        for r in rows:
            item = dict(r)
            item["subject"] = _loads(r["subject"], {})
            score = VERDICT_SCORE.get(r["final_verdict"])
            conf = r["confidence_score"]
            item["calibration_point"] = (
                [round(min(max(conf, 0.0), 1.0), 3), score]
                if conf is not None and score is not None else None
            )
            predictions.append(item)
        result["prediction_count"] = len(predictions)
        result["predictions"] = predictions
        return result
    finally:
        conn.close()


def get_verification_detail(prediction_id: str, conn=None) -> dict:
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        row = _prediction_row(conn, prediction_id)
        verification = conn.execute(
            "SELECT * FROM verification WHERE prediction_id=?",
            (prediction_id,),
        ).fetchone()
        evidence = conn.execute(
            "SELECT * FROM evidence WHERE prediction_id=? "
            "ORDER BY CASE relation WHEN 'baseline' THEN 0 ELSE 1 END, "
            "COALESCE(published_at, 0), collected_at",
            (prediction_id,),
        ).fetchall()
        prediction = dict(row)
        prediction["subject"] = _loads(row["subject"], {})
        prediction["magnitude"] = _loads(row["magnitude"], None)
        prediction["time_window"] = _loads(row["time_window"], {})
        prediction["conditions"] = _loads(row["conditions"], [])
        result = {
            "prediction": prediction,
            "verification": dict(verification) if verification else None,
            "evidence": [dict(item) for item in evidence],
        }
        return result
    finally:
        if owns_conn:
            conn.close()


def list_verification_queue(status: str = "") -> list[dict]:
    conn = get_conn()
    try:
        statuses = [status] if status else ["due", "verifying", "ai_verified", "human_review"]
        placeholders = ",".join("?" for _ in statuses)
        rows = conn.execute(
            f"SELECT p.id, p.raw_text, p.interpreted_intent, p.status, p.prediction_at, p.due_at, "
            "p.confidence_raw, p.confidence_score, p.subject, p.direction, "
            "co.title AS video_title, co.url AS video_url, cr.name AS creator_name, "
            "v.ai_verdict, v.ai_score, v.ai_confidence, v.human_verdict, "
            "v.final_verdict, v.locked, "
            "(SELECT COUNT(*) FROM evidence e WHERE e.prediction_id=p.id) AS evidence_count "
            "FROM prediction p "
            "JOIN content co ON co.id=p.content_id "
            "LEFT JOIN creator cr ON cr.id=co.creator_id "
            "LEFT JOIN verification v ON v.prediction_id=p.id "
            f"WHERE p.status IN ({placeholders}) ORDER BY COALESCE(p.due_at, 0), p.created_at",
            statuses,
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["subject"] = _loads(row["subject"], {})
            result.append(item)
        return result
    finally:
        conn.close()
