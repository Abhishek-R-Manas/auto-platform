"""Strict research contracts. User strategies are data, never executable Python."""
from datetime import datetime, time, timezone
from typing import Annotated, Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator, model_validator

VERSION = "research/2.0.0"
ID = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:|+\-]+$")]
Positive = Annotated[FiniteFloat, Field(gt=0)]


def aware(value):
    if isinstance(value,str): value=datetime.fromisoformat(value.replace("Z","+00:00"))
    if value.tzinfo is None: raise ValueError("A timezone-aware timestamp is required")
    return value.astimezone(timezone.utc)


def iso(value):
    return aware(value).isoformat()


class Strict(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False)


class Terms(Strict):
    effective_at: datetime
    lot_size: Annotated[int,Field(ge=1,le=1000000)]=1
    tick_size: Positive=.05
    margin_rate: Annotated[FiniteFloat,Field(gt=0,le=2)]=.18
    _timestamp=field_validator("effective_at")(aware)


class Instrument(Strict):
    id: ID
    symbol: Annotated[str,Field(min_length=1,max_length=80)]
    kind: Literal["EQ","INDEX","FUT","OPT"]
    underlying: ID
    exchange: Annotated[str,Field(min_length=1,max_length=20)]="NSE"
    lot_size: Annotated[int,Field(ge=1,le=1000000)]=1
    tick_size: Positive=.05
    expiry: datetime|None=None
    strike: Positive|None=None
    option_type: Literal["CE","PE"]|None=None
    settlement: Literal["CASH","PHYSICAL"]="CASH"
    listed_at: datetime|None=None
    delisted_at: datetime|None=None
    terms: Annotated[list[Terms],Field(max_length=100)]=[]
    @field_validator("expiry","listed_at","delisted_at")
    @classmethod
    def dates(cls,value): return aware(value) if value else value
    @model_validator(mode="after")
    def contracts(self):
        if self.kind in {"FUT","OPT"} and not self.expiry: raise ValueError("Derivative expiry is required")
        if self.kind=="OPT" and (not self.strike or not self.option_type): raise ValueError("Options need strike and CE/PE")
        if self.kind in {"EQ","INDEX"} and self.lot_size!=1: raise ValueError("Equities/index reference units must use lot_size=1")
        if len({t.effective_at for t in self.terms})!=len(self.terms): raise ValueError("Duplicate contract terms date")
        self.terms.sort(key=lambda t:t.effective_at)
        return self


class Session(Strict):
    open: time=time(9,15)
    close: time=time(15,30)
    @model_validator(mode="after")
    def interval(self):
        if self.close<=self.open: raise ValueError("Session close must follow open")
        return self


class Calendar(Strict):
    timezone: str="Asia/Kolkata"
    holidays: Annotated[list[str],Field(max_length=5000)]=[]
    session: Session=Session()
    special_sessions: dict[str,Session]={}
    settlement_days: Annotated[int,Field(ge=0,le=10)]=1
    @field_validator("timezone")
    @classmethod
    def zone(cls,value):
        try: ZoneInfo(value)
        except (KeyError,ValueError): raise ValueError("Unknown calendar timezone") from None
        return value
    @field_validator("holidays")
    @classmethod
    def days(cls,values):
        for v in values: datetime.strptime(v,"%Y-%m-%d")
        return sorted(set(values))
    @field_validator("special_sessions")
    @classmethod
    def special(cls,value):
        for day in value: datetime.strptime(day,"%Y-%m-%d")
        return value


class Action(Strict):
    instrument_id: ID
    kind: Literal["SPLIT","DIVIDEND","SYMBOL_CHANGE","DELIST","CONTRACT_ADJUSTMENT"]
    effective_at: datetime
    known_at: datetime
    ratio: Positive=1
    amount: Annotated[FiniteFloat,Field(ge=0)]=0
    payable_at: datetime|None=None
    new_symbol: str|None=None
    @field_validator("effective_at","known_at","payable_at")
    @classmethod
    def dates(cls,value): return aware(value) if value else value
    @model_validator(mode="after")
    def chronology(self):
        if self.known_at>self.effective_at: raise ValueError("Action must have been known by its effective time")
        if self.payable_at and self.payable_at<self.effective_at: raise ValueError("Pay date precedes entitlement")
        return self


class FeeRules(Strict):
    effective_from: datetime=datetime(1900,1,1,tzinfo=timezone.utc)
    source: str="ILLUSTRATIVE_CURRENT_CHARGES"
    brokerage: Annotated[FiniteFloat,Field(ge=0,le=100000)]=20
    brokerage_rate: Annotated[FiniteFloat,Field(ge=0,le=.1)]=.0003
    equity_delivery_brokerage: Annotated[FiniteFloat,Field(ge=0,le=100000)]=0
    equity_stt_delivery: Annotated[FiniteFloat,Field(ge=0,le=.1)]=.001
    equity_stt_intraday_sell: Annotated[FiniteFloat,Field(ge=0,le=.1)]=.00025
    futures_stt_sell: Annotated[FiniteFloat,Field(ge=0,le=.1)]=.0005
    options_stt_sell: Annotated[FiniteFloat,Field(ge=0,le=.1)]=.0015
    exchange_eq: Annotated[FiniteFloat,Field(ge=0,le=.1)]=.0000307
    exchange_fut: Annotated[FiniteFloat,Field(ge=0,le=.1)]=.0000183
    exchange_opt: Annotated[FiniteFloat,Field(ge=0,le=.1)]=.0003553
    stamp_eq: Annotated[FiniteFloat,Field(ge=0,le=.1)]=.00015
    stamp_eq_intraday: Annotated[FiniteFloat,Field(ge=0,le=.1)]=.00003
    stamp_fut: Annotated[FiniteFloat,Field(ge=0,le=.1)]=.00002
    stamp_opt: Annotated[FiniteFloat,Field(ge=0,le=.1)]=.00003
    sebi_rate: Annotated[FiniteFloat,Field(ge=0,le=.1)]=.000001
    gst_rate: Annotated[FiniteFloat,Field(ge=0,le=1)]=.18
    dp_fee: Annotated[FiniteFloat,Field(ge=0,le=100000)]=15.34
    _timestamp=field_validator("effective_from")(aware)


class Manifest(Strict):
    name: Annotated[str,Field(min_length=1,max_length=120)]
    parent_dataset_id: Annotated[str,Field(pattern=r"^[a-f0-9]{64}$")]|None=None
    revision_note: Annotated[str,Field(max_length=2000)]=""
    source: Annotated[str,Field(min_length=1,max_length=200)]
    source_kind: Literal["SYNTHETIC","BROKER","VENDOR"]="VENDOR"
    license_note: Annotated[str,Field(max_length=2000)]="Not independently verified"
    rights_confirmed: bool=False
    observation_clock: Literal["UNSPECIFIED","EXCHANGE_AND_RECEIPT"]="UNSPECIFIED"
    frequency: Literal["DAY","MINUTE","QUOTE"]="DAY"
    interval_minutes: Annotated[int,Field(ge=1,le=1440)]=1
    timestamp_semantics: Literal["START","END"]="END"
    prices_adjusted: bool=False
    point_in_time_universe: bool=False
    corporate_actions_complete: bool=False
    margin_history_complete: bool=False
    instruments: Annotated[list[Instrument],Field(min_length=1,max_length=2000)]
    calendar: Calendar=Calendar()
    actions: Annotated[list[Action],Field(max_length=10000)]=[]
    fee_rules: Annotated[list[FeeRules],Field(min_length=1,max_length=100)]=[FeeRules()]
    notes: Annotated[str,Field(max_length=4000)]=""
    @model_validator(mode="after")
    def integrity(self):
        ids=[i.id for i in self.instruments]
        if len(set(ids))!=len(ids): raise ValueError("Instrument ids must be unique")
        if any(a.instrument_id not in ids for a in self.actions): raise ValueError("Unknown corporate-action instrument")
        if len({r.effective_from for r in self.fee_rules})!=len(self.fee_rules): raise ValueError("Duplicate fee rule date")
        self.fee_rules.sort(key=lambda r:r.effective_from)
        return self


class ImportData(Strict):
    manifest: Manifest
    format: Literal["CSV","PARQUET"]="CSV"
    content: Annotated[str,Field(min_length=1,max_length=32000000)]


class Selector(Strict):
    underlying: ID
    kind: Literal["FUT","OPT"]="OPT"
    option_type: Literal["CE","PE"]|None=None
    expiry_rank: Annotated[int,Field(ge=0,le=20)]=0
    min_dte: Annotated[int,Field(ge=0,le=1000)]=1
    max_dte: Annotated[int,Field(ge=0,le=1000)]=90
    strike_offset: Annotated[int,Field(ge=-100,le=100)]=0
    target_delta: Annotated[FiniteFloat,Field(gt=0,lt=1)]|None=None
    @model_validator(mode="after")
    def dates(self):
        if self.min_dte>self.max_dte: raise ValueError("Invalid expiry range")
        if self.kind=="OPT" and not self.option_type: raise ValueError("Option selection needs CE/PE")
        return self


class Leg(Strict):
    instrument_id: ID|None=None
    selector: Selector|None=None
    ratio: Annotated[int,Field(ge=-100,le=100)]=1
    product: Literal["AUTO","DELIVERY","INTRADAY","DERIVATIVE"]="AUTO"
    @model_validator(mode="after")
    def instrument(self):
        if bool(self.instrument_id)==bool(self.selector): raise ValueError("Specify an instrument id or a selector")
        if self.ratio==0: raise ValueError("Leg ratio cannot be zero")
        return self


class Parameters(Strict):
    fast: Annotated[int,Field(ge=2,le=500)]=5
    slow: Annotated[int,Field(ge=3,le=1000)]=20
    lookback: Annotated[int,Field(ge=2,le=1000)]=20
    entry_z: Annotated[FiniteFloat,Field(gt=0,le=10)]=2
    exit_z: Annotated[FiniteFloat,Field(ge=0,le=10)]=.25
    threshold: Annotated[FiniteFloat,Field(ge=0,le=1)]=.01
    rebalance_bars: Annotated[int,Field(ge=1,le=10000)]=20
    top_n: Annotated[int,Field(ge=1,le=50)]=3
    hold_bars: Annotated[int,Field(ge=1,le=100000)]=20
    ridge: Annotated[FiniteFloat,Field(gt=0,le=100)]=.01
    model_lags: Annotated[int,Field(ge=1,le=10)]=3


class Scheduled(Strict):
    at: datetime
    action: Literal["ENTER","EXIT","CANCEL"]="ENTER"
    _timestamp=field_validator("at")(aware)


class Strategy(Strict):
    id: ID
    name: Annotated[str,Field(min_length=1,max_length=120)]
    family: Literal["TREND","MEAN_REVERSION","BREAKOUT","MOMENTUM","PAIRS","SCHEDULED","RULES","LINEAR_MODEL","VOLATILITY","MARKET_MAKING"]="TREND"
    version: Annotated[str,Field(min_length=1,max_length=80)]="1.0.0"
    signal_instruments: Annotated[list[ID],Field(max_length=50)]=[]
    legs: Annotated[list[Leg],Field(min_length=1,max_length=20)]
    parameters: Parameters=Parameters()
    allocation_pct: Annotated[FiniteFloat,Field(gt=0,le=100)]=25
    size_mode: Literal["RISK","LOTS","ALLOCATION"]="RISK"
    lots: Annotated[int,Field(ge=1,le=100000)]=1
    stop_pct: Annotated[FiniteFloat,Field(gt=0,le=100)]=2
    target_multiple: Annotated[FiniteFloat,Field(gt=0,le=100)]|None=2
    allow_short: bool=False
    exit_dte_days: Annotated[FiniteFloat,Field(ge=0,le=1000)]=1
    allow_overnight: bool=True
    roll_on_expiry: bool=False
    order_type: Literal["MARKET","LIMIT"]="MARKET"
    limit_offset_bps: Annotated[FiniteFloat,Field(ge=0,le=1000)]=0
    schedule: Annotated[list[Scheduled],Field(max_length=1000)]=[]
    entry_rule: dict|None=None
    exit_rule: dict|None=None
    @model_validator(mode="after")
    def validate_spec(self):
        if self.parameters.fast>=self.parameters.slow: raise ValueError("Fast average must be shorter than slow")
        if self.family=="SCHEDULED" and not self.schedule: raise ValueError("Scheduled strategies require decision timestamps")
        if self.family=="RULES" and not self.entry_rule: raise ValueError("A declarative entry rule is required")
        if self.roll_on_expiry and not any(l.selector for l in self.legs): raise ValueError("Rollover requires a dynamic contract selector")
        if self.family=="PAIRS" and len(self.signal_instruments)!=2: raise ValueError("Pairs need two signal instruments")
        return self


class Risk(Strict):
    trade_pct: Annotated[FiniteFloat,Field(gt=0,le=50)]=.25
    open_pct: Annotated[FiniteFloat,Field(gt=0,le=100)]=.75
    daily_pct: Annotated[FiniteFloat,Field(gt=0,le=100)]=1
    weekly_pct: Annotated[FiniteFloat,Field(gt=0,le=100)]=2
    drawdown_pct: Annotated[FiniteFloat,Field(gt=0,le=100)]=5
    margin_pct: Annotated[FiniteFloat,Field(gt=0,le=100)]=40
    slippage_bps: Annotated[FiniteFloat,Field(ge=0,le=1000)]=2
    overnight_stress_pct: Annotated[FiniteFloat,Field(gt=0,le=100)]=5
    gross_exposure_pct: Annotated[FiniteFloat,Field(gt=0,le=5000)]=300
    underlying_exposure_pct: Annotated[FiniteFloat,Field(gt=0,le=5000)]=150
    cash_buffer_pct: Annotated[FiniteFloat,Field(ge=0,le=100)]=0
    @model_validator(mode="after")
    def hierarchy(self):
        if not self.trade_pct<=self.open_pct<=self.drawdown_pct: raise ValueError("Trade/open/drawdown limits must increase")
        return self


class Execution(Strict):
    spread_bps: Annotated[FiniteFloat,Field(ge=0,le=1000)]=5
    participation_pct: Annotated[FiniteFloat,Field(gt=0,le=100)]=5
    latency_seconds: Annotated[FiniteFloat,Field(ge=0,le=86400)]=0
    cancel_latency_seconds: Annotated[FiniteFloat,Field(ge=0,le=86400)]=0
    max_quote_age_seconds: Annotated[int,Field(ge=1,le=604800)]=86400
    order_ttl_bars: Annotated[int,Field(ge=1,le=10000)]=5
    bar_ambiguity: Literal["ADVERSE_FIRST","FAVORABLE_FIRST"]="ADVERSE_FIRST"
    margin_model: Literal["HISTORICAL_GROSS","CONSERVATIVE_GROSS"]="CONSERVATIVE_GROSS"
    futures_margin_rate: Annotated[FiniteFloat,Field(gt=0,le=2)]=.18
    short_option_margin_rate: Annotated[FiniteFloat,Field(gt=0,le=2)]=.25
    allow_uncovered_options: bool=False
    expiry_policy: Literal["EXIT_BEFORE","SETTLE"]="EXIT_BEFORE"
    interest_rate: Annotated[FiniteFloat,Field(ge=-.1,le=1)]=.065
    dividend_yield: Annotated[FiniteFloat,Field(ge=0,le=1)]=0
    forced_reject_legs: Annotated[list[ID],Field(max_length=100)]=[]


class Window(Strict):
    label: Literal["TRAIN","VALIDATION","HOLDOUT","WALK_FORWARD"]
    start: datetime
    end: datetime
    _dates=field_validator("start","end")(aware)
    @model_validator(mode="after")
    def ordering(self):
        if self.end<=self.start: raise ValueError("Window end must follow start")
        return self



class BasketProtocol(Strict):
    version: Literal["mvp2/1.0"]="mvp2/1.0"
    contract_id: Annotated[str,Field(pattern=r"^[a-f0-9]{64}$")]
    definition_hash: Annotated[str,Field(pattern=r"^[a-f0-9]{64}$")]
    entry_at: datetime
    exit_at: datetime
    entry_deadline: datetime
    reference_id: ID
    units: Annotated[int,Field(ge=1,le=1000000)]
    lot_size: Annotated[int,Field(ge=1,le=100000)]
    tick_size_paise: Annotated[int,Field(ge=1,le=100000)]
    min_depth_units: Annotated[int,Field(ge=1,le=100000000)]
    max_spread_bps: Annotated[int,Field(ge=1,le=10000)]
    max_expiry_loss_paise: Annotated[int,Field(gt=0,le=1000000000000)]
    estimated_costs_paise: Annotated[int,Field(ge=0,le=1000000000)]
    fee_multiplier: Annotated[FiniteFloat,Field(ge=1,le=4)]=1
    clock_mode: Literal["RECORDED_RECEIPT","ASSUMED_EXCHANGE"]
    _dates=field_validator("entry_at","exit_at","entry_deadline")(aware)
    @model_validator(mode="after")
    def bounds(self):
        if not self.entry_at<self.entry_deadline<=self.exit_at:raise ValueError("Entry deadline must follow entry and not exceed exit")
        if self.units%self.lot_size:raise ValueError("Protocol units must use whole lots")
        return self

class Run(Strict):
    contract_protocol: BasketProtocol|None=None
    dataset_id: Annotated[str,Field(pattern=r"^[a-f0-9]{64}$")]
    name: Annotated[str,Field(min_length=1,max_length=120)]="Research experiment"
    capital: Annotated[FiniteFloat,Field(ge=100,le=1000000000)]=1000000
    strategies: Annotated[list[Strategy],Field(min_length=1,max_length=20)]
    risk: Risk=Risk()
    execution: Execution=Execution()
    seed: Annotated[int,Field(ge=0,le=2147483647)]=42
    start: datetime|None=None
    end: datetime|None=None
    training_end: datetime|None=None
    windows: Annotated[list[Window],Field(max_length=100)]=[]
    embargo_bars: Annotated[int,Field(ge=0,le=10000)]=0
    benchmark_id: ID|None=None
    experiment_id: ID|None=None
    @field_validator("start","end","training_end")
    @classmethod
    def dates(cls,value): return aware(value) if value else value
    @model_validator(mode="after")
    def chronology(self):
        if len({s.id for s in self.strategies})!=len(self.strategies): raise ValueError("Strategy ids must be unique")
        if sum(s.allocation_pct for s in self.strategies)>100.000001: raise ValueError("Strategy allocations exceed shared capital")
        if self.start and self.end and self.start>=self.end: raise ValueError("Run start must precede end")
        if any(s.family=="LINEAR_MODEL" for s in self.strategies) and not self.training_end: raise ValueError("A model needs a training cutoff")
        ordered=sorted(self.windows,key=lambda w:w.start)
        if any(a.end>=b.start for a,b in zip(ordered,ordered[1:])): raise ValueError("Evaluation windows overlap")
        ranks={"TRAIN":0,"VALIDATION":1,"WALK_FORWARD":1,"HOLDOUT":2}
        if any(ranks[a.label]>ranks[b.label] for a,b in zip(ordered,ordered[1:])): raise ValueError("Training, validation and holdout roles must be chronological")
        for label in ("TRAIN","VALIDATION","HOLDOUT"):
            if sum(w.label==label for w in ordered)>1: raise ValueError("Use one window per role or separate walk-forward runs")
        if self.contract_protocol:
            p=self.contract_protocol
            if len(self.strategies)!=1:raise ValueError("A basket protocol requires exactly one strategy")
            strategy=self.strategies[0]
            expected=[(p.entry_at,"ENTER"),(p.exit_at,"EXIT")]
            if strategy.family!="SCHEDULED" or [(x.at,x.action) for x in strategy.schedule]!=expected:raise ValueError("Schedule differs from the frozen basket protocol")
            if strategy.allow_overnight or strategy.roll_on_expiry or strategy.target_multiple is not None or strategy.stop_pct!=100:raise ValueError("Implicit strategy exits are unavailable for fixed basket protocols")
            if len(strategy.legs) not in {2,4} or any(not l.instrument_id or abs(l.ratio)!=1 for l in strategy.legs):raise ValueError("Fixed equal-unit option legs required")
            if strategy.size_mode!="LOTS" or strategy.lots*p.lot_size!=p.units:raise ValueError("Sizing differs from the basket protocol")
            if self.execution.cancel_latency_seconds!=0:raise ValueError("Basket protocol requires synchronous simulated entry cancellation")
            if self.start!=p.entry_at or not self.end or self.end<p.exit_at:raise ValueError("Evaluation window differs from basket decision bounds")
        self.windows=ordered
        if self.training_end and any(w.label in {"VALIDATION","HOLDOUT","WALK_FORWARD"} and w.start<=self.training_end for w in ordered): raise ValueError("Training cutoff overlaps evaluation")
        return self


class Suite(Strict):
    base: Run
    variants: Annotated[list[dict],Field(min_length=1,max_length=32)]


class Control(Strict):
    action: Literal["PAUSE","RESUME","CANCEL"]
