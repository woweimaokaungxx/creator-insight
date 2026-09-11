"""SQLite 初始化：按 docs/04-sqlite-schema.sql 建表"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import config

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS creator (
  id            TEXT PRIMARY KEY,
  platform      TEXT NOT NULL,
  platform_id   TEXT NOT NULL,
  name          TEXT NOT NULL,
  url           TEXT NOT NULL,
  avatar_url    TEXT,               -- 平台远程头像地址
  avatar_path   TEXT,               -- 本地缓存路径（相对 data/avatars）
  avatar_src    TEXT,               -- 本地文件对应的来源 URL（用于检测博主是否换头像）
  domain_tags   TEXT,
  added_at      INTEGER NOT NULL,
  notes         TEXT,
  UNIQUE(platform, platform_id)
);

CREATE TABLE IF NOT EXISTS content (
  id              TEXT PRIMARY KEY,
  creator_id      TEXT NOT NULL REFERENCES creator(id),
  platform        TEXT NOT NULL,
  platform_vid    TEXT NOT NULL,
  title           TEXT,
  url             TEXT NOT NULL,
  published_at    INTEGER,
  duration_sec    INTEGER,
  fetched_at      INTEGER NOT NULL,
  media_path      TEXT,
  cover_path      TEXT,
  raw_meta_json   TEXT,
  digg_count      INTEGER,          -- 点赞
  comment_count   INTEGER,          -- 评论
  share_count     INTEGER,          -- 转发
  collect_count   INTEGER,          -- 收藏
  UNIQUE(platform, platform_vid)
);
CREATE INDEX IF NOT EXISTS idx_content_creator ON content(creator_id);

CREATE TABLE IF NOT EXISTS transcript (
  id                     TEXT PRIMARY KEY,
  content_id             TEXT NOT NULL UNIQUE REFERENCES content(id),
  source                 TEXT NOT NULL,
  language               TEXT,
  text_full              TEXT NOT NULL,
  text_full_simplified   TEXT,          -- 简体校对版（AI 转简体，V0.5）
  segments_json          TEXT,
  created_at             INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS claim (
  id              TEXT PRIMARY KEY,
  content_id      TEXT NOT NULL REFERENCES content(id),
  text            TEXT NOT NULL,
  speaker         TEXT,
  start_offset    REAL,
  end_offset      REAL,
  category        TEXT,
  topic           TEXT,               -- V0.8 观点所属主题
  stance          TEXT,               -- V0.8 博主立场 支持|反对|中性|unknown
  importance      REAL,               -- V0.8 重要度 0-1
  confidence      REAL,               -- V0.8 博主表述肯定程度 0-1
  support         TEXT,               -- V0.8 论据/数据/例证
  key_phrase      TEXT,               -- V0.8 代表观点的原文短句
  created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS prediction (
  id                   TEXT PRIMARY KEY,
  content_id           TEXT NOT NULL REFERENCES content(id),
  parent_prediction_id TEXT REFERENCES prediction(id),
  revision_no          INTEGER DEFAULT 1,
  raw_text             TEXT NOT NULL,
  interpreted_intent   TEXT,               -- AI 对"博主想预测什么"的理解（非原话）
  intent_confidence    REAL,               -- AI 对"这确实是预测"的把握 0-1
  direction_source     TEXT,               -- explicit（明说）| inferred（AI 推断）
  speaker              TEXT,
  start_offset         REAL,
  end_offset           REAL,
  subject              TEXT,
  direction            TEXT,
  magnitude            TEXT,
  time_expression_raw  TEXT,
  time_window          TEXT,
  conditions           TEXT,
  confidence_raw       TEXT,
  confidence_score     REAL,
  status               TEXT NOT NULL,
  prediction_at        INTEGER NOT NULL,
  due_at               INTEGER,
  auto_apply_eligible  INTEGER DEFAULT 0,
  prediction_baseline_evidence_id TEXT,
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prediction_status ON prediction(status);
CREATE INDEX IF NOT EXISTS idx_prediction_due ON prediction(due_at);
CREATE INDEX IF NOT EXISTS idx_prediction_content ON prediction(content_id);

CREATE TABLE IF NOT EXISTS evidence (
  id               TEXT PRIMARY KEY,
  prediction_id    TEXT REFERENCES prediction(id),
  content_id       TEXT REFERENCES content(id),
  source           TEXT NOT NULL,
  source_type      TEXT NOT NULL,
  url              TEXT,
  title            TEXT,
  publisher        TEXT,
  published_at     INTEGER,
  collected_at     INTEGER NOT NULL,
  is_official      INTEGER DEFAULT 0,
  is_direct        INTEGER DEFAULT 0,
  credibility      REAL,
  summary          TEXT,
  raw_json         TEXT,
  relation         TEXT,
  collection_batch TEXT,
  created_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_prediction ON evidence(prediction_id);

CREATE TABLE IF NOT EXISTS verification (
  id                TEXT PRIMARY KEY,
  prediction_id     TEXT NOT NULL UNIQUE REFERENCES prediction(id),
  ai_verdict        TEXT,
  ai_score          REAL,
  ai_confidence     REAL,
  ai_reasoning_json TEXT,
  ai_evidence_ids   TEXT,
  ai_judged_at      INTEGER,
  human_verdict     TEXT,
  human_score       REAL,
  human_notes       TEXT,
  human_reviewed_at INTEGER,
  final_verdict     TEXT NOT NULL,
  locked            INTEGER DEFAULT 0,
  created_at        INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS creator_reliability (
  creator_id             TEXT PRIMARY KEY REFERENCES creator(id),
  computed_at            INTEGER NOT NULL,
  total_predictions      INTEGER,
  verified_count         INTEGER,
  correct_count          INTEGER,
  partial_count          INTEGER,
  incorrect_count        INTEGER,
  inconclusive_count     INTEGER,
  base_accuracy          REAL,
  accuracy_by_domain     TEXT,
  accuracy_by_horizon    TEXT,
  accuracy_by_subject    TEXT,
  accuracy_by_confidence_band TEXT,
  calibration_score      REAL
);

CREATE TABLE IF NOT EXISTS event_log (
  id          TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id   TEXT NOT NULL,
  event_type  TEXT NOT NULL,
  payload     TEXT,
  actor       TEXT NOT NULL,
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_entity ON event_log(entity_type, entity_id);

-- V0.4 关注监控（参考 douyin-creator-distill 的「关注与更新」）
-- 把博主「加入关注」后，定期增量抓取主页作品目录并自动入库。
CREATE TABLE IF NOT EXISTS subscription (
  id                    TEXT PRIMARY KEY,
  platform              TEXT NOT NULL,
  source_key            TEXT NOT NULL,          -- 稳定来源键（抖音 sec_user_id）
  source                TEXT NOT NULL,          -- 主页链接
  display_name          TEXT NOT NULL,          -- 博主昵称
  enabled               INTEGER NOT NULL DEFAULT 1,
  check_interval_hours  INTEGER NOT NULL DEFAULT 24,
  auto_process          INTEGER NOT NULL DEFAULT 0,  -- 新视频是否自动进入转写+AI 管线
  last_checked_at       INTEGER,
  last_success_at       INTEGER,
  next_check_at         INTEGER NOT NULL,
  last_error            TEXT,
  last_result_json      TEXT,                   -- 最近一次抓取结果摘要
  baseline_video_ids_json TEXT,                 -- 基线：最近审核通过的作品 ID 集
  deleted_at            INTEGER,                -- 取消关注=软删除，保留历史资产
  created_at            INTEGER NOT NULL,
  updated_at            INTEGER NOT NULL,
  UNIQUE(platform, source_key)
);
CREATE INDEX IF NOT EXISTS idx_sub_next_check ON subscription(next_check_at);
CREATE INDEX IF NOT EXISTS idx_sub_enabled ON subscription(enabled, deleted_at);

CREATE TABLE IF NOT EXISTS crawl_task (
  id                 TEXT PRIMARY KEY,
  subscription_id    TEXT NOT NULL REFERENCES subscription(id),
  trigger            TEXT NOT NULL,             -- manual / schedule
  status             TEXT NOT NULL,             -- pending/running/success/error
  total_videos       INTEGER,
  new_video_ids_json TEXT,
  error              TEXT,
  started_at         INTEGER,
  finished_at        INTEGER,
  created_at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crawl_task_sub ON crawl_task(subscription_id);

-- V0.4 通知中心：站内（local）/ Webhook / 邮件的统一发送记录
CREATE TABLE IF NOT EXISTS notification (
  id           TEXT PRIMARY KEY,
  channel      TEXT NOT NULL,                   -- local / webhook / email
  category     TEXT NOT NULL,                   -- new_videos / auto_process / auto_applied / manual
  title        TEXT NOT NULL,
  body         TEXT NOT NULL,
  payload_json TEXT,
  status       TEXT NOT NULL DEFAULT 'sent',    -- sent / failed
  error        TEXT,
  created_at   INTEGER NOT NULL,
  read_at      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_notification_created ON notification(created_at);
CREATE INDEX IF NOT EXISTS idx_notification_read ON notification(read_at);

-- V0.6 本地语义检索（参考 douyin-creator-distill 的语义索引）：预测向量存库
CREATE TABLE IF NOT EXISTS semantic_index (
  id           TEXT PRIMARY KEY,
  target_type  TEXT NOT NULL,                 -- prediction / content
  target_id    TEXT NOT NULL,
  model_id     TEXT NOT NULL,                 -- lightweight / high_precision
  dimension    INTEGER NOT NULL,
  vector       BLOB NOT NULL,                 -- float32 小端序列化的归一化向量
  text         TEXT,                          -- 入库文本（用于展示）
  created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_semantic_target ON semantic_index(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_semantic_model ON semantic_index(model_id);
"""


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or config.db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    """轻量迁移：列不存在则 ALTER TABLE 补列（CREATE TABLE IF NOT EXISTS 不会改已有表）"""
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db(db_path: Path | None = None) -> None:
    conn = get_conn(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        # V0.3 轻量迁移
        _ensure_column(conn, "verification", "auto_applied_at", "INTEGER")  # 硬预测自动过时间戳（24h 撤销窗口）
        # V0.6 互动数据迁移：content 表补点赞/评论/转发/收藏列
        _ensure_column(conn, "content", "digg_count", "INTEGER")
        _ensure_column(conn, "content", "comment_count", "INTEGER")
        _ensure_column(conn, "content", "share_count", "INTEGER")
        _ensure_column(conn, "content", "collect_count", "INTEGER")
        _ensure_column(conn, "transcript", "text_full_simplified", "TEXT")
        # V0.7 意图推断迁移：预测表补"AI 理解的意图"相关列
        _ensure_column(conn, "prediction", "interpreted_intent", "TEXT")
        _ensure_column(conn, "prediction", "intent_confidence", "REAL")
        _ensure_column(conn, "prediction", "direction_source", "TEXT")
        # V0.7 创作者头像迁移
        _ensure_column(conn, "creator", "avatar_url", "TEXT")
        _ensure_column(conn, "creator", "avatar_path", "TEXT")
        _ensure_column(conn, "creator", "avatar_src", "TEXT")
        # V0.8 观点抽取增强：claim 表补主题/立场/重要度/置信度/论据/代表短句列
        _ensure_column(conn, "claim", "topic", "TEXT")
        _ensure_column(conn, "claim", "stance", "TEXT")
        _ensure_column(conn, "claim", "importance", "REAL")
        _ensure_column(conn, "claim", "confidence", "REAL")
        _ensure_column(conn, "claim", "support", "TEXT")
        _ensure_column(conn, "claim", "key_phrase", "TEXT")
        # V0.2 生命周期从 pending_review 开始；兼容 V0.1 留下的 extracted 记录。
        conn.execute(
            "UPDATE prediction SET status='pending_review' WHERE status='extracted'"
        )
        conn.commit()
    finally:
        conn.close()


def log_event(conn: sqlite3.Connection, entity_type: str, entity_id: str,
              event_type: str, payload: str | None = None,
              actor: str = "system") -> None:
    import time
    import uuid
    conn.execute(
        "INSERT INTO event_log (id, entity_type, entity_id, event_type, payload, actor, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), entity_type, entity_id, event_type, payload, actor, int(time.time())),
    )
    conn.commit()


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {config.db_path}")
