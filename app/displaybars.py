"""Bounded display-only quote bars. Never a historical or executable data source."""
from collections import deque
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import copy,math,threading
IST=ZoneInfo('Asia/Kolkata')

class DisplayBars:
    def __init__(self):
        self.lock=threading.RLock();self.tokens=set();self.bars={};self.last={}
    def select(self,tokens):
        with self.lock:
            self.tokens=set(tokens);self.bars={k:v for k,v in self.bars.items() if k[0] in self.tokens};self.last={k:v for k,v in self.last.items() if k in self.tokens}
    def observe(self,q):
        token=q['instrument_token']
        with self.lock:
            if token not in self.tokens:return
            if abs(datetime.fromisoformat(q['exchange_time']).timestamp()-q['received_at'])>30:return
            at=datetime.fromisoformat(q.get('last_trade_time') or q['exchange_time']).astimezone(IST)
            exchange=datetime.fromisoformat(q['exchange_time']).astimezone(IST)
            if at.date()!=exchange.date() or at.weekday()>4:return
            start=at.replace(hour=9,minute=15,second=0,microsecond=0);end=at.replace(hour=15,minute=30,second=0,microsecond=0)
            if not start<=at<end:return
            price=q['last'];volume=q.get('cumulative_volume');previous=self.last.get(token)
            if not math.isfinite(price) or price<=0 or previous and at<previous[0]:return
            if previous and (at,price,volume)==previous:return
            delta=max(0,volume-previous[2]) if type(volume) is int and previous and type(previous[2]) is int and previous[0].date()==at.date() else 0
            self.last[token]=(at,price,volume)
            for interval,minutes in [('day',None),('5minute',5),('15minute',15),('60minute',60)]:
                bucket=at.replace(hour=0,minute=0,second=0,microsecond=0) if minutes is None else start+timedelta(minutes=int((at-start).total_seconds()//(minutes*60))*minutes)
                rows=self.bars.setdefault((token,interval),deque(maxlen=12))
                new_bucket=not rows or rows[-1]['time']!=bucket.isoformat()
                if new_bucket:rows.append(dict(time=bucket.isoformat(),open=price,high=price,low=price,close=price,volume=0,partial=True,source='OBSERVED_KITE_QUOTES',first_observed_at=q['exchange_time']))
                row=rows[-1];row.update(high=max(row['high'],price),low=min(row['low'],price),close=price,volume=row['volume']+(0 if new_bucket else delta),last_observed_at=q['exchange_time'])
                if interval=='day':
                    o,h,l=q.get('open'),q.get('high'),q.get('low')
                    if all(type(v) in (int,float) and math.isfinite(v) and v>0 for v in (o,h,l)) and h>=max(o,price) and l<=min(o,price):
                        row.update(open=o,high=h,low=l,volume=volume or 0,source='KITE_DAY_OHLC')
    def get(self,token,interval):
        with self.lock:return copy.deepcopy(list(self.bars.get((token,interval),[])))
