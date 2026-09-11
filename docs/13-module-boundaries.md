# 13 · 模块边界

## 核心契约：PlatformAdapter（Protocol）

```python
class PlatformAdapter(Protocol):
    def extract_url(input: str) -> ParsedURL: ...          # 粘链接/分享文案都能解析
    def fetch_creator(platform_id: str) -> Creator: ...
    def fetch_creator_videos(creator_id: str, cursor=None) -> List[Content]: ...
    def fetch_content_meta(url: str) -> Content: ...
    def fetch_transcript(content_id: str) -> Optional[Transcript]: ...
    def download_media(content_id: str) -> Path: ...
```

## 核心契约：AIProvider（Protocol）

```python
class AIProvider(Protocol):
    def summarize(transcript: str, content_meta) -> Summary: ...
    def extract_claims(transcript: str) -> List[Claim]: ...
    def extract_predictions(transcript: str) -> List[Prediction]: ...
    def parse_time(expression: str, prediction_at) -> TimeWindow: ...
    def verify_prediction(prediction, evidences) -> AIVerdict: ...
```

## 核心契约：SearchProvider / MarketDataProvider

```python
class SearchProvider(Protocol):
    def search(query: str, since: datetime, until: datetime, n=10) -> List[SearchResult]: ...

class MarketDataProvider(Protocol):
    def get_series(symbol, start, end) -> List[DataPoint]: ...
    def get_current_price(symbol) -> float: ...
```

## 核心契约：KnowledgeBaseAdapter

```python
class KnowledgeBaseAdapter(Protocol):
    def ensure_structure(): ...                       # 建目录 + _index.md
    def write_verification(verification) -> Path: ...
    def write_prediction_summary(prediction) -> Path: ...
    def update_creator_reliability(creator_id): ...
```

## 扩展点：加新东西要改哪里

| 你要加 | 只需新建 | 必须改 | 禁止改 |
|--------|---------|--------|--------|
| **新平台**（快手/小红书/YouTube） | 1 个 `PlatformAdapter` 实现 | 适配器注册表 + 平台配置 | AI / DB / Verification |
| **新 AI 模型**（Claude/Gemini） | 1 个 `AIProvider` 实现 | `config/ai.config.json` + 调用路由 | 业务逻辑 |
| **新搜索引擎**（Perplexity/SerpAPI） | 1 个 `SearchProvider` 实现 | 注册表 + Evidence Collector 路由 | Verification Engine |
| **新知识库**（Notion/Logseq） | 1 个 `KnowledgeBaseAdapter` 实现 | Vault 路径配置 | SQLite 结构 |
| **新统计维度**（按行业/按情绪） | 1 个 `AggregationView` | Prediction 元数据字段（必要时） | 数据库 schema |

## 禁止的依赖方向

```
PlatformAdapter → AIProvider     ❌ 平台不碰 AI
AIProvider → SearchProvider      ❌ 模型不直接搜索（走 EvidenceService）
SearchProvider → VerificationEngine  ❌ 搜索不直接判预测
KnowledgeBaseAdapter → 业务逻辑   ❌ 知识库只写不读业务
```

## 数据流约束

```
所有模块只能通过 SQLite 交换数据
模块之间禁止直接函数调用跨越 2 层以上
所有 AI 输出带 model 版本 hash，可审计
```

## 版本策略

- 所有 Schema 带 `schema_version` 字段
- Schema 变更 → 写 migration SQL + 更新 Obsidian frontmatter 的 schema_version
- event_log 永不删除（可重放）
