"""Guided, market-data-only connection setup. Secret values never appear in responses."""
from datetime import datetime,timedelta,timezone
import math
import os
import re
import threading
import time
from typing import Annotated,Literal
from fastapi import APIRouter,Request
from pydantic import BaseModel,ConfigDict,Field,SecretStr,field_validator
from .engine import IST

class Credentials(BaseModel):
    model_config=ConfigDict(extra='forbid')
    api_key:Annotated[SecretStr,Field(min_length=8,max_length=256)]
    api_secret:Annotated[SecretStr,Field(min_length=8,max_length=256)]
    @field_validator('api_key','api_secret')
    @classmethod
    def valid(cls,value):
        if not re.fullmatch(r'[A-Za-z0-9_-]+',value.get_secret_value()):raise ValueError('Enter the key and secret exactly as provided by Kite')
        return value

class Check(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['quotes','history']

class ConnectionSetup:
    def __init__(self,kite,hub,engine,config):
        self.kite=kite;self.hub=hub;self.engine=engine;self.config=config
        self.lock=threading.RLock();self.binding=None;self.results={};self.last_attempt={};self.authentication_error=None
        self.ai_status=lambda:bool(os.getenv('ARTHA_AI_API_KEY') and os.getenv('ARTHA_AI_MODEL'))
        self.rotation_lock=threading.RLock();self.forward_exposure=lambda:False;self.pause_forward=lambda:None

    def reset(self):
        with self.lock:self.binding=None;self.results={};self.last_attempt={};self.authentication_error=None

    def _bind(self):
        # In-memory only. No key/token fingerprint or value is serialized.
        binding=(self.kite.key,self.kite.token)
        if binding!=self.binding:self.results={};self.last_attempt={};self.binding=binding

    def status(self):
        with self.lock:
            self._bind();session=self.kite.status();market=self.hub.status()
            ready=bool(self.results.get('quotes',{}).get('access_verified')) and session['authenticated']
            with self.engine.lock:
                paper_ready=self.engine.state['source']=='KITE' and not self.engine.state['feed_error'] and all(self.engine._fresh(symbol) for symbol in self.engine.state['instruments'])
            return dict(credentials=dict(api_key_set=bool(self.kite.key),api_secret_set=bool(self.kite.secret),saved_on_server=self.config.path.exists()),
                session=session,authentication_error=self.authentication_error,checks=dict(self.results),can_connect=ready,market=market,paper_source=self.engine.state['source'],paper_ready=paper_ready,
                live_orders_enabled=False,ai_configured=self.ai_status(),
                session_note='Kite sessions expire at 6 AM the next day and can be revoked earlier; sign in again when requested',
                coverage=[dict(name='Current NSE instruments',available='Public metadata; not price history'),
                    dict(name='Live stock and current futures/options quotes',available='Connect data subscription and actual feed permissions'),
                    dict(name='Historical candles',available='Available symbols and intervals; coverage must be checked'),
                    dict(name='Expired options / historical order-book depth',available='Not supplied by this connection'),
                    dict(name='Complete actions, historical margins and point-in-time listings',available='Additional data and validation required')])

    def save(self,body):
        key=body.api_key.get_secret_value();secret=body.api_secret.get_secret_value()
        with self.lock,self.rotation_lock,self.engine.lock:
            if self.forward_exposure():raise ValueError('Close forward paper positions and cancel pending forward orders before replacing market-data credentials')
            if self.engine.state['source']=='KITE' and (self.engine.state['positions'] or any(o['status'] in {'OPEN','PARTIAL'} for o in self.engine.state['orders'])):
                raise ValueError('Close paper positions and cancel pending paper orders before replacing the active feed credentials')
            try:self.config.save_kite(key,secret)
            except OSError:raise ValueError('Could not save private credentials; check server storage and file permissions') from None
            with self.kite.lock:
                self.kite.token='';self.kite.key=key;self.kite.secret=secret
            self.pause_forward()
            if self.engine.state['source']=='KITE':
                self.engine.set_paused(True)
                self.engine.market_error('Market-data credentials changed; sign in again before receiving quotes')
        self.reset()
        return dict(saved=True,message='Credentials saved privately on this server. Sign in with Zerodha next.',status=self.status())

    def check(self,kind):
        with self.lock:
            self._bind()
            if not self.kite.status()['authenticated']:raise ValueError('Sign in with Zerodha before checking data access')
            if time.monotonic()-self.last_attempt.get(kind,-100)<3:raise ValueError('A check just ran; wait a few seconds before retrying')
            self.last_attempt[kind]=time.monotonic()
            try:
                if kind=='quotes':
                    q=self.kite._quotes(['NSE:RELIANCE']).get('NSE:RELIANCE',{})
                    if not q.get('timestamp') or not math.isfinite(float(q.get('last_price',0))) or float(q.get('last_price',0))<=0:
                        raise ValueError('Missing quote sample')
                    at=datetime.fromisoformat(q['timestamp'])
                    if at.tzinfo is None:at=at.replace(tzinfo=IST)
                    age=(datetime.now(IST)-at).total_seconds()
                    if age < -2:raise ValueError('Future quote timestamp')
                    result=dict(status='PASSED',access_verified=True,checked_at=datetime.now(timezone.utc).isoformat(),symbol='NSE:RELIANCE',
                        exchange_time=at.isoformat(),fresh=age<=30,message='Equity quote access verified.' if age<=30 else 'Quote access verified. The sample is old; the market may be closed or the feed quiet.')
                else:
                    row=next((r for r in self.kite.master() if r['exchange']=='NSE' and r['tradingsymbol']=='RELIANCE' and r['instrument_type']=='EQ'),None)
                    if not row:raise ValueError('Sample instrument unavailable')
                    end=(datetime.now(IST)-timedelta(days=1)).replace(hour=15,minute=30,second=0,microsecond=0);start=end-timedelta(days=14)
                    payload=self.kite._request('GET','/instruments/historical/'+str(int(row['instrument_token']))+'/day',params=dict(
                        **{'from':start.strftime('%Y-%m-%d %H:%M:%S'),'to':end.strftime('%Y-%m-%d %H:%M:%S')},continuous=0,oi=0))
                    candles=payload.get('candles',[])
                    if not candles:raise ValueError('No historical observations returned')
                    for candle in candles:
                        if len(candle)<6 or datetime.fromisoformat(candle[0]).tzinfo is None or any(not math.isfinite(float(v)) for v in candle[1:6]):raise ValueError('Invalid history sample')
                    result=dict(status='PASSED',access_verified=True,checked_at=datetime.now(timezone.utc).isoformat(),symbol='NSE:RELIANCE',
                        candles=len(candles),first=candles[0][0],last=candles[-1][0],message='Daily candle access verified on a small equity sample. This does not establish complete historical coverage.')
            except Exception:
                self._bind()
                result=dict(status='FAILED',access_verified=False,checked_at=datetime.now(timezone.utc).isoformat(),
                    message='Could not verify this data access. Check your Connect subscription, session and network, then retry. No prices or history were invented.')
            self.results[kind]=result
            return result

    def connect(self):
        if not self.status()['can_connect']:raise ValueError('Verify quote access for this session before connecting the market feed')
        self.hub.enable(True)
        return dict(started=True,message='Market-data connection requested. Watch the quote and freshness counts below.',status=self.status())


def router(setup,oauth_states):
    api=APIRouter(prefix='/api/onboarding',tags=['Connection setup'])
    @api.get('/status')
    def status(request:Request):return dict(setup.status(),callback_url=str(request.base_url).rstrip('/')+'/api/kite/callback')
    @api.post('/kite/credentials')
    def credentials(body:Credentials):
        result=setup.save(body);oauth_states.clear();return result
    @api.post('/kite/check')
    def check(body:Check):return setup.check(body.kind)
    @api.post('/kite/connect')
    def connect():return setup.connect()
    return api
