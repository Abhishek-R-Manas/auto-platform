"""Bounded immutable forward protocols and explicitly authorized operator controls."""
from datetime import date, datetime
from typing import Annotated, Literal
from pydantic import Field, FiniteFloat, SecretStr, field_validator, model_validator
from ..research.schemas import Strict, Strategy, Risk, Execution, Calendar, Action, aware

Key = Annotated[str, Field(pattern=r"^(?:NSE|NFO):[A-Z0-9 &_.\-]{1,70}$")]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Text = Annotated[str, Field(min_length=2, max_length=120)]

class Supervision(Strict):
    max_spread_bps: Annotated[FiniteFloat, Field(gt=0, le=10000)] = 100
    expiry_exit_minutes: Annotated[int, Field(ge=5, le=180)] = 45
    intraday_exit_minutes: Annotated[int, Field(ge=5, le=120)] = 10
    gamma_shock_loss_pct: Annotated[FiniteFloat, Field(gt=0, le=100)] = 3
    theta_daily_loss_pct: Annotated[FiniteFloat, Field(gt=0, le=100)] = 2
    vega_ten_point_loss_pct: Annotated[FiniteFloat, Field(gt=0, le=100)] = 5
    correlated_exposure_pct: Annotated[FiniteFloat, Field(gt=0, le=5000)] = 150
    correlation_threshold: Annotated[FiniteFloat, Field(ge=.1, le=1)] = .8
    max_active_orders: Annotated[int, Field(ge=1, le=1000)] = 100
    max_orders_per_minute: Annotated[int, Field(ge=1, le=1000)] = 30
    worker_timeout_seconds: Annotated[int, Field(ge=5, le=300)] = 30
    minimum_sessions: Annotated[int, Field(ge=1, le=1000)] = 20
    minimum_session_minutes: Annotated[int, Field(ge=1, le=375)] = 60
    next_session_signal_window_seconds: Annotated[int, Field(ge=1, le=3600)] = 120
    minimum_fills: Annotated[int, Field(ge=1, le=100000)] = 10
    minimum_return_pct: Annotated[FiniteFloat, Field(ge=-100, le=10000)] = 0
    minimum_quote_coverage_pct: Annotated[FiniteFloat, Field(ge=1, le=100)] = 99
    event_budget: Annotated[int, Field(ge=1000, le=1000000)] = 100000

class Portfolio(Strict):
    request_id: Annotated[str, Field(min_length=1, max_length=100)]
    name: Text
    origin: Literal['REFERENCE', 'REGISTRY'] = 'REFERENCE'
    capital: Annotated[FiniteFloat, Field(ge=100, le=1000000000)] = 1000000
    candidate_ids: Annotated[list[Hash], Field(max_length=20)] = []
    strategies: Annotated[list[Strategy], Field(max_length=20)] = []
    bindings: Annotated[dict[str, Key], Field(min_length=1, max_length=50)]
    interval_minutes: Literal[1, 3, 5, 10, 15, 30, 60, 1440] = 5
    bar_source: Literal['KITE_CANDLES', 'QUOTE_SAMPLES'] = 'KITE_CANDLES'
    risk: Risk = Risk()
    execution: Execution = Execution(max_quote_age_seconds=30, latency_seconds=1)
    supervision: Supervision = Supervision()
    calendar: Calendar = Calendar()
    actions: Annotated[list[Action], Field(max_length=1000)] = []
    calendar_valid_until: date
    calendar_source: Annotated[str, Field(min_length=10, max_length=1000)]
    reviewer: Text
    review_note: Annotated[str, Field(min_length=20, max_length=3000)]
    data_rights_confirmed: bool = False
    assumptions_reviewed: bool = False
    compatibility_review: Annotated[str, Field(max_length=3000)] = ''
    benchmark_id: str | None = None
    evidence_features: Annotated[list[Annotated[str,Field(min_length=1,max_length=100)]],Field(max_length=20)]=[]
    evidence_policy_hash: Hash|None=None
    @model_validator(mode='after')
    def validate_protocol(self):
        if self.evidence_features and (not self.evidence_policy_hash or len(set(self.evidence_features))!=len(self.evidence_features)):
            raise ValueError('Evidence features require a current immutable input policy hash and distinct names')
        if self.origin == 'REGISTRY' and (not self.candidate_ids or self.strategies):
            raise ValueError('Registry portfolios accept approved candidate ids, not replacement strategies')
        if self.origin == 'REFERENCE' and (not self.strategies or self.candidate_ids):
            raise ValueError('Reference portfolios require explicit unqualified strategies')
        if self.origin == 'REGISTRY' and self.bar_source != 'KITE_CANDLES':
            raise ValueError('Registry bar strategies require actual completed broker candles')
        if self.execution.max_quote_age_seconds > 30:
            raise ValueError('Forward executable quotes must be no older than 30 seconds')
        if self.execution.bar_ambiguity != 'ADVERSE_FIRST' or self.execution.forced_reject_legs:
            raise ValueError('Forward operation cannot use optimistic or forced-rejection research scenarios')
        if self.execution.margin_model != 'CONSERVATIVE_GROSS':
            raise ValueError('Forward margin uses declared conservative estimates; historical margins are unavailable')
        if self.execution.expiry_policy != 'EXIT_BEFORE':
            raise ValueError('Forward portfolios must exit before expiry; no official settlement feed is installed')
        if len(set(self.candidate_ids)) != len(self.candidate_ids) or len(set(self.bindings.values())) != len(self.bindings):
            raise ValueError('Duplicate candidates or ambiguous instrument bindings')
        if self.benchmark_id and self.benchmark_id not in self.bindings:
            raise ValueError('Benchmark must have a current instrument binding')
        return self

class Control(Strict):
    action: Literal['RESUME', 'PAUSE', 'CANCEL_ENTRIES', 'LIQUIDATE', 'RETIRE']
    request_id: Annotated[str, Field(min_length=1, max_length=100)]
    reviewer: Text
    note: Annotated[str, Field(min_length=10, max_length=3000)]
    expected_revision: Annotated[int, Field(ge=0)]

class Acknowledge(Strict):
    action: Literal['ACKNOWLEDGE', 'RESOLVE'] = 'ACKNOWLEDGE'
    reviewer: Text
    note: Annotated[str, Field(min_length=10, max_length=3000)]

class RequestJob(Strict):
    request_id: Annotated[str, Field(min_length=1, max_length=100)]

class Monitoring(Strict):
    enabled: bool = False
    interval_seconds: Annotated[int, Field(ge=300, le=86400)] = 7200
    daily_requests: Annotated[int, Field(ge=1, le=100)] = 12
    max_output_tokens: Annotated[int, Field(ge=256, le=6000)] = 1500
    total_reserved_tokens: Annotated[int, Field(ge=256, le=1000000)] = 18000
    consent: bool = False
    @model_validator(mode='after')
    def authorization(self):
        if self.enabled and not self.consent: raise ValueError('Periodic model requests require explicit provider-usage consent')
        return self

class AIConfig(Strict):
    @field_validator('model',check_fields=False)
    @classmethod
    def model_is_not_key(cls,value):
        if value.startswith(('sk-','sk_')):raise ValueError('Enter a model name in the model field')
        return value
    api_key: Annotated[SecretStr, Field(min_length=8, max_length=512)]
    model: Annotated[str, Field(pattern=r'^[A-Za-z0-9_.:/\-]{1,160}$')]
    @field_validator('api_key')
    @classmethod
    def key_format(cls, value):
        raw=value.get_secret_value()
        if not raw.isascii() or any(c.isspace() for c in raw): raise ValueError('Enter the provider key without whitespace')
        return value

class Finding(Strict):
    severity: Literal['INFO', 'WARNING', 'CRITICAL']
    statement: Annotated[str, Field(min_length=1, max_length=1500)]
    evidence_ids: Annotated[list[str], Field(min_length=1, max_length=20)]
    proposed_action: Literal['OBSERVE', 'REVIEW', 'PAUSE', 'CANCEL_ENTRIES', 'LIQUIDATE']

class MonitorNote(Strict):
    summary: Annotated[str, Field(min_length=1, max_length=3000)]
    findings: Annotated[list[Finding], Field(max_length=20)]
    limitations: Annotated[list[Annotated[str, Field(max_length=1000)]], Field(min_length=1, max_length=20)]


class GuardianReview(Strict):
    request_id: Annotated[str, Field(min_length=1, max_length=100)]
    evidence_hash: Hash
    decision: Literal['CONTINUE_PAPER', 'APPROVE_REVIEW', 'REJECT', 'REVOKE']
    reviewer: Text
    note: Annotated[str, Field(min_length=20, max_length=3000)]
    conflicts: Annotated[str, Field(min_length=10, max_length=1500)]
    expires_at: datetime
    @field_validator('expires_at')
    @classmethod
    def expiry_has_timezone(cls,value):
        if value.tzinfo is None or value.utcoffset() is None:raise ValueError('Review expiry requires a timezone')
        return value
