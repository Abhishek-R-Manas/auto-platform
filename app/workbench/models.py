from datetime import datetime
from typing import Annotated,Literal
from pydantic import Field,StrictInt,model_validator
from ..research.schemas import Strict,Parameters

Key=Annotated[str,Field(pattern=r'^(?:NSE|NFO):[A-Z0-9 &_.\-]{1,70}$')]
Positive=Annotated[float,Field(gt=0,le=100000000)]
class Watchlist(Strict):
    name:Annotated[str,Field(min_length=1,max_length=40)]='My watchlist'
    keys:Annotated[list[Key],Field(max_length=50)]=[]
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.keys))!=len(self.keys):raise ValueError('A watchlist cannot contain duplicate instruments')
        return self
class NewWatchlist(Watchlist):
    pass
class QuoteConnection(Strict):
    selected:Key|None=None
class ChainRequest(Strict):
    underlying:Annotated[str,Field(pattern=r'^[A-Z0-9 &_.\-]{1,40}$')]='NIFTY'
    expiry:Annotated[str,Field(pattern=r'^\d{4}-\d{2}-\d{2}$')]|None=None
    width:Annotated[StrictInt,Field(ge=3,le=30)]=15
class BasketLeg(Strict):
    enabled:bool=True
    hedge:bool=False
    key:Key|None=None
    kind:Literal['CE','PE','EQ','FUT']='CE'
    side:Literal['BUY','SELL']='BUY'
    strike:Annotated[float,Field(ge=0,le=100000000)]=0
    expiry:datetime|None=None
    lots:Annotated[StrictInt,Field(ge=1,le=1000)]=1
    lot_size:Annotated[StrictInt,Field(ge=1,le=100000)]=1
    premium:Annotated[float,Field(ge=0,le=100000000)]
    iv_pct:Annotated[float,Field(gt=0,le=500)]|None=None
    price_source:Literal['MANUAL','MODEL','KITE_BID','KITE_ASK','KITE_LAST']='MANUAL'
    @model_validator(mode='after')
    def terms(self):
        if self.kind in {'CE','PE'} and (not self.strike or self.expiry is None):raise ValueError('Options require a positive strike and an expiry')
        if self.kind=='FUT' and self.expiry is None:raise ValueError('Futures require an explicit expiry')
        if self.expiry is not None and not 2000<=self.expiry.year<=2100:raise ValueError('Expiry is outside the supported bounded analysis horizon')
        if self.expiry is not None and self.expiry.tzinfo is None:raise ValueError('Use a timezone-aware expiry')
        return self
class Rules(Strict):
    family:Literal['TREND','MEAN_REVERSION','BREAKOUT','MOMENTUM','VOLATILITY','RULES']='TREND'
    parameters:Parameters=Parameters()
    stop_pct:Annotated[float,Field(gt=0,le=100)]=2
    target_multiple:Annotated[float,Field(gt=0,le=100)]=2
    allocation_pct:Annotated[float,Field(gt=0,le=100)]=25
    allow_overnight:bool=True
class Scenario(Strict):
    target_move_pct:Annotated[float,Field(ge=-90,le=200)]=0
    days_forward:Annotated[float,Field(ge=0,le=1000)]=0
    iv_shift_pp:Annotated[float,Field(ge=-100,le=200)]=0
    range_pct:Annotated[float,Field(ge=.5,le=100)]=5
class Basket(Strict):
    name:Annotated[str,Field(min_length=1,max_length=120)]='Untitled strategy'
    kind:Literal['OPTIONS','RULES']='OPTIONS'
    underlying:Annotated[str,Field(min_length=1,max_length=80)]='NIFTY'
    spot:Positive
    volatility_pct:Annotated[float,Field(gt=0,le=500)]=20
    interest_rate:Annotated[float,Field(ge=-.1,le=1)]=.065
    dividend_yield:Annotated[float,Field(ge=0,le=1)]=0
    cost_estimate:Annotated[float,Field(ge=0,le=100000000)]=0
    manual_pnl:Annotated[float,Field(ge=-1000000000,le=1000000000)]=0
    manual_pnl_enabled:bool=True
    legs:Annotated[list[BasketLeg],Field(min_length=1,max_length=20)]
    rules:Rules=Rules()
    notes:Annotated[str,Field(max_length=4000)]=''
    source:Literal['MANUAL','KITE','HYPOTHETICAL']='MANUAL'
    scenario:Scenario=Scenario()
class Analysis(Strict):
    basket:Basket
    target_move_pct:Annotated[float,Field(ge=-90,le=200)]|None=None
    days_forward:Annotated[float,Field(ge=0,le=1000)]|None=None
    iv_shift_pp:Annotated[float,Field(ge=-100,le=200)]|None=None
    range_pct:Annotated[float,Field(ge=.5,le=100)]|None=None
    table_step_pct:Annotated[float,Field(ge=.1,le=20)]=1
    table_center_move_pct:Annotated[float,Field(ge=-90,le=200)]=0
    @model_validator(mode='after')
    def saved_scenario(self):
        for name in ('target_move_pct','days_forward','iv_shift_pp','range_pct'):
            if getattr(self,name) is None:setattr(self,name,getattr(self.basket.scenario,name))
        return self
class SaveDraft(Strict):
    request_id:Annotated[str,Field(min_length=8,max_length=100)]
    basket:Basket
    parent_id:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]|None=None
class Backtest(Strict):
    allow_uncovered_options:bool=False
    draft_id:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
    dataset_id:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
    mapping:Annotated[dict[str,str],Field(max_length=21)]
    capital:Annotated[float,Field(ge=100,le=1000000000)]=1000000

class PaperBasket(Strict):
    draft_id:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
    capital:Annotated[float,Field(ge=100,le=1000000000)]
    reviewer:Annotated[str,Field(min_length=2,max_length=100)]
    review_note:Annotated[str,Field(min_length=20,max_length=3000)]
    data_rights_confirmed:bool=False
    assumptions_reviewed:bool=False
    allow_uncovered_options:bool=False

class TemplateApply(Strict):
    source:Literal['HYPOTHETICAL','KITE']='HYPOTHETICAL'
    template_id:Annotated[str,Field(min_length=1,max_length=60)]
    underlying:Annotated[str,Field(min_length=1,max_length=40)]='NIFTY'
    spot:Positive
    strike_step:Positive=50
    expiry:datetime
    next_expiry:datetime|None=None
    lot_size:Annotated[StrictInt,Field(ge=1,le=100000)]=1
    volatility_pct:Annotated[float,Field(gt=0,le=500)]=20
    @model_validator(mode='after')
    def aware(self):
        if self.expiry.tzinfo is None or (self.next_expiry is not None and self.next_expiry.tzinfo is None):raise ValueError('Use timezone-aware expiries')
        if self.next_expiry is not None and self.next_expiry<=self.expiry:raise ValueError('The later expiry must follow the first expiry')
        return self

class Screener(Strict):
    template:TemplateApply
    category:Literal['All','Bullish','Bearish','Neutral','Volatility','Calendar','Hedged']='All'
    target_move_pct:Annotated[float,Field(ge=-90,le=200)]=0
    days_forward:Annotated[float,Field(ge=0,le=1000)]=0
    iv_shift_pp:Annotated[float,Field(ge=-100,le=200)]=0
    max_loss:Annotated[float,Field(ge=0,le=100000000)]=0
    cost_estimate:Annotated[float,Field(ge=0,le=100000000)]=0

class StudioBasket(Strict):
    basket:Basket
class StrikeAdjust(StudioBasket):
    action:Literal['SHIFT','WIDTH','HEDGE','EXPIRY']
    steps:Annotated[StrictInt,Field(ge=-20,le=20)]=1
    strike_step:Positive=50
    expiry:datetime|None=None
class MarginQuote(StudioBasket):
    product:Literal['NRML','MIS']='NRML'
    consider_positions:bool=False
class PositionImport(Strict):
    snapshot_hash:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
    selected:Annotated[list[Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]],Field(min_length=1,max_length=20)]
    spot:Positive
class ChainSeries(Strict):
    underlying:Annotated[str,Field(pattern=r'^[A-Z0-9 &_.\-]{1,40}$')]
    expiry:Annotated[str,Field(pattern=r'^\d{4}-\d{2}-\d{2}$')]
    strikes:Annotated[list[Positive],Field(min_length=1,max_length=6)]
