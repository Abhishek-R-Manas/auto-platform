"""Durable manually confirmed NSE/CNC pilot. No strategy or AI dispatches orders."""
from contextlib import contextmanager
from datetime import datetime,timedelta
from decimal import Decimal
import copy,json,math,secrets,sqlite3,threading,uuid
from typing import Annotated,Literal
from pydantic import Field,StrictBool,StrictInt,model_validator,field_validator
from ..engine import IST
from ..portfolio import portfolio_view,broker_rows
from ..finance import paise,fees
from ..risk import budget_checks
from ..research.schemas import Strict
from ..research.catalog import canonical,digest,bounds
from .broker import KiteBroker

TERMINAL={'COMPLETE','CANCELLED','REJECTED'}
CONFIRM='I AUTHORIZE THIS REAL MONEY ORDER'

class Mandate(Strict):
    account_id:Annotated[str,Field(pattern=r'^[A-Z0-9]{3,20}$')]
    portfolio_id:Annotated[str,Field(pattern=r'^[a-f0-9]{32}$')]
    symbols:Annotated[list[Annotated[str,Field(pattern=r'^[A-Z0-9 &_.\-]{1,70}$')]],Field(min_length=1,max_length=10)]
    capital:Annotated[float,Field(gt=0,le=100000)]
    daily_loss:Annotated[float,Field(gt=0,le=10000)]
    max_trade_loss:Annotated[float,Field(gt=0,le=10000)]
    protective_orders_authorized:StrictBool=False
    max_order:Annotated[float,Field(gt=0,le=25000)]
    max_orders_per_day:Annotated[StrictInt,Field(ge=1,le=10)]=3
    expires_at:datetime
    static_ip_confirmed:StrictBool=False
    delivery_authorized:StrictBool=False
    reviewer:Annotated[str,Field(min_length=1,max_length=100)]
    @model_validator(mode='after')
    def valid(self):
        if self.expires_at.tzinfo is None:raise ValueError('Pilot expiry requires a timezone')
        if self.max_trade_loss>self.daily_loss or self.daily_loss>self.capital or self.max_order>self.capital:raise ValueError('Loss and order limits cannot exceed allocated capital')
        if len(set(self.symbols))!=len(self.symbols):raise ValueError('Duplicate pilot symbols')
        return self

class Intention(Strict):
    request_id:Annotated[str,Field(pattern=r'^[A-Za-z0-9_-]{8,80}$')]
    symbol:Annotated[str,Field(pattern=r'^[A-Z0-9 &_.\-]{1,70}$')]
    side:Literal['BUY','SELL']
    quantity:Annotated[StrictInt,Field(ge=1,le=10000)]
    limit_price:Annotated[float,Field(gt=0,le=1000000)]
    stop_loss:Annotated[float,Field(gt=0,le=1000000)]|None=None
    rationale:Annotated[str,Field(min_length=5,max_length=1000)]
    @field_validator('limit_price','stop_loss')
    @classmethod
    def exact_paise(cls,value):
        if value is not None and Decimal(str(value))*100!=(Decimal(str(value))*100).to_integral_value():raise ValueError('Order prices must use whole paise')
        return value
    @model_validator(mode='after')
    def stop(self):
        if self.side=='BUY' and (self.stop_loss is None or self.stop_loss>=self.limit_price):raise ValueError('A buy requires a protective stop below the limit')
        return self

class Submit(Strict):
    preview_id:Annotated[str,Field(pattern=r'^[a-f0-9]{32}$')]
    confirmation:Annotated[str,Field(max_length=80)]

class Arm(Strict):
    confirmation:Annotated[str,Field(max_length=80)]

class Pilot:
    def __init__(self,root,kite,forward,broker=None,clock=None):
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.kite=kite;self.forward=forward;self.broker=broker or KiteBroker(kite)
        self.clock=clock or (lambda:datetime.now(IST));self.lock=threading.RLock();self.session=None;self.last_tick=None;self.watch_required=False
        self.db=sqlite3.connect(root/'pilot.sqlite3',check_same_thread=False);self.db.row_factory=sqlite3.Row
        self.db.executescript("""PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS settings(name TEXT PRIMARY KEY,document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS intents(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS previews(id TEXT PRIMARY KEY,document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY,document TEXT NOT NULL);""")
        initial_audit=self.audit()
        with self.transaction():
            if not initial_audit["ok"]:self._set("integrity_halt",True)
            if initial_audit['ok'] and self._get('snapshot') is not None:self._set('portfolio_snapshot',self._get('snapshot'))
            self._set('armed',False);self._set('snapshot',None)
            for item in self.orders():
                if initial_audit['ok'] and item['status'] in {'SENDING','CANCEL_SENDING'}:
                    item['status']='UNCERTAIN';self._save(item);self._event('RESTART_UNCERTAIN',item)
            self._event('RESTART_PAUSED',{'reason':'Reconcile and explicitly arm after every restart'})

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:yield
            except BaseException:self.db.rollback();raise
            else:self.db.commit()

    def _get(self,name,default=None):
        row=self.db.execute('SELECT document FROM settings WHERE name=?',(name,)).fetchone()
        return json.loads(row[0]) if row else default
    def _set(self,name,value):self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(name,canonical(value)))
    def _save(self,item):self.db.execute('INSERT OR REPLACE INTO intents VALUES(?,?,?)',(item['id'],item['request_id'],canonical(item)))
    def _event(self,kind,details):
        row=self.db.execute('SELECT document FROM events ORDER BY sequence DESC LIMIT 1').fetchone();last=json.loads(row[0]) if row else None
        item=dict(sequence=last['sequence']+1 if last else 1,time=self.clock().isoformat(),kind=kind,details=copy.deepcopy(details),previous_hash=last['hash'] if last else '0'*64)
        item['hash']=digest(item);self.db.execute('INSERT INTO events VALUES(?,?)',(item['sequence'],canonical(item)))
    def orders(self):
        with self.lock:return [json.loads(r[0]) for r in self.db.execute('SELECT document FROM intents ORDER BY rowid DESC')]
    def audit(self):
        with self.lock:
            if self._get('integrity_halt',False):return {'ok':False,'reason':'Pilot integrity failed before restart; restore reviewed evidence'}
            previous='0'*64;sequence=0;latest={};mandate=None;snapshot_hash=None
            try:
                for row in self.db.execute('SELECT sequence,document FROM events ORDER BY sequence'):
                    item=json.loads(row[1]);expected=item.pop('hash');sequence+=1
                    if item['sequence']!=sequence or row[0]!=sequence or item['previous_hash']!=previous or digest(item)!=expected:return {'ok':False,'reason':'Pilot event integrity failed'}
                    previous=expected
                    if item['kind']=='MANDATE':mandate=item['details']
                    if item['kind']=='RECONCILIATION':snapshot_hash=item['details']['hash']
                    if item['kind'] in {'SEND_RESERVED','BROKER_ACK','SEND_UNCERTAIN','RECONCILED_ORDER','CANCEL_RESERVED','CANCEL_ACK','CANCEL_UNCERTAIN','RESTART_UNCERTAIN'}:latest[item['details']['id']]=item['details']
                current={o['id']:o for o in self.orders()}
                if self._get('mandate')!=mandate:return {'ok':False,'reason':'Pilot mandate/event integrity failed'}
                snapshot=self._get('snapshot')
                if snapshot is not None and digest(snapshot)!=snapshot_hash:return {'ok':False,'reason':'Pilot snapshot/event integrity failed'}
                display=self._get('portfolio_snapshot')
                if display is not None and digest(display)!=snapshot_hash:return {'ok':False,'reason':'Portfolio display snapshot/event integrity failed'}
                if current!=latest:return {'ok':False,'reason':'Pilot order/event integrity failed'}
            except (ValueError,TypeError,KeyError):return {'ok':False,'reason':'Malformed pilot evidence'}
            return dict(ok=True,checked=sequence,head=previous)

    def has_exposure(self):
        with self.lock:
            orders=self.orders();symbols={o['order']['symbol'] for o in orders}
            return bool(self._get('armed') or any(o['status'] not in TERMINAL for o in orders) or any(sum((1 if o['order']['side']=='BUY' else -1)*o.get('filled_quantity',0) for o in orders if o['order']['symbol']==symbol)>0 for symbol in symbols))

    def configure(self,body):
        data=body.model_dump(mode='json');now=self.clock()
        with self.transaction():
            if not self.audit()['ok']:raise ValueError('Pilot audit failed')
            if self.has_exposure() or self.orders():raise ValueError('Existing pilot orders require review before replacing the mandate; archive/reallocation is not automatic')
            if not now<body.expires_at<=now+timedelta(days=7):raise ValueError('Pilot authority must expire within seven days')
            report=self.forward.report(body.portfolio_id)
            if not report['ready_for_phase5_review']:raise ValueError('Forward qualification and current guardian approval must pass before configuring a real-money pilot')
            portfolio=self.forward.portfolio(body.portfolio_id);doc=portfolio['document']
            equity={b['key'][4:] for b in doc['bindings'].values() if b['kind']=='EQ' and b['key'].startswith('NSE:')}
            if not set(body.symbols)<=equity:raise ValueError('Pilot symbols must be qualified NSE equity bindings')
            if body.capital>doc['config']['capital']:raise ValueError('Pilot capital cannot exceed the reviewed forward allocation')
            data.update(id=uuid.uuid4().hex,created_at=now.isoformat(),forward_evidence_hash=report['evidence_hash'],mode='MANUALLY_CONFIRMED_NSE_CNC',build=doc['build'])
            data['hash']=digest(data);self._set('mandate',data);self._set('armed',False);self._event('MANDATE',data)
            return self.status()

    def _mandate(self,allow_expired=False):
        item=self._get('mandate')
        if not item:raise ValueError('Configure a qualified pilot mandate first')
        core=dict(item);expected=core.pop('hash',None)
        if digest(core)!=expected:raise ValueError('Pilot mandate integrity failed')
        if not allow_expired and datetime.fromisoformat(item['expires_at'])<=self.clock():raise ValueError('Pilot authority expired')
        return item

    def _source(self,m):
        report=self.forward.report(m['portfolio_id'])
        if not report['ready_for_phase5_review'] or report['evidence_hash']!=m['forward_evidence_hash']:raise ValueError('Forward qualification or approved evidence changed; pause and obtain a new review')
        return self.forward.portfolio(m['portfolio_id'])['document']

    def reconcile(self):
        with self.lock:
            try:
                snap=self.broker.snapshot()
                if not isinstance(snap.get('account_id'),str) or not snap['account_id']:raise ValueError('Missing broker account identity')
                for key in ('orders','trades','holdings'):
                    if not isinstance(snap.get(key),list):raise ValueError('Incomplete broker snapshot')
                if not isinstance(snap.get('positions',{}).get('net'),list):raise ValueError('Incomplete broker positions')
                snap['observed_at']=self.clock().isoformat();m=self._get('mandate')
                if m and snap['account_id']!=m['account_id']:raise ValueError('Broker account differs from pilot mandate')
                by_id={str(o['order_id']):o for o in snap['orders']};by_tag={}
                for o in snap['orders']:
                    if o.get('tag'):by_tag.setdefault(o['tag'],[]).append(o)
                updates=[]
                for item in self.orders():
                    matches=by_tag.get(item['tag'],[])
                    if len(matches)>1:raise ValueError('Multiple broker orders share a pilot tag; manual reconciliation required')
                    observed=by_id.get(item.get('broker_order_id')) or (matches[0] if matches else None)
                    if not observed:
                        if item['status'] not in TERMINAL:raise ValueError('A sent order is absent from today’s broker order book; verify in Kite before continuing')
                        continue
                    order=item['order']
                    if any(observed.get(k)!=v for k,v in dict(tradingsymbol=order['symbol'],exchange='NSE',product='CNC',transaction_type=order['side'],order_type=order.get('order_type','LIMIT'),quantity=order['quantity'],tag=item['tag']).items()) or paise(observed.get('price',0))!=paise(order['limit_price']):raise ValueError('Broker order differs from authorized intention')
                    if order.get('order_type')=='SL' and paise(observed.get('trigger_price',0))!=paise(order['stop_loss']):raise ValueError('Broker protective trigger differs from authorized intention')
                    filled=observed.get('filled_quantity');status=observed.get('status');average=float(observed.get('average_price',0))
                    if type(filled) is not int or not 0<=filled<=order['quantity'] or filled<item.get('filled_quantity',0) or not math.isfinite(average) or average<0 or (filled and not average) or not isinstance(status,str):raise ValueError('Invalid broker fill evidence')
                    if filled and ((order['side']=='BUY' and average>order['limit_price']+.00001) or (order['side']=='SELL' and average<order['limit_price']-.00001)):raise ValueError('Broker fill violates the authorized limit')
                    if status=='COMPLETE' and filled!=order['quantity']:raise ValueError('Complete order has incomplete fills')
                    trades=[t for t in snap['trades'] if str(t.get('order_id'))==str(observed['order_id'])]
                    if sum(t.get('quantity',0) for t in trades)!=filled:raise ValueError('Broker trade and order quantities do not reconcile')
                    if filled and abs(sum(Decimal(str(t['average_price']))*t['quantity'] for t in trades)/filled-Decimal(str(average)))>Decimal('0.02'):raise ValueError('Broker trade and order values do not reconcile')
                    changed=dict(item,status=status,broker_order_id=str(observed['order_id']),filled_quantity=filled,average_price=average,trades=trades)
                    updates.append(changed)
                # Validate finite balances before saving any broker evidence.
                self._funds(snap);self._loss(snap)
                with self.transaction():
                    for item in updates:
                        self._save(item);self._event('RECONCILED_ORDER',item)
                        if item.get('origin')=='PROTECTIVE' and item['status']=='REJECTED':self._set('protection_error','Broker rejected a protective order; review in Kite and explicitly restore protection');self._set('armed',False)
                    self._set('snapshot',snap);self._set('portfolio_snapshot',snap);self._event('RECONCILIATION',{'hash':digest(snap),'account_id':snap['account_id']})
                    self._set('reconcile_error',None)
                    if m and self._loss(snap)>=paise(m['daily_loss']):self._set('armed',False);self._set('loss_halt_day',str(self.clock().date()));self._event('LOSS_HALT',{'loss_paise':self._loss(snap)})
                self.session=(self.kite.key,self.kite.token)
                self._protect()
                if m and self._get('loss_halt_day')==str(self.clock().date()):self._cancel_entries()
                return self.status()
            except Exception:
                with self.transaction():self._set('armed',False);self._set('reconcile_error','Broker reconciliation failed. Inspect broker orders, trades, positions and account identity.');self._event('RECONCILIATION_FAILED',{})
                raise ValueError('Broker reconciliation failed; real entries are paused. Verify in Kite before retrying reconciliation.') from None

    @staticmethod
    def _funds(snap):
        funds=snap['funds'];available=funds['available']
        net=float(funds['net']);cash=float(available['cash']);live=float(available['live_balance']);collateral=float(available['collateral']);adhoc=float(available['adhoc_margin'])
        if not all(math.isfinite(v) for v in (net,cash,live,collateral,adhoc)) or min(collateral,adhoc)<0:raise ValueError('Invalid broker funds')
        # Delivery purchases cannot spend collateral or discretionary broker margin.
        return paise(min(net,cash,live,net-collateral-adhoc))

    @staticmethod
    def _loss(snap):
        # Gross negative account P&L is conservative: gains never offset losses.
        values=[float(p['pnl']) for p in snap['positions']['net']]
        values.extend(float(h['day_change'])*int(h['quantity']) for h in snap['holdings'])
        if any(not math.isfinite(v) for v in values):raise ValueError('Invalid broker loss evidence')
        return paise(sum(max(0,-v) for v in values))

    def _snapshot(self,m):
        snap=self._get('snapshot')
        if not snap or (self.clock()-datetime.fromisoformat(snap['observed_at'])).total_seconds()>15 or self.session!=(self.kite.key,self.kite.token):raise ValueError('Reconcile the current broker session before continuing')
        if snap['account_id']!=m['account_id']:raise ValueError('Broker account differs from pilot mandate')
        event=self.db.execute("SELECT document FROM events WHERE json_extract(document,'$.kind')='RECONCILIATION' ORDER BY sequence DESC LIMIT 1").fetchone()
        if not event or json.loads(event[0])['details']['hash']!=digest(snap):raise ValueError('Broker snapshot integrity failed')
        return snap

    def arm(self,body):
        with self.transaction():
            m=self._mandate()
            if body.confirmation!='ARM REAL MONEY '+m['account_id']:raise ValueError('Type ARM REAL MONEY followed by the exact broker account ID')
            if not self.broker.enabled:raise ValueError('Live transport remains disabled by deployment policy; review the implementation and limits before enablement')
            if not m['protective_orders_authorized']:raise ValueError('Authorize broker-native protective orders before arming')
            if self._get('protection_error') or self._get('protection_suspensions',[]) or self.protection()['unprotected_units']:raise ValueError('Resolve missing or suspended broker protection before arming')
            if not m['static_ip_confirmed'] or not m['delivery_authorized']:raise ValueError('Confirm broker static-IP setup and delivery-sale authorization')
            self._source(m);snap=self._snapshot(m)
            if self._loss(snap)>=paise(m['daily_loss']) or self._get('loss_halt_day')==str(self.clock().date()):raise ValueError('Daily loss halt blocks new orders')
            if not self.audit()['ok']:raise ValueError('Pilot audit failed')
            self._set('armed',True);self.watch_required=True;self._event('ARMED',{'mandate_hash':m['hash']});return self.status()

    def pause(self,cancel_entries=False):
        with self.transaction():self._set('armed',False);self._event('PAUSED',{})
        if cancel_entries:self._cancel_entries()
        return self.status()

    def _checks(self,order):
        try:return self._evaluate_live(order)
        except (KeyError,TypeError,OverflowError,StopIteration,ZeroDivisionError):raise ValueError('Broker market or account evidence is incomplete; reconcile and verify current data') from None

    def _evaluate_live(self,order):
        exiting=order['side']=='SELL'
        m=self._mandate(allow_expired=exiting);snap=self._snapshot(m);doc=self.forward.portfolio(m['portfolio_id'])['document'] if exiting else self._source(m);now=self.clock()
        if not self.broker.enabled or (not exiting and not self._get('armed')):raise ValueError('Real-money pilot is paused or deployment transport is disabled')
        if not self.audit()['ok']:raise ValueError('Pilot audit failed')
        span=bounds(doc['manifest']['calendar'],now.date())
        if not span or not span[0]<=now<span[1]-timedelta(minutes=10):raise ValueError('Outside the reviewed pilot session or within its closing buffer')
        if now.date().isoformat()>doc['config']['calendar_valid_until']:raise ValueError('Reviewed exchange calendar expired')
        if order['symbol'] not in m['symbols']:raise ValueError('Instrument is outside the pilot mandate')
        if not exiting and (self._get('loss_halt_day')==str(now.date()) or self._loss(snap)>=paise(m['daily_loss'])):raise ValueError('Daily loss limit reached')
        if not {'NSE'}<=set(snap['exchanges']) or 'CNC' not in snap['products'] or not {'LIMIT','SL'}<=set(snap['order_types']):raise ValueError('Broker account lacks required trading permissions')
        own=self.orders();ids={o.get('broker_order_id') for o in own}
        if any(o.get('tradingsymbol') in m['symbols'] and o.get('status') not in TERMINAL and str(o.get('order_id')) not in ids for o in snap['orders']):raise ValueError('An external pending order overlaps pilot instruments')
        if any(o['status'] in {'UNCERTAIN','SENDING','CANCEL_SENDING','CANCEL_PENDING'} for o in own):raise ValueError('Uncertain broker action requires reconciliation; it cannot be retried')
        if not exiting and (self._get('protection_error') or self._get('protection_suspensions',[]) or self.protection()['unprotected_units']):raise ValueError('Resolve missing broker protection before new exposure')
        if not exiting and sum(o['order']['side']=='BUY' and o['created_at'][:10]==now.isoformat()[:10] for o in own)>=m['max_orders_per_day']:raise ValueError('Daily pilot order count exhausted')
        row=next((r for r in self.kite.master() if r['exchange']=='NSE' and r['tradingsymbol']==order['symbol'] and r['instrument_type']=='EQ'),None)
        if not row or int(row['lot_size'])!=1:raise ValueError('Pilot supports current one-unit NSE delivery-equity contracts only')
        value=paise(order['limit_price'])*order['quantity']
        if value>paise(m['max_order']):raise ValueError('Per-order capital limit exceeded')
        if paise(order['limit_price'])%paise(float(row['tick_size'])):raise ValueError('Limit price must align with the current tick size')
        q=self.kite._quotes(['NSE:'+order['symbol']]).get('NSE:'+order['symbol'],{})
        stamp=datetime.fromisoformat(q['timestamp']);stamp=stamp if stamp.tzinfo else stamp.replace(tzinfo=IST)
        age=(self.clock()-stamp).total_seconds()
        self._snapshot(m)
        if not -2<=age<=30 or int(q.get('instrument_token',0))!=int(row['instrument_token']):raise ValueError('Fresh current-token quote is required for real orders')
        depth=q.get('depth',{});bid=depth.get('buy',[{}])[0];ask=depth.get('sell',[{}])[0]
        b=float(bid.get('price',0));a=float(ask.get('price',0));price=order['limit_price']
        if not all(math.isfinite(v) and v>0 for v in (b,a)) or b>a or min(int(bid.get('quantity',0)),int(ask.get('quantity',0)))<order['quantity']:raise ValueError('Insufficient valid executable depth')
        if (a-b)/b>.01 or abs(price-(a if order['side']=='BUY' else b))/(a if order['side']=='BUY' else b)>.01:raise ValueError('Spread or limit-price deviation exceeds the 1% pilot ceiling')
        fee=paise(fees('EQ',order['side'],value/100,dp=order['side']=='SELL')['total'])
        if not exiting:
            stop=order['stop_loss']
            if stop>=b or paise(stop)%paise(float(row['tick_size'])):raise ValueError('Protective stop must be below the current bid and aligned with the tick size')
            planned=paise((price-stop)*order['quantity'])+fee+paise(fees('EQ','SELL',stop*order['quantity'],dp=True)['total'])
            reviewed=doc['config'].get('risk',{})
            trade_limit=min(m['max_trade_loss'],m['capital']*reviewed.get('trade_pct',.25)/100)
            existing=sum(o.get('planned_risk_paise',0) for o in own if o['order']['side']=='BUY' and (o['status'] not in TERMINAL or o.get('filled_quantity',0)))
            risk=dict(trade_pct=100*trade_limit/m['capital'],open_pct=min(100,reviewed.get('open_pct',100*m['daily_loss']/m['capital'])),margin_pct=100,overnight_stress_pct=100)
            checks=budget_checks(equity=paise(m['capital']),trade_risk=planned,open_risk=existing+planned,funding_needed=value+fee,funding_available=self._funds(snap),margin=0,stress=value,risk=risk,futures=False)
            if any(c['status']=='FAIL' for c in checks):raise ValueError('Pilot planned-loss or funding budget exceeded: '+next(c['reason'] for c in checks if c['status']=='FAIL'))
        else:planned=0;checks=[]
        if order['side']=='BUY':
            # Lifetime gross purchases plus pending commitments never recycle sale proceeds.
            committed=sum(paise(o['order']['limit_price'])*(o['filled_quantity'] if o['status'] in TERMINAL else o['order']['quantity'])+o.get('fee_reserve_paise',0) for o in own if o['order']['side']=='BUY')
            pending=sum(paise(o['order']['limit_price'])*(o['order']['quantity']-o.get('filled_quantity',0))+o.get('fee_reserve_paise',0) for o in own if o['order']['side']=='BUY' and o['status'] not in TERMINAL)
            if committed+value+fee>paise(m['capital']):raise ValueError('Pilot capital reservation exhausted')
            if value+fee+pending>self._funds(snap):raise ValueError('Insufficient actual broker funds after pilot reservations')
        else:
            bought=sum(o.get('filled_quantity',0) for o in own if o['order']['symbol']==order['symbol'] and o['order']['side']=='BUY')
            sold=sum(o.get('filled_quantity',0) if o['status'] in TERMINAL else o['order']['quantity'] for o in own if o['order']['symbol']==order['symbol'] and o['order']['side']=='SELL')
            holdings=sum(max(0,int(h['quantity'])-int(h.get('used_quantity',0))) for h in snap['holdings'] if h['exchange']=='NSE' and h['tradingsymbol']==order['symbol'])
            intraday=sum(max(0,int(p['quantity'])) for p in snap['positions']['net'] if p['exchange']=='NSE' and p['tradingsymbol']==order['symbol'] and p['product']=='CNC')
            if order['quantity']>min(bought-sold,holdings+intraday):raise ValueError('Sell quantity exceeds reconciled pilot-owned unreserved stock')
        return dict(quote_time=stamp.isoformat(),bid=b,ask=a,notional_paise=value,fee_reserve_paise=fee,planned_risk_paise=planned,risk_checks=checks,mandate_hash=m['hash'],snapshot_hash=digest(snap),master_hash=self.kite.master_hash)

    def preview(self,body):
        order=body.model_dump(mode='json')
        with self.lock:
            existing=self.db.execute('SELECT document FROM intents WHERE request_id=?',(order['request_id'],)).fetchone()
            if existing:raise ValueError('This request already has a durable order intention; inspect its status')
            evidence=self._checks(order);item=dict(id=uuid.uuid4().hex,order=order,evidence=evidence,created_at=self.clock().isoformat(),expires_at=(self.clock()+timedelta(seconds=20)).isoformat())
            with self.transaction():self.db.execute('INSERT INTO previews VALUES(?,?)',(item['id'],canonical(item)));self._event('PREVIEW',item)
            return dict(item,confirmation_required=CONFIRM,real_money=True)

    def submit(self,body):
        with self.lock:
            row=self.db.execute('SELECT document FROM previews WHERE id=?',(body.preview_id,)).fetchone()
            if not row:raise ValueError('Preview not found')
            preview=json.loads(row[0]);order=preview['order']
            existing=self.db.execute('SELECT document FROM intents WHERE request_id=?',(order['request_id'],)).fetchone()
            if existing:return json.loads(existing[0])
            if body.confirmation!=CONFIRM:raise ValueError('Explicit real-money order confirmation is required')
            if self.clock()>datetime.fromisoformat(preview['expires_at']):raise ValueError('Preview expired; inspect a new preview')
            evidence=self._checks(order)
            if evidence['mandate_hash']!=preview['evidence']['mandate_hash']:raise ValueError('Pilot mandate changed after preview')
            item=dict(id=uuid.uuid4().hex,request_id=order['request_id'],order=order,tag='artha'+secrets.token_hex(7),created_at=self.clock().isoformat(),status='SENDING',filled_quantity=0,average_price=0,fee_reserve_paise=evidence['fee_reserve_paise'],planned_risk_paise=evidence['planned_risk_paise'],evidence=evidence)
            # This reservation commits BEFORE the external side effect. Never retry an uncertain send.
            with self.transaction():self._save(item);self._event('SEND_RESERVED',item)
            try:
                result=self.broker.place(order,item['tag']);identifier=str(result.get('order_id',''))
                if not identifier.isdigit() or len(identifier)>30:raise ValueError('Invalid broker acknowledgement')
                item.update(status='ACKNOWLEDGED',broker_order_id=identifier)
                with self.transaction():self._save(item);self._event('BROKER_ACK',item);self._set('snapshot',None)
            except Exception:
                item['status']='UNCERTAIN'
                with self.transaction():self._save(item);self._event('SEND_UNCERTAIN',item);self._set('armed',False);self._set('snapshot',None)
            return item

    def cancel(self,identifier):
        with self.lock:
            item=next((o for o in self.orders() if o['id']==identifier),None)
            if not item or not item.get('broker_order_id'):raise ValueError('Only an acknowledged pilot-owned order can be cancelled')
            if item['status'] in TERMINAL:return item
            if item['status'] in {'UNCERTAIN','CANCEL_SENDING','CANCEL_PENDING'}:raise ValueError('Cancellation is uncertain or pending; reconcile without repeating it')
            m=self._mandate(allow_expired=True);self._snapshot(m)
            with self.transaction():
                if item.get('origin')=='PROTECTIVE':
                    suspended=self._get('protection_suspensions',[])
                    if item['order']['symbol'] not in suspended:suspended.append(item['order']['symbol'])
                    self._set('protection_suspensions',suspended);self._set('armed',False);self._event('PROTECTION_SUSPENDED',{'symbol':item['order']['symbol']})
                item['status']='CANCEL_SENDING';self._save(item);self._event('CANCEL_RESERVED',item)
            try:
                result=self.broker.cancel(item['broker_order_id'])
                if str(result.get('order_id'))!=item['broker_order_id']:raise ValueError('Cancellation acknowledgement mismatch')
                item['status']='CANCEL_PENDING'
            except Exception:item['status']='UNCERTAIN'
            item['cancel_requested_at']=self.clock().isoformat()
            with self.transaction():self._save(item);self._event('CANCEL_ACK' if item['status']=='CANCEL_PENDING' else 'CANCEL_UNCERTAIN',item);self._set('snapshot',None)
            return item

    def tick(self):
        self.last_tick=self.clock().isoformat()
        with self.lock:
            if not self._get('armed') and not any(o['status'] not in TERMINAL or (o['order']['side']=='BUY' and o.get('filled_quantity',0)) for o in self.orders()):return
            try:
                m=self._mandate(allow_expired=True)
                if self._get('armed'):
                    try:
                        self._mandate();self._source(m)
                    except ValueError:self.pause(cancel_entries=True)
                if not self.kite.status()['authenticated'] or self.session!=(self.kite.key,self.kite.token):raise ValueError('Session changed')
                snap=self._get('snapshot')
                if not snap or (self.clock()-datetime.fromisoformat(snap['observed_at'])).total_seconds()>=10:self.reconcile()
            except Exception:self.pause(cancel_entries=True)

    def _cancel_entries(self):
        # Mandate consent includes cancelling only this pilot's resting BUY remainders.
        with self.lock:
            m=self._get('mandate')
            if not m or not m.get('protective_orders_authorized') or not self.broker.enabled:return
            try:self._snapshot(m)
            except ValueError:return
            changed=False
            for item in self.orders():
                if item['order']['side']!='BUY' or item['status'] in TERMINAL or item['status'] in {'UNCERTAIN','SENDING','CANCEL_SENDING','CANCEL_PENDING'} or not item.get('broker_order_id') or item.get('cancel_requested_at'):continue
                changed=True
                with self.transaction():item['status']='CANCEL_SENDING';self._save(item);self._event('CANCEL_RESERVED',item)
                try:
                    result=self.broker.cancel(item['broker_order_id'])
                    if str(result.get('order_id'))!=item['broker_order_id']:raise ValueError('Cancellation acknowledgement mismatch')
                    item['status']='CANCEL_PENDING'
                except Exception:item['status']='UNCERTAIN'
                item['cancel_requested_at']=self.clock().isoformat()
                with self.transaction():self._save(item);self._event('CANCEL_ACK' if item['status']=='CANCEL_PENDING' else 'CANCEL_UNCERTAIN',item)
            if changed:
                with self.transaction():self._set('snapshot',None)

    def protection(self):
        own=self.orders();symbols={o['order']['symbol'] for o in own};details=[]
        for symbol in symbols:
            buys=sum(o.get('filled_quantity',0) for o in own if o['order']['symbol']==symbol and o['order']['side']=='BUY')
            sells=sum(o.get('filled_quantity',0) for o in own if o['order']['symbol']==symbol and o['order']['side']=='SELL')
            covered=sum(o['order']['quantity']-o.get('filled_quantity',0) for o in own if o['order']['symbol']==symbol and o.get('origin')=='PROTECTIVE' and o['status'] not in TERMINAL and o['status'] not in {'UNCERTAIN','SENDING','CANCEL_SENDING','CANCEL_PENDING'} and o.get('broker_order_id'))
            details.append(dict(symbol=symbol,owned_units=max(0,buys-sells),protected_units=covered,unprotected_units=max(0,buys-sells-covered)))
        return dict(unprotected_units=sum(d['unprotected_units'] for d in details),symbols=details,error=self._get('protection_error'),suspended=self._get('protection_suspensions',[]))

    def restore_protection(self):
        with self.lock:
            m=self._mandate(allow_expired=True);self._snapshot(m)
            if any(o['status'] in {'UNCERTAIN','SENDING','CANCEL_SENDING','CANCEL_PENDING'} for o in self.orders()):raise ValueError('Resolve uncertain/cancelling orders before restoring protection')
            with self.transaction():self._set('protection_error',None);self._set('protection_suspensions',[]);self._event('PROTECTION_RESTORE_REQUESTED',{})
            self._protect();return self.status()

    def _protect(self):
        m=self._get('mandate')
        if not m or not m.get('protective_orders_authorized') or not self.broker.enabled or self._get('protection_error'):return
        if not self.audit()['ok']:return
        own=self.orders()
        if any(o['status'] in {'UNCERTAIN','SENDING','CANCEL_SENDING','CANCEL_PENDING'} for o in own):return
        suspended=self._get('protection_suspensions',[])
        # Each verified fill increment gets its own native stop; no modification races.
        for parent in own:
            if parent['order']['side']!='BUY' or not parent.get('filled_quantity') or parent['order']['symbol'] in suspended:continue
            symbol=parent['order']['symbol'];all_orders=self.orders()
            bought=sum(o.get('filled_quantity',0) for o in all_orders if o['order']['side']=='BUY' and o['order']['symbol']==symbol)
            reserved_sells=sum(o.get('filled_quantity',0) if o['status'] in TERMINAL else o['order']['quantity'] for o in all_orders if o['order']['side']=='SELL' and o['order']['symbol']==symbol)
            children=[o for o in all_orders if o.get('parent_id')==parent['id']]
            accounted=sum(o.get('filled_quantity',0) if o['status'] in TERMINAL else o['order']['quantity'] for o in children)
            quantity=min(parent['filled_quantity']-accounted,bought-reserved_sells)
            if quantity<=0:continue
            try:
                self._snapshot(m)
                row=next(r for r in self.kite.master() if r['exchange']=='NSE' and r['tradingsymbol']==symbol and r['instrument_type']=='EQ')
                quote=self.kite._quotes(['NSE:'+symbol])['NSE:'+symbol];at=datetime.fromisoformat(quote['timestamp']);at=at if at.tzinfo else at.replace(tzinfo=IST)
                bid=float(quote['depth']['buy'][0]['price']);tick=paise(float(row['tick_size']));trigger=parent['order']['stop_loss']
                if not -2<=(self.clock()-at).total_seconds()<=30 or int(quote['instrument_token'])!=int(row['instrument_token']) or not math.isfinite(bid) or bid<=0:raise ValueError('Fresh quote required for broker protection')
                breached=bid<=trigger;reference=bid if breached else trigger
                limit=max(tick,(paise(reference)*98//100)//tick*tick)/100
                order=dict(request_id='protect-'+uuid.uuid4().hex,symbol=symbol,side='SELL',quantity=quantity,limit_price=limit,stop_loss=trigger,order_type='LIMIT' if breached else 'SL',rationale='Authorized protective exit after stop breach' if breached else 'Authorized native protective stop for verified buy fills')
                item=dict(id=uuid.uuid4().hex,request_id=order['request_id'],order=order,tag='artha'+secrets.token_hex(7),origin='PROTECTIVE',parent_id=parent['id'],created_at=self.clock().isoformat(),status='SENDING',filled_quantity=0,average_price=0,fee_reserve_paise=0,evidence={'mandate_hash':m['hash'],'verified_parent_fills':parent['filled_quantity']})
                with self.transaction():self._save(item);self._event('SEND_RESERVED',item)
                try:
                    result=self.broker.place(order,item['tag']);identifier=str(result.get('order_id',''))
                    if not identifier.isdigit() or len(identifier)>30:raise ValueError('Invalid stop acknowledgement')
                    item.update(status='ACKNOWLEDGED',broker_order_id=identifier)
                    with self.transaction():self._save(item);self._event('BROKER_ACK',item)
                except Exception:
                    item['status']='UNCERTAIN'
                    with self.transaction():self._save(item);self._event('SEND_UNCERTAIN',item)
                    raise ValueError('Protective request outcome is uncertain')
            except Exception:
                with self.transaction():self._set('armed',False);self._set('protection_error','Native protection is missing, failed or uncertain. Inspect broker orders immediately; no request was retried.');self._event('PROTECTION_FAILED',{'symbol':symbol})
                return

    def status(self):
        with self.lock:
            snap=self._get('snapshot');m=self._get('mandate');audit=self.audit()
            display=(self._get('portfolio_snapshot') or snap) if audit['ok'] else None
            return dict(portfolio_view=portfolio_view(display),broker_portfolio=broker_rows(display),worker={'last_tick':self.last_tick,'watch_required':self.watch_required},mode='SUPERVISED_REAL_MONEY_PILOT',transport_enabled=self.broker.enabled,armed=bool(self._get('armed')),authenticated=self.kite.status()['authenticated'],mandate=m,
                protection=self.protection(),reconciliation=dict(observed_at=snap['observed_at'] if snap else None,account_id=snap['account_id'] if snap else None,error=self._get('reconcile_error'),net_funds=self._funds(snap)/100 if snap else None,gross_daily_loss=self._loss(snap)/100 if snap else None),orders=self.orders(),audit=audit,
                prerequisites=['Qualified forward portfolio and current guardian approval','Exact broker account and explicit capital/loss/order mandate','Whitelisted static IP and delivery-sale authorization','Reviewed deployment transport capability, fresh reconciliation and explicit arming'],
                limitations=['Manual confirmation for every NSE/CNC LIMIT order; no AI or strategy order dispatch','Loss halt pauses entries and requests cancellation of known entry remainders; it cannot guarantee cancellation or liquidation','Verified buy fills receive authorized native stop-limit exits; gaps can remain unfilled; supervise in Kite','Pilot capital limits lifetime gross purchases, not reinvestment','Broker trade fills are authoritative; contract-note fees and settlement still need owner review','Orders absent from the daily broker book require manual investigation; never resubmit an uncertain order'])

    def close(self):
        self.pause()
        with self.lock:self.db.close()
