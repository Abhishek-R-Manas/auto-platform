"""Persistent, display-only broker candles. Cached data never supplies execution liquidity."""
from datetime import datetime,timedelta
import json,math,sqlite3,threading
from fastapi import APIRouter
from pydantic import Field
from typing import Annotated,Literal
from .engine import IST
from .research.schemas import Strict

class ChartRequest(Strict):
    symbol:Annotated[str,Field(pattern=r'^(?:NSE|NFO):[A-Z0-9 &_.\-]{1,70}$')]='NSE:RELIANCE'
    interval:Literal['day','5minute','15minute','60minute']='day'
    days:Annotated[int,Field(ge=1,le=365)]=90

class MarketCharts:
    def __init__(self,root,kite,clock=None):
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.kite=kite;self.clock=clock or (lambda:datetime.now(IST));self.lock=threading.RLock()
        self.db=sqlite3.connect(root/'charts.sqlite3',check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS charts(symbol TEXT,interval TEXT,document TEXT,PRIMARY KEY(symbol,interval))');self.db.commit()

    def get(self,symbol='NSE:RELIANCE',interval='day'):
        with self.lock:
            row=self.db.execute('SELECT document FROM charts WHERE symbol=? AND interval=?',(symbol,interval)).fetchone()
        return dict(symbol=symbol,interval=interval,authenticated=self.kite.status()['authenticated'],cached=bool(row),data=json.loads(row[0]) if row else None,
            message='Saved historical prices; never executable liquidity.' if row else 'Sign in with Zerodha, then refresh to save genuine prices and historical candles.')

    def refresh(self,body):
        # The lock bounds concurrent history requests and serializes cache replacement.
        with self.lock:
            if not self.kite.status()['authenticated']:raise ValueError('Sign in with Zerodha to retrieve prices. Previously saved charts remain available.')
            previous=self.get(body.symbol,body.interval)['data'];now=self.clock()
            if previous and (now-datetime.fromisoformat(previous['fetched_at'])).total_seconds()<60:return self.get(body.symbol,body.interval)
            row=next((r for r in self.kite.master() if r['exchange']+':'+r['tradingsymbol']==body.symbol and r['instrument_type'] in {'EQ','INDEX','FUT','CE','PE'}),None)
            if row is None:raise ValueError('Select a current NSE or NFO instrument from the master')
            days=min(body.days,60) if body.interval!='day' else body.days
            params={'from':(now-timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S'),'to':now.strftime('%Y-%m-%d %H:%M:%S'),'continuous':0,'oi':0}
            try:raw=self.kite._request('GET',f"/instruments/historical/{int(row['instrument_token'])}/{body.interval}",params=params)
            except Exception:raise ValueError('Could not fetch Kite candles; check your session and data subscription. Saved charts are retained.') from None
            candles=[];seen=set();minutes={'day':1440,'5minute':5,'15minute':15,'60minute':60}[body.interval]
            for c in raw.get('candles',[]):
                if len(c)<6:raise ValueError('Incomplete broker candle; cache was retained')
                stamp=datetime.fromisoformat(c[0]);values=[float(v) for v in c[1:6]]
                if stamp.tzinfo is None or any(not math.isfinite(v) for v in values) or min(values[:4])<=0 or values[4]<0 or values[4]!=int(values[4]):raise ValueError('Invalid broker candle; cache was retained')
                o,h,l,close,volume=values
                if h<max(o,l,close) or l>min(o,h,close) or stamp.isoformat() in seen:raise ValueError('Invalid or duplicate candle; cache was retained')
                seen.add(stamp.isoformat())
                completed=stamp.replace(hour=15,minute=30,second=0,microsecond=0) if body.interval=='day' else stamp+timedelta(minutes=minutes)
                if completed<=now:candles.append(dict(time=stamp.isoformat(),available_at=completed.isoformat(),open=o,high=h,low=l,close=close,volume=int(volume)))
            candles.sort(key=lambda c:c['time'])
            if not candles:raise ValueError('No completed historical candles returned; saved chart retained')
            data=dict(symbol=body.symbol,interval=body.interval,source='KITE_HISTORICAL',fetched_at=now.isoformat(),instrument_token=int(row['instrument_token']),master_hash=self.kite.master_hash,
                candles=candles[-10000:],latest_completed_close=candles[-1]['close'],close_time=candles[-1]['available_at'],executable=False)
            with self.db:self.db.execute('INSERT OR REPLACE INTO charts VALUES(?,?,?)',(body.symbol,body.interval,json.dumps(data,allow_nan=False)))
            return self.get(body.symbol,body.interval)

    def close(self):
        with self.lock:self.db.close()

def router(service):
    api=APIRouter(prefix='/api/market/charts',tags=['Market charts'])
    @api.get('')
    def chart(symbol:Annotated[str,Field(pattern=r'^(?:NSE|NFO):[A-Z0-9 &_.\-]{1,70}$')]='NSE:RELIANCE',interval:Literal['day','5minute','15minute','60minute']='day'):return service.get(symbol,interval)
    @api.post('/refresh')
    def refresh(body:ChartRequest):return service.refresh(body)
    return api
