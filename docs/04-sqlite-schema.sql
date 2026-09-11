-- ============================================================
-- creator-insight · SQLite 表设计 (v0.1)
-- 9 张核心表：creator / content / transcript / claim /
-- prediction / evidence / verification / creator_reliability / event_log
-- ============================================================

-- 1. Creator（博主）
CREATE TABLE creator (
  id            TEXT PRIMARY KEY,           -- ULID
  platform      TEXT NOT NULL,              -- 'douyin' | 'bilibili' | ...
  platform_id   TEXT NOT NULL,              -- 平台原始 sec_uid / mid
  name          TEXT NOT NULL,
  url           TEXT NOT NULL,
  domain_tags   TEXT,                       -- JSON: ['finance','macro','policy']
  added_at      INTEGER NOT NULL,
  notes         TEXT,                       -- 手工备注
  UNIQUE(platform, platform_id)
);

-- 2. Content（内容作品）
CREATE TABLE content (
  id              TEXT PRIMARY KEY,
  creator_id      TEXT NOT NULL REFERENCES creator(id),
  platform        TEXT NOT NULL,
  platform_vid    TEXT NOT NULL,
  title           TEXT,
  url             TEXT NOT NULL,
  published_at    INTEGER,
  duration_sec    INTEGER,
  fetched_at      INTEGER NOT NULL,
  media_path      TEXT,                     -- 视频本地缓存（可选）
  cover_path      TEXT,
  raw_meta_json   TEXT,                     -- 平台原始 JSON 证据
  UNIQUE(platform, platform_vid)
);
CREATE INDEX idx_content_creator ON content(creator_id);

-- 3. Transcript（逐字稿 · 不可被覆盖）
CREATE TABLE transcript (
  id            TEXT PRIMARY KEY,
  content_id    TEXT NOT NULL UNIQUE REFERENCES content(id),
  source        TEXT NOT NULL,              -- 'platform_subtitle' | 'whisper_local' | 'cloud_asr'
  language      TEXT,
  text_full     TEXT NOT NULL,              -- 完整逐字稿
  segments_json TEXT,                       -- [{start,end,text},...] 含时间戳
  created_at    INTEGER NOT NULL
);

-- 4. Claim（观点 · 不可证伪）
CREATE TABLE claim (
  id              TEXT PRIMARY KEY,
  content_id      TEXT NOT NULL REFERENCES content(id),
  text            TEXT NOT NULL,
  speaker         TEXT,                     -- 博主本人/嘉宾/引用
  start_offset    REAL,
  end_offset      REAL,
  category        TEXT,                     -- 'opinion' | 'analysis' | 'context'
  created_at      INTEGER NOT NULL
);

-- 5. Prediction（核心 · 可证伪预测）
CREATE TABLE prediction (
  id                   TEXT PRIMARY KEY,
  content_id           TEXT NOT NULL REFERENCES content(id),
  parent_prediction_id TEXT REFERENCES prediction(id),   -- Revision 链
  revision_no          INTEGER DEFAULT 1,

  -- 原始语言
  raw_text             TEXT NOT NULL,       -- 博主原话（永不可变）
  speaker              TEXT,
  start_offset         REAL,
  end_offset           REAL,

  -- 抽取字段
  subject              TEXT,                -- JSON: {type:'stock', symbol:'AAPL', name:'苹果'}
  direction            TEXT,                -- 'up'|'down'|'flat'|'range'|'above'|'below'|'event_yes'|'event_no'
  magnitude            TEXT,                -- JSON: {min,max,unit,is_relative}
  time_expression_raw  TEXT,                -- "未来 6 个月"
  time_window          TEXT,                -- JSON: {raw,parsed_start,parsed_end,granularity,is_fuzzy,parsed_at}
  conditions           TEXT,                -- JSON: ["if fed cuts",...]
  confidence_raw       TEXT,                -- "大概率"/"我敢打赌"
  confidence_score     REAL,                -- AI 转换 0-1（先存不急用）

  -- 生命周期
  status               TEXT NOT NULL,       -- 见状态机（extracted/pending_review/active/due/verifying/ai_verified/human_review/final/invalid/uncertain）
  prediction_at        INTEGER NOT NULL,    -- 预测发表时间
  due_at               INTEGER,             -- 应当验证的时间（is_fuzzy 时可为空）
  auto_apply_eligible  INTEGER DEFAULT 0,   -- 1 = 系统可自动验证（硬预测），0 = 必须人工
  prediction_baseline_evidence_id TEXT REFERENCES evidence(id),

  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL
);
CREATE INDEX idx_prediction_status ON prediction(status);
CREATE INDEX idx_prediction_due   ON prediction(due_at);
CREATE INDEX idx_prediction_content ON prediction(content_id);

-- 6. Evidence（验证证据）
CREATE TABLE evidence (
  id               TEXT PRIMARY KEY,
  prediction_id    TEXT REFERENCES prediction(id),   -- baseline 时可为空
  content_id       TEXT REFERENCES content(id),

  source           TEXT NOT NULL,          -- 'tavily'|'bing'|'akshare'|'fred'|'manual'
  source_type      TEXT NOT NULL,          -- 'news'|'official'|'market_data'|'filings'|'regulatory'
  url              TEXT,
  title            TEXT,
  publisher        TEXT,

  published_at     INTEGER,                -- 证据原始发布时间（核心 · 事后诸葛亮）
  collected_at     INTEGER NOT NULL,       -- 我们抓到的时间
  is_official      INTEGER DEFAULT 0,      -- 0/1
  is_direct        INTEGER DEFAULT 0,      -- 是否直接证据（vs 二手转述）
  credibility      REAL,                   -- 0-1

  summary          TEXT,                   -- AI 摘要
  raw_json         TEXT,                   -- 原始内容
  relation         TEXT,                   -- 'baseline'|'primary'|'contradicting'|'corroborating'|'context'

  collection_batch TEXT,                   -- 同批次 UUID
  created_at       INTEGER NOT NULL
);
CREATE INDEX idx_evidence_prediction ON evidence(prediction_id);

-- 7. Verification（验证结果 · ai 与 human 分离）
CREATE TABLE verification (
  id                TEXT PRIMARY KEY,
  prediction_id     TEXT NOT NULL UNIQUE REFERENCES prediction(id),

  -- AI 初始判断
  ai_verdict        TEXT,                  -- 'correct'|'partial'|'incorrect'|'inconclusive'|'invalid'
  ai_score          REAL,                  -- 0-1
  ai_confidence     REAL,                  -- AI 对自己判断的确信度
  ai_reasoning_json TEXT,                  -- 多步推理链（必须保留）
  ai_evidence_ids   TEXT,                  -- JSON array of evidence.id
  ai_judged_at      INTEGER,

  -- 人工最终判断
  human_verdict     TEXT,                  -- 枚举同上
  human_score       REAL,
  human_notes       TEXT,
  human_reviewed_at INTEGER,

  -- 锁定
  final_verdict     TEXT NOT NULL,         -- human 优先，缺失则用 ai
  locked            INTEGER DEFAULT 0,

  created_at        INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL
);

-- 8. Creator Reliability（聚合视图 · 计算结果，非原始数据）
CREATE TABLE creator_reliability (
  creator_id             TEXT PRIMARY KEY REFERENCES creator(id),
  computed_at            INTEGER NOT NULL,

  total_predictions      INTEGER,
  verified_count         INTEGER,
  correct_count          INTEGER,
  partial_count          INTEGER,
  incorrect_count        INTEGER,
  inconclusive_count     INTEGER,

  base_accuracy          REAL,             -- (correct + partial*0.5) / verified

  accuracy_by_domain     TEXT,             -- JSON: {"macro":0.81,...}
  accuracy_by_horizon    TEXT,             -- JSON: {"short":0.49,...}
  accuracy_by_subject    TEXT,             -- JSON: {"index":0.74,...}
  accuracy_by_confidence_band TEXT,        -- JSON: {"0.0-0.3":...,}

  calibration_score      REAL              -- Brier Score，样本足够时才计算
);

-- 9. Event Log（事件日志 · 可回放）
CREATE TABLE event_log (
  id          TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id   TEXT NOT NULL,
  event_type  TEXT NOT NULL,
  payload     TEXT,
  actor       TEXT NOT NULL,               -- 'system'|'human'|'ai:<provider>'
  created_at  INTEGER NOT NULL
);
CREATE INDEX idx_event_entity ON event_log(entity_type, entity_id);
