"""Pydantic row models mirroring the SQLite schema (migration 001).

These are thin, permissive models: all timestamps are kept as RFC3339
strings (the SQL layer owns the CHECK constraint); the web layer does
not need ``datetime`` roundtripping for now.  Integer-booleans (such as
``runs.is_featured``) are exposed as ``bool`` here; the DB adapter is
expected to coerce 0/1 on read/write.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Typed enums
# ---------------------------------------------------------------------------

RunStatus = Literal[
    "pending", "running",
    "completed", "completed_with_errors",
    "failed", "cancelled", "orphaned",
]
RunKind = Literal["batch", "fork"]
ForkMode = Literal["diverge"]  # Phase 1 only; schema keeps room for 'replay'
Family = Literal["overlay", "earnings", "intraday"]

CheckpointStatus = Literal["writing", "ready", "failed", "archived"]
QuestionStatus = Literal["pending", "streaming", "completed", "failed"]


# ---------------------------------------------------------------------------
# Row models
# ---------------------------------------------------------------------------


class RunRow(BaseModel):
    id: str
    kind: RunKind
    parent_run_id: Optional[str] = None
    parent_checkpoint_id: Optional[str] = None
    scenario: str
    config_path: str
    model_id: str
    provider: str
    universe_json: Optional[str] = None
    date_start: str
    date_end: str
    status: RunStatus
    status_updated_at: str
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    last_heartbeat_at: Optional[str] = None
    created_at: str
    created_by: Optional[str] = None
    title: Optional[str] = None
    notes: Optional[str] = None
    is_featured: bool = False
    storage_dir: str
    fork_mode: Optional[ForkMode] = None
    fork_prompt_override: Optional[str] = None
    fork_model_override: Optional[str] = None
    family: Optional[Family] = None
    error_message: Optional[str] = None


class DecisionRecordRow(BaseModel):
    id: str
    run_id: str
    seq: int
    trading_day: str
    decision_ts: str
    model_id: str
    provider: str
    prompt_sha256: str
    response_sha256: str
    storage_dir: str
    has_trade: bool
    promoted_checkpoint_id: Optional[str] = None
    cum_pnl_after: Optional[float] = None
    created_at: str


class CheckpointRow(BaseModel):
    id: str
    run_id: str
    decision_record_id: str
    storage_dir: str
    fingerprint: str
    portfolio_summary_json: Optional[str] = None
    promoted_at: str
    promoted_by: str
    status: CheckpointStatus
    status_updated_at: str
    label: Optional[str] = None


class QuestionRow(BaseModel):
    id: str
    checkpoint_id: str
    parent_question_id: Optional[str] = None
    asked_by: str
    asked_at: str
    user_prompt: str
    model_id: str
    provider: str
    response_text: Optional[str] = None
    response_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    cost_usd: Optional[float] = None
    status: QuestionStatus
    status_updated_at: str
    error_message: Optional[str] = None


class QuestionChunkRow(BaseModel):
    question_id: str
    seq: int
    ts: str
    delta_text: str


class EventRow(BaseModel):
    id: int
    run_id: str
    subprocess_seq: int
    ts: str
    kind: str
    payload_json: str


class AuditLogRow(BaseModel):
    id: int
    ts: str
    actor_user_id: Optional[str] = None
    actor_email: Optional[str] = None
    action: str
    target_type: str
    target_id: str
    payload_json: Optional[str] = None


class SchemaVersionRow(BaseModel):
    version: int
    applied_at: str
    description: Optional[str] = None


class RunMetricsRow(BaseModel):
    run_id: str
    family: Optional[Family] = None
    generic_json: str
    family_json: Optional[str] = None
    behavioral_json: str
    computed_at: str
    evaluator_version: str
