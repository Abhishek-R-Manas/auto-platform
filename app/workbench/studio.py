"""Read-only builder operations. No method dispatches an order or grants authority."""
import math
from datetime import datetime
from .models import Basket,Analysis
from .analytics import analyze
from ..engine import IST
from ..finance import option_value
from ..research.catalog import digest,canonical

class Studio:
    def __init__(self,workspace,pilot=None):
        self.workspace=workspace;self.pilot=pilot
        with workspace.lock,workspace.db:
            workspace.db.execute('CREATE TABLE IF NOT EXISTS strategy_marks(id TEXT PRIMARY KEY,basket_hash TEXT NOT NULL,document TEXT NOT NULL)')

    def _active(self,b):
        result=[l for l in b.legs if l.enabled]
        if not result:raise ValueError('Enable at least one leg')
        return result

    def _bound(self,b):
        master=self.workspace._master();result=[]
        for l in self._active(b):
            row=master.get(l.key)
            if not row or row['instrument_type']!=l.kind or int(row['lot_size'])!=l.lot_size:raise ValueError('Bind every enabled leg to an exact current contract and lot size')
            if l.kind in {'CE','PE','FUT'} and (row.get('expiry')!=l.expiry.astimezone(IST).date().isoformat() or (l.kind in {'CE','PE'} and float(row['strike'])!=l.strike)):raise ValueError('A leg strike or expiry differs from the current broker contract')
            result.append((l,row))
        underlying_keys={self.workspace.kite.underlying_key(row) if l.kind!='EQ' else l.key for l,row in result}
        if any((row['name'] if l.kind!='EQ' else row['tradingsymbol'])!=b.underlying for l,row in result):raise ValueError('The selected underlying does not match the bound contracts')
        if len(underlying_keys)!=1:raise ValueError('All enabled legs must share the same underlying')
        return result

    def adjust(self,body):
        b=body.basket;active=self._active(b);opts=[l for l in active if l.kind in {'CE','PE'}]
        if not opts:raise ValueError('Strike controls require option legs')
        if body.action=='WIDTH' and {l.kind for l in opts}!={'CE','PE'}:raise ValueError('Width needs both calls and puts; edit single-type butterfly wings using Hedge')
        if body.action=='HEDGE' and not any(l.hedge for l in opts):raise ValueError('Select the hedge legs below the editor first')
        if body.action=='EXPIRY' and (body.expiry is None or body.expiry.tzinfo is None or body.expiry<=self.workspace.clock()):raise ValueError('Choose a future timezone-aware expiry')
        result=b.model_dump(mode='json');changes={};model=b.source=='HYPOTHETICAL'
        for index,l in enumerate(b.legs):
            if not l.enabled or l.kind not in {'CE','PE'}:continue
            direction=1
            if body.action=='HEDGE':
                if not l.hedge:continue
                mains=[p for p in opts if not p.hedge and p.kind==l.kind and p.expiry==l.expiry and p.strike!=l.strike]
                if not mains:raise ValueError('Each hedge needs a main leg of the same option type and expiry at another strike')
                main=min(mains,key=lambda p:abs(p.strike-l.strike));direction=1 if l.strike>main.strike else -1
            elif body.action=='WIDTH':direction=1 if l.kind=='CE' else -1
            expiry=l.expiry+(body.expiry-min(p.expiry for p in opts)) if body.action=='EXPIRY' else l.expiry
            if model:
                strike=l.strike+(0 if body.action=='EXPIRY' else direction*body.steps*body.strike_step)
                if strike<=0:raise ValueError('This adjustment would create a nonpositive strike')
                years=max(0,(expiry-self.workspace.clock()).total_seconds()/(365*86400))
                value=option_value(b.spot,strike,years,(l.iv_pct or b.volatility_pct)/100,l.kind,b.interest_rate,b.dividend_yield)
                update=dict(strike=strike,expiry=expiry.isoformat(),key=None,premium=round(value,2),price_source='MODEL')
            else:
                chain=self.workspace.chain(b.underlying,expiry.astimezone(IST).date().isoformat())['data']
                if not chain:raise ValueError('Refresh the required expiry chain before adjusting observed contracts')
                strikes=sorted({p['strike'] for p in chain['contracts'] if p['kind']==l.kind})
                if l.strike not in strikes:raise ValueError('The recorded chain does not contain the current strike')
                target=strikes.index(l.strike)+(0 if body.action=='EXPIRY' else direction*body.steps)
                if not 0<=target<len(strikes):raise ValueError('Refresh a wider chain; the required strike is missing')
                q=next(p for p in chain['contracts'] if p['kind']==l.kind and p['strike']==strikes[target])
                if q['lot_size']!=l.lot_size:raise ValueError('Expiry lot size differs; explicitly review quantities before rolling')
                field='ask' if l.side=='BUY' else 'bid';value=q.get(field)
                if value is None:value=q['last'];source='KITE_LAST'
                else:source='KITE_ASK' if l.side=='BUY' else 'KITE_BID'
                update=dict(key=q['key'],strike=q['strike'],expiry=q['expiry'],premium=value,iv_pct=q['iv_pct'],price_source=source)
            changes[index]=update
        # Reject collapsing/crossing an existing strike ordering; width may widen a straddle.
        for i,one in enumerate(b.legs):
            if i not in changes:continue
            for j,two in enumerate(b.legs):
                if j<=i or j not in changes or one.kind!=two.kind or one.expiry!=two.expiry:continue
                old=one.strike-two.strike;new=changes[i]['strike']-changes[j]['strike']
                if old*new<0 or (old!=0 and new==0):raise ValueError('The adjustment would cross or collapse existing legs')
        if body.action=='WIDTH':
            for expiry in {l.expiry for l in opts}:
                calls=[changes[i]['strike'] for i,l in enumerate(b.legs) if i in changes and l.expiry==expiry and l.kind=='CE']
                puts=[changes[i]['strike'] for i,l in enumerate(b.legs) if i in changes and l.expiry==expiry and l.kind=='PE']
                if calls and puts and min(calls)<max(puts):raise ValueError('Width cannot cross the call and put main strikes')
        if body.action=='HEDGE':
            for i,l in enumerate(b.legs):
                if i not in changes:continue
                for p in opts:
                    if not p.hedge and p.kind==l.kind and p.expiry==l.expiry and (l.strike-p.strike)*(changes[i]['strike']-p.strike)<=0:raise ValueError('A hedge cannot cross or collapse onto a main leg')
        for i,update in changes.items():result['legs'][i].update(update)
        return dict(basket=Basket.model_validate(result).model_dump(mode='json'),changed_legs=len(changes),execution_authority=False)

    def mark(self,body):
        b=body.basket;bound=self._bound(b)
        if not self.workspace.kite.status()['authenticated']:raise ValueError('Sign in to refresh genuine strategy quotes')
        master=self.workspace._master();underlying=self.workspace.kite.underlying_key(bound[0][1]) if bound[0][0].kind!='EQ' else bound[0][0].key
        if underlying not in master:raise ValueError('Underlying contract identity is unavailable')
        keys=list(dict.fromkeys([underlying]+[l.key for l,row in bound]));raw=self.workspace.kite._quotes(keys);now=self.workspace.clock();quotes={}
        for key in keys:quotes[key]=self.workspace._quote(key,master[key],raw.get(key,{}),now)
        observations=[];value=0;entry=0
        for l,row in bound:
            q=quotes[l.key];signed=l.lots*l.lot_size*(1 if l.side=='BUY' else -1);value+=signed*q['last'];entry+=signed*l.premium
            age=(now-datetime.fromisoformat(q['exchange_time'])).total_seconds()
            observations.append(dict(key=l.key,kind=l.kind,side=l.side,units=signed,price=q['last'],entry_price=l.premium,pnl=signed*(q['last']-l.premium),oi=q['oi'],bid=q['bid'],ask=q['ask'],exchange_time=q['exchange_time'],fresh=-2<=age<=30))
        spot=quotes[underlying]
        if spot['last']<=0:raise ValueError('A positive underlying quote is required for strategy tracking')
        ages=[(now-datetime.fromisoformat(q['exchange_time'])).total_seconds() for q in quotes.values()]
        item=dict(basket_hash=digest(b.model_dump(mode='json')),observed_at=now.isoformat(),spot=spot['last'],spot_time=spot['exchange_time'],net_value=value,net_entry=entry,pnl=value-entry-b.cost_estimate+(b.manual_pnl if b.manual_pnl_enabled else 0),cost_estimate=b.cost_estimate,manual_pnl=b.manual_pnl if b.manual_pnl_enabled else 0,legs=observations,fresh=all(-2<=age<=30 for age in ages) and max(ages)-min(ages)<=5,source='KITE_LAST',execution_authority=False)
        bounds=analyze(Analysis(basket=b),now)
        item['expiry_profit_above_mark']=max(0,bounds['best_case_pnl']-item['pnl']) if bounds['best_case_pnl'] is not None else None
        item['expiry_downside_from_mark']=max(0,item['pnl']-bounds['worst_case_pnl']) if bounds['worst_case_pnl'] is not None else None
        item['id']=digest(item)
        with self.workspace.lock,self.workspace.db:
            self.workspace.db.execute('INSERT OR IGNORE INTO strategy_marks VALUES(?,?,?)',(item['id'],item['basket_hash'],canonical(item)))
        return item

    def history(self,body):
        identifier=digest(body.basket.model_dump(mode='json'))
        with self.workspace.lock:rows=self.workspace.db.execute('SELECT id,document FROM strategy_marks WHERE basket_hash=? ORDER BY rowid DESC LIMIT 500',(identifier,)).fetchall()
        result=[]
        for key,document in reversed(rows):
            item=__import__('json').loads(document);core=dict(item);hashed=core.pop('id')
            if hashed!=key or digest(core)!=key or core['basket_hash']!=identifier:raise ValueError('Strategy quote history integrity failed')
            result.append(item)
        return dict(basket_hash=identifier,observations=result,source='Actual local quote observations only',execution_authority=False)

    def chain_series(self,body):
        with self.workspace.lock:rows=self.workspace.db.execute('SELECT id,document FROM chains WHERE underlying=? AND expiry=? ORDER BY rowid DESC LIMIT 200',(body.underlying,body.expiry)).fetchall()
        points=[];keys=set();ivs=[]
        for identifier,value in reversed(rows):
            item=__import__('json').loads(value);core=dict(item);hashed=core.pop('id')
            if identifier!=hashed or digest(core)!=identifier:raise ValueError('Saved chain history integrity failed')
            contracts=[dict(key=p['key'],kind=p['kind'],strike=p['strike'],premium=p['last'],oi=p['oi'],iv_pct=p['iv_pct'],exchange_time=p['exchange_time']) for p in item['contracts'] if p['strike'] in body.strikes]
            keys.update(p['key'] for p in contracts);points.append(dict(time=item['observed_at'],contracts=contracts))
            valid=[p['iv_pct'] for p in contracts if p['iv_pct'] is not None]
            if valid:ivs.append(sum(valid)/len(valid))
        rank=None;percentile=None
        # This window is explicitly local recorded snapshots, never a 1-year IV statistic.
        if len(ivs)>1:
            if max(ivs)>min(ivs):rank=100*(ivs[-1]-min(ivs))/(max(ivs)-min(ivs))
            percentile=100*sum(v<ivs[-1] for v in ivs[:-1])/len(ivs[:-1])
        return dict(observations=points,keys=sorted(keys),iv_rank_pct=rank,iv_percentile_pct=percentile,iv_sample_count=len(ivs),window='Selected strikes in up to 200 actual local snapshots; not annual IV rank/percentile',execution_authority=False)

    def candidates(self):
        if self.pilot is None:raise ValueError('Broker portfolio import is unavailable')
        with self.pilot.lock:
            if not self.pilot.audit()['ok']:raise ValueError('Broker portfolio audit failed')
            snapshot=self.pilot._get('portfolio_snapshot')
        if not snapshot:return dict(snapshot_hash=None,groups=[],unavailable=[],message='Sync Zerodha in Portfolio first; no broker read happens automatically')
        master=self.workspace._master();groups={};unavailable=[]
        for p in snapshot['positions']['net']:
            quantity=p.get('quantity',0)
            if not quantity:continue
            key=str(p.get('exchange'))+':'+str(p.get('tradingsymbol'));r=master.get(key)
            if not r or r['instrument_type'] not in {'CE','PE','FUT','EQ'} or r.get('segment')=='INDICES':unavailable.append(dict(key=key,reason='No supported current contract'));continue
            lot=int(r['lot_size'])
            if type(quantity) is not int or abs(quantity)%lot:unavailable.append(dict(key=key,reason='Quantity is not a complete current lot'));continue
            price=p.get('average_price')
            if not isinstance(price,(int,float)) or not math.isfinite(price) or price<=0:unavailable.append(dict(key=key,reason='Broker entry price unavailable'));continue
            underlying=r['name'] if r['exchange']=='NFO' else r['tradingsymbol'];kind=r['instrument_type']
            expiry=datetime.fromisoformat(r['expiry']).replace(hour=15,minute=30,tzinfo=IST).isoformat() if kind!='EQ' else None
            identity=digest(dict(key=key,product=p.get('product','')))
            groups.setdefault(underlying,[]).append(dict(id=identity,key=key,product=p.get('product',''),quantity=quantity,leg=dict(key=key,kind=kind,side='BUY' if quantity>0 else 'SELL',strike=float(r['strike']) if kind in {'CE','PE'} else 0,expiry=expiry,lots=abs(quantity)//lot,lot_size=lot,premium=price,price_source='MANUAL')))
        return dict(snapshot_hash=digest(snapshot),observed_at=snapshot.get('observed_at'),groups=[dict(underlying=k,positions=v) for k,v in groups.items()],unavailable=unavailable,message='Cached broker positions; entry averages imported for analysis only. Realised P&L is not inferred.')

    def import_positions(self,body):
        candidates=self.candidates()
        if candidates['snapshot_hash']!=body.snapshot_hash:raise ValueError('Broker snapshot changed; reopen the position selection')
        if len(set(body.selected))!=len(body.selected):raise ValueError('Select each position once')
        found=[(g['underlying'],p) for g in candidates['groups'] for p in g['positions'] if p['id'] in body.selected]
        if len(found)!=len(body.selected) or len({u for u,p in found})!=1:raise ValueError('Select supported positions from one underlying')
        name=found[0][0];b=Basket(name=name+' · imported positions',underlying=name,spot=body.spot,source='MANUAL',legs=[p['leg'] for u,p in found],notes='Imported cached broker average prices. Realised P&L and broker cash are not changed; edit manual P&L explicitly if required.')
        return dict(basket=b.model_dump(mode='json'),execution_authority=False,snapshot_hash=body.snapshot_hash)

    def margins(self,body):
        if self.pilot is None:raise ValueError('Broker margin calculator unavailable')
        bound=self._bound(body.basket);orders=[]
        for l,row in bound:
            if row.get('segment')=='INDICES':raise ValueError('Cash indices are analysis references, not tradable margin legs')
            if l.premium<=0:raise ValueError('Enter a positive limit price before margin calculation')
            orders.append(dict(exchange=row['exchange'],tradingsymbol=row['tradingsymbol'],transaction_type=l.side,variety='regular',product='CNC' if l.kind=='EQ' else body.product,order_type='LIMIT',quantity=l.lots*l.lot_size,price=l.premium,trigger_price=0))
        # User ordering is retained: initial margin depends on execution sequence.
        response=self.pilot.broker.basket_margin(orders,body.consider_positions)
        initial=response.get('initial',{}).get('total');final=response.get('final',{}).get('total')
        if any(type(v) not in {float,int} or not math.isfinite(v) for v in (initial,final)):raise ValueError('Broker margin response is incomplete')
        charges=[]
        for order in response.get('orders',[]):
            total=order.get('charges',{}).get('total')
            charges.append(total if type(total) in {int,float} and math.isfinite(total) and total>=0 else None)
        a=analyze(Analysis(basket=body.basket),self.workspace.clock());roi=100*a['max_profit']/final if not body.consider_positions and final>0 and a['max_profit'] is not None else None
        return dict(max_profit_on_final_margin_pct=roi,reward_risk=a['reward_risk'],observed_at=self.workspace.clock().isoformat(),basket_hash=digest(body.basket.model_dump(mode='json')),initial=initial,final=final,estimated_entry_charges=sum(charges) if len(charges)==len(orders) and all(c is not None for c in charges) else None,consider_positions=body.consider_positions,product=body.product,execution_authority=False,message='Broker calculation for this exact leg order and products; changes and fills require recalculation. Entry charges only; exit charges and settlement are excluded.')
