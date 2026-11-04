from datetime import datetime
from typing import Annotated,Literal
from urllib.parse import urlsplit,parse_qsl
from pydantic import Field,FiniteFloat,field_validator,model_validator
from ..research.schemas import Strict,aware

class ImportReport(Strict):
    kind: Literal['FII_CSV','MACRO_CSV']
    source: Literal['NSE','MOSPI','RBI']
    scope: Literal['COMBINED_CASH','NSE_CASH','INDIA']
    source_url: Annotated[str,Field(max_length=1000)]
    published_at: datetime|None=None
    content: Annotated[str,Field(min_length=10,max_length=1000000)]
    rights_confirmed: bool=False
    reviewer: Annotated[str,Field(min_length=2,max_length=120)]
    @field_validator('published_at')
    @classmethod
    def timestamp(cls,value):return aware(value) if value else value
    @model_validator(mode='after')
    def source_contract(self):
        hosts={'NSE':{'www.nseindia.com','nseindia.com','nsearchives.nseindia.com'},'MOSPI':{'www.mospi.gov.in','mospi.gov.in','esankhyiki.mospi.gov.in','api.mospi.gov.in','mcp.mospi.gov.in'},'RBI':{'www.rbi.org.in','rbi.org.in','data.rbi.org.in'}}
        url=urlsplit(self.source_url)
        if url.scheme!='https' or url.hostname not in hosts[self.source] or url.username or url.password or url.port not in {None,443}:raise ValueError('Use an official HTTPS publication URL for this source')
        keys={key.casefold().replace('_','').replace('-','') for key,_ in parse_qsl(url.query)}
        if any(any(word in key for word in ('token','secret','password','apikey','authorization')) for key in keys):raise ValueError('Use a public publication URL without credential query parameters')
        if self.kind=='FII_CSV' and (self.source!='NSE' or self.scope=='INDIA'):raise ValueError('FII/DII reports require NSE cash scope and a publication timestamp')
        if self.kind=='MACRO_CSV' and (self.source=='NSE' or self.scope!='INDIA'):raise ValueError('Macro reports require RBI/MoSPI India scope')
        return self

class FeedPolicy(Strict):
    macro_enabled: bool=False
    kite_enabled: bool=False
    underlyings: Annotated[list[Literal['NIFTY','BANKNIFTY']],Field(min_length=1,max_length=2)]=['NIFTY']
    expiries: Annotated[int,Field(ge=1,le=3)]=2
    strikes_each_side: Annotated[int,Field(ge=2,le=10)]=5
    quote_interval_seconds: Annotated[int,Field(ge=5,le=3600)]=10
    macro_interval_seconds: Annotated[int,Field(ge=900,le=86400)]=3600
    max_quote_age_seconds: Annotated[int,Field(ge=1,le=30)]=30
    max_spread_bps: Annotated[FiniteFloat,Field(gt=0,le=5000)]=500
    max_timestamp_skew_seconds: Annotated[int,Field(ge=1,le=30)]=5
    interest_rate: Annotated[FiniteFloat,Field(ge=-.1,le=1)]=.065
    dividend_yield: Annotated[FiniteFloat,Field(ge=0,le=1)]=0
    fii_max_age_hours: Annotated[int,Field(ge=24,le=240)]=96
    @field_validator('underlyings')
    @classmethod
    def unique(cls,value):
        if len(set(value))!=len(value):raise ValueError('Duplicate underlyings')
        return value

class Refresh(Strict):
    source: Literal['RBI','MOSPI','KITE']
    request_id: Annotated[str,Field(min_length=1,max_length=100)]

class Enrich(Strict):
    dataset_id: Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
    features: Annotated[list[str],Field(min_length=1,max_length=20)]
