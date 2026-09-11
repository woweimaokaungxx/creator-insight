"""领域模型（Pydantic）——对应 docs/05、06 的 Schema"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# ─── 枚举 ────────────────────────────────────────────────
SubjectType = Literal["stock", "index", "fx", "commodity", "macro_indicator",
                      "policy_event", "company_event", "unknown"]
Direction = Literal["up", "down", "flat", "range", "above", "below",
                    "event_yes", "event_no", "unknown"]
Granularity = Literal["day", "week", "month", "quarter", "year", "event_horizon", "fuzzy"]
PredictionStatus = Literal["extracted", "pending_review", "active", "due",
                           "verifying", "ai_verified", "human_review", "final", "invalid", "uncertain"]
Verdict = Literal["correct", "partial", "incorrect", "inconclusive", "invalid"]


# ─── 子结构 ───────────────────────────────────────────────
class Subject(BaseModel):
    type: SubjectType = "unknown"
    symbol: Optional[str] = ""   # 允许 None（AI 可能对政策类返回 null），兜底为空串
    name: str = ""


class Magnitude(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None
    unit: Optional[str] = ""   # AI 可能返回 null；兜底为空串
    is_relative: Optional[bool] = True   # AI 可能返回 null；兜底为 True

    @property
    def has_value(self) -> bool:
        return self.min is not None or self.max is not None


class TimeWindow(BaseModel):
    raw: str = ""
    parsed_start: Optional[int] = None
    parsed_end: Optional[int] = None
    granularity: Granularity = "fuzzy"
    is_fuzzy: bool = True
    parsed_at: Optional[int] = None
    requires_human_confirmation: bool = False


# ─── 主模型 ───────────────────────────────────────────────
class PredictionIn(BaseModel):
    """AI 抽取结果（原始输出，尚未入库）"""
    raw_text: str
    # 意图推断：AI 对"博主想预测什么"的理解，不等于原话
    interpreted_intent: str = ""
    speaker: str = "博主本人"
    start_offset: Optional[float] = None
    end_offset: Optional[float] = None
    subject: Subject = Subject()
    direction: Direction = "unknown"
    direction_source: Optional[str] = None   # explicit（明说）| inferred（AI 推断）
    magnitude: Optional[Magnitude] = None
    time_expression_raw: str = ""
    time_window: TimeWindow = TimeWindow()
    conditions: list[str] = Field(default_factory=list)
    confidence_raw: str = ""
    confidence_score: Optional[float] = None
    intent_confidence: Optional[float] = None   # AI 对"这确实是预测"的把握 0-1
    inference_notes: str = ""                   # 推断依据说明
    needs_human_confirmation: bool = False      # 抽取质量低，需人工确认


class Prediction(BaseModel):
    """库内 Prediction（带状态）"""
    id: str
    content_id: str
    parent_prediction_id: Optional[str] = None
    revision_no: int = 1
    raw_text: str
    speaker: str = "博主本人"
    start_offset: Optional[float] = None
    end_offset: Optional[float] = None
    subject: Subject = Subject()
    direction: Direction = "unknown"
    magnitude: Optional[Magnitude] = None
    time_expression_raw: str = ""
    time_window: TimeWindow = TimeWindow()
    conditions: list[str] = Field(default_factory=list)
    confidence_raw: str = ""
    confidence_score: Optional[float] = None
    status: PredictionStatus = "extracted"
    prediction_at: int
    due_at: Optional[int] = None
    auto_apply_eligible: bool = False
    prediction_baseline_evidence_id: Optional[str] = None
    created_at: int
    updated_at: int


class Evidence(BaseModel):
    id: str
    prediction_id: Optional[str] = None
    content_id: Optional[str] = None
    source: str
    source_type: str
    url: Optional[str] = None
    title: Optional[str] = None
    publisher: Optional[str] = None
    published_at: Optional[int] = None
    collected_at: int
    is_official: bool = False
    is_direct: bool = False
    credibility: Optional[float] = None
    summary: Optional[str] = None
    raw_json: Optional[str] = None
    relation: Optional[str] = None
    collection_batch: Optional[str] = None


class Verification(BaseModel):
    id: str
    prediction_id: str
    ai_verdict: Optional[Verdict] = None
    ai_score: Optional[float] = None
    ai_confidence: Optional[float] = None
    ai_reasoning_json: Optional[str] = None
    ai_evidence_ids: Optional[list[str]] = None
    ai_judged_at: Optional[int] = None
    human_verdict: Optional[Verdict] = None
    human_score: Optional[float] = None
    human_notes: Optional[str] = None
    human_reviewed_at: Optional[int] = None
    final_verdict: Verdict = "inconclusive"
    locked: bool = False


# ─── 统计 ─────────────────────────────────────────────────
class ReliabilityStats(BaseModel):
    creator_id: str
    computed_at: int
    total_predictions: int = 0
    verified_count: int = 0
    correct_count: int = 0
    partial_count: int = 0
    incorrect_count: int = 0
    inconclusive_count: int = 0
    base_accuracy: Optional[float] = None
    accuracy_by_domain: dict[str, float] = Field(default_factory=dict)
    accuracy_by_horizon: dict[str, float] = Field(default_factory=dict)
    accuracy_by_subject: dict[str, float] = Field(default_factory=dict)
    accuracy_by_confidence_band: dict[str, float] = Field(default_factory=dict)
    calibration_score: Optional[float] = None
    sample_size_warning: list[str] = Field(default_factory=list)
