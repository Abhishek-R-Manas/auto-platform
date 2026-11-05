"""Watchlists, immutable draft revisions and read-only option-chain snapshots."""
from datetime import datetime,timedelta,timezone
import copy,json,math,sqlite3,threading,uuid
from httpx import HTTPError
from ..engine import IST
from ..finance import implied_volatility,option_greeks,option_value
from ..research.catalog import canonical,digest
from ..research.schemas import Run,Strategy
from .models import Basket,Watchlist
from .templates import catalog,RECIPES

DEFAULT_KEYS=['NSE:RELIANCE','NSE:HDFCBANK','NSE:INFY','NSE:ICICIBANK','NSE:TCS']
class Workspace:
    def __init__(self,root,kite,hub,research,clock=None):
        root.mkdir(parents=True,exist_ok=True,mode=0o700);self.kite=kite;self.hub=hub;self.research=research
        self.clock=clock or (lambda:datetime.now(IST));self.lock=threading.RLock();self.fetch_lock=threading.Lock();self.master_cache={};self.master_identity=None
        self.db=sqlite3.connect(root/'workspace.sqlite3',check_same_thread=False);self.db.row_factory=sqlite3.Row
        self.db.executescript("""PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS settings(name TEXT PRIMARY KEY,document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS quote_cache(key TEXT PRIMARY KEY,document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chains(id TEXT PRIMARY KEY,underlying TEXT NOT NULL,expiry TEXT NOT NULL,document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,document TEXT NOT NULL);""")
    def _master(self):
        rows=self.kite.master();identity=(self.kite.master_hash,len(rows))
        with self.lock:
            if identity!=self.master_identity:
                self.master_cache={r['exchange']+':'+r['tradingsymbol']:r for r in rows if r['exchange'] in {'NSE','NFO'}};self.master_identity=identity
            return self.master_cache
    def watchlists(self):
        with self.lock:
            row=self.db.execute("SELECT document FROM settings WHERE name='watchlists'").fetchone()
            if row:return json.loads(row[0])
            old=self.db.execute("SELECT document FROM settings WHERE name='watchlist'").fetchone();item=json.loads(old[0]) if old else dict(name='My watchlist',keys=DEFAULT_KEYS)
            return dict(active_id='main',lists=[dict(id='main',**item)],max_lists=10,max_items=50)
    def _save_lists(self,document):
        with self.db:self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('watchlists',canonical(document)))
    def create_watchlist(self,body):
        with self.lock:
            document=self.watchlists()
            if len(document['lists'])>=10:raise ValueError('You can keep up to 10 watchlists')
            self._validate_new_keys(body.keys,document)
            item=dict(id=uuid.uuid4().hex,name=body.name,keys=body.keys);document['lists'].append(item);document['active_id']=item['id'];self._save_lists(document)
        return self.watchlist()
    def _validate_new_keys(self,keys,document):
        known={k for group in document['lists'] for k in group['keys']};added=set(keys)-known
        if added and not added<=self._master().keys():raise ValueError('A watchlist instrument is absent from the current master')
    def activate_watchlist(self,identifier):
        with self.lock:
            document=self.watchlists()
            if not any(i['id']==identifier for i in document['lists']):raise ValueError('Watchlist not found')
            document['active_id']=identifier;self._save_lists(document)
        return self.watchlist()
    def delete_watchlist(self,identifier):
        with self.lock:
            document=self.watchlists()
            if len(document['lists'])==1:raise ValueError('Keep at least one watchlist; it may be empty')
            if not any(i['id']==identifier for i in document['lists']):raise ValueError('Watchlist not found')
            document['lists']=[i for i in document['lists'] if i['id']!=identifier]
            if document['active_id']==identifier:document['active_id']=document['lists'][0]['id']
            self._save_lists(document)
        return self.watchlist()
    def connect_quotes(self,selected=None):
        if not self.kite.status()['authenticated']:raise ValueError('Sign in to Kite for streaming prices')
        master=self._master();keys={k for i in self.watchlists()['lists'] for k in i['keys']}
        if selected:keys.add(selected)
        mapping={k:dict(instrument_token=int(master[k]['instrument_token'])) for k in keys if k in master}
        if hasattr(self.hub,'set_workspace_instruments'):
            self.hub.set_workspace_instruments(mapping)
            if not self.hub.store.enabled():self.hub.enable(True)
        return dict(connected=True,instruments=len(mapping),execution_changed=False)
    def meta(self):
        groups={};available=True
        try:master=self._master()
        except (ValueError,OSError,HTTPError):master={};available=False
        for key,row in master.items():
            if row['exchange']!='NFO' or row['instrument_type'] not in {'CE','PE'} or not row.get('expiry'):continue
            if row['expiry']<self.clock().date().isoformat():continue
            group=groups.setdefault(row['name'],dict(name=row['name'],key=self.kite.underlying_key(row),expiries=set()))
            group['expiries'].add(row['expiry'])
        return dict(master_available=available,underlyings=[dict(g,expiries=sorted(g['expiries'])) for g in sorted(groups.values(),key=lambda x:(x['name'] not in {'NIFTY','BANKNIFTY'},x['name']))],templates=catalog(),authenticated=self.kite.status()['authenticated'])
    def watchlist(self,identifier=None,selected=None,interval='day'):
        document=self.watchlists();identifier=identifier or document['active_id'];item=next((i for i in document['lists'] if i['id']==identifier),None)
        if item is None:raise ValueError('Watchlist not found')
        # Read only in-memory identities here. A streaming snapshot never fetches a master.
        master=self.master_cache;now=self.clock();keys=list(dict.fromkeys(item['keys']+([selected] if selected else [])))
        with self.lock:cached={key:json.loads(row[0]) for key in keys if (row:=self.db.execute('SELECT document FROM quote_cache WHERE key=?',(key,)).fetchone())}
        tokens={int(master[k]['instrument_token']) for k in keys if k in master}
        with self.hub.lock:live={t:dict(self.hub.quotes[t]) for t in tokens if t in self.hub.quotes}
        items=[]
        for key in keys:
            row=master.get(key);quote=cached.get(key);source='KITE_SNAPSHOT' if quote else None
            if row and (q:=live.get(int(row['instrument_token']))):
                candidate=dict(last=q.get('last'),bid=q.get('bid'),ask=q.get('ask'),bid_quantity=q.get('bid_quantity'),ask_quantity=q.get('ask_quantity'),exchange_time=q.get('exchange_time'),received_at=datetime.fromtimestamp(q['received_at'],timezone.utc).isoformat(),close=q.get('close'),volume=q.get('cumulative_volume'),oi=q.get('oi'))
                if not quote or datetime.fromisoformat(candidate['exchange_time'])>=datetime.fromisoformat(quote['exchange_time']):quote=candidate;source=q.get('transport','KITE_STREAM')
            age=(now-datetime.fromisoformat(quote['exchange_time'])).total_seconds() if quote and quote.get('exchange_time') else None
            receive_age=(now-datetime.fromisoformat(quote['received_at'])).total_seconds() if quote and quote.get('received_at') else None
            items.append(dict(key=key,symbol=key.split(':',1)[1],name=row.get('name') if row else key,kind=('INDEX' if row.get('segment')=='INDICES' else row.get('instrument_type')) if row else None,quote=quote,source=source,fresh=bool(age is not None and -2<=age<=30 and receive_age is not None and -2<=receive_age<=30),age_seconds=age,receive_age_seconds=receive_age))
        display=getattr(self.hub,'display_bars',None);token=int(master[selected]['instrument_token']) if selected in master else None
        return dict(**item,items=[i for i in items if i['key'] in item['keys']],selected_quote=next((i for i in items if i['key']==selected),None),live_bars=display.get(token,interval) if display and token else [],authenticated=self.kite.status()['authenticated'],feed=getattr(self.hub,'phase',None) or self.hub.status()['phase'],watchlists=document,server_time=now.isoformat())
    def save_watchlist(self,body,identifier=None):
        with self.lock:
            document=self.watchlists();identifier=identifier or document['active_id'];item=next((i for i in document['lists'] if i['id']==identifier),None)
            if item is None:raise ValueError('Watchlist not found')
            self._validate_new_keys(body.keys,document);item.update(body.model_dump());self._save_lists(document)
        return self.watchlist(identifier)
    def _quote(self,key,row,raw,at):
        if int(raw.get('instrument_token',0))!=int(row['instrument_token']):raise ValueError('Quote identity mismatch')
        stamp=datetime.fromisoformat(raw['timestamp']);stamp=stamp if stamp.tzinfo else stamp.replace(tzinfo=IST)
        if stamp>at+timedelta(seconds=2):raise ValueError('Future-dated broker quote')
        last=float(raw['last_price'])
        if not math.isfinite(last) or last<0:raise ValueError('Invalid last price')
        result=dict(last=last,exchange_time=stamp.isoformat(),received_at=at.isoformat(),close=None,bid=None,ask=None,bid_quantity=None,ask_quantity=None,volume=None,oi=None)
        close=float(raw.get('ohlc',{}).get('close',0))
        if math.isfinite(close) and close>0:result['close']=close
        for field in ('volume','oi'):
            v=raw.get(field)
            if type(v) is int and v>=0:result[field]=v
        buy=raw.get('depth',{}).get('buy',[]);sell=raw.get('depth',{}).get('sell',[])
        if buy and sell:
            b=float(buy[0].get('price',0));a=float(sell[0].get('price',0));bq=buy[0].get('quantity',0);aq=sell[0].get('quantity',0)
            if all(math.isfinite(v) and v>0 for v in (a,b)) and a>=b and type(bq) is int and type(aq) is int and min(bq,aq)>0:result.update(bid=b,ask=a,bid_quantity=bq,ask_quantity=aq)
        return result
    def refresh_quotes(self):
        if not self.kite.status()['authenticated']:raise ValueError('Sign in with Zerodha to refresh quotes')
        with self.fetch_lock:
            watch=self.watchlist();master=self._master();keys=watch['keys'];
            if not keys:return watch
            raw=self.kite._quotes(keys);now=self.clock();accepted=[]
            for key in keys:
                try:accepted.append((key,self._quote(key,master[key],raw.get(key,{}),now)))
                except (ValueError,TypeError,KeyError,OverflowError):continue
            if not accepted:raise ValueError('No genuine quotes returned; saved observations were retained')
            with self.lock,self.db:self.db.executemany('INSERT OR REPLACE INTO quote_cache VALUES(?,?)',[(key,canonical(q)) for key,q in accepted])
        return self.watchlist()
    def chain(self,underlying='NIFTY',expiry=None):
        with self.lock:
            row=self.db.execute('SELECT document FROM chains WHERE underlying=?'+(' AND expiry=?' if expiry else '')+' ORDER BY rowid DESC LIMIT 1',(underlying,expiry) if expiry else (underlying,)).fetchone()
        item=json.loads(row[0]) if row else None
        if item:
            core=dict(item);expected=core.pop('id')
            if digest(core)!=expected:raise ValueError('Saved chain integrity failed')
        return dict(data=item,authenticated=self.kite.status()['authenticated'],fresh=bool(item and (self.clock()-datetime.fromisoformat(item['observed_at'])).total_seconds()<=30 and all(-2<=(self.clock()-datetime.fromisoformat(p['exchange_time'])).total_seconds()<=30 for p in item['contracts']) and -2<=(self.clock()-datetime.fromisoformat(item['spot_time'])).total_seconds()<=30),message='Saved chain is analysis data; orders independently recheck quotes.' if item else 'Connect Kite and refresh the option chain, or explicitly use hypothetical inputs.')
    def refresh_chain(self,body):
        if not self.kite.status()['authenticated']:raise ValueError('Sign in with Zerodha to retrieve a genuine option chain')
        with self.fetch_lock:
            previous=self.chain(body.underlying,body.expiry)['data'];now=self.clock()
            if previous and previous.get('width',0)>=body.width and (now-datetime.fromisoformat(previous['observed_at'])).total_seconds()<10:return self.chain(body.underlying,body.expiry)
            master=self._master();rows=[r for r in master.values() if r['exchange']=='NFO' and r['name']==body.underlying and r['instrument_type'] in {'CE','PE'} and r.get('expiry') and r['expiry']>=now.date().isoformat()]
            expiries=sorted({r['expiry'] for r in rows});expiry=body.expiry or (expiries[0] if expiries else None)
            if expiry not in expiries:raise ValueError('Select a current expiry for the underlying')
            selected=[r for r in rows if r['expiry']==expiry];key=self.kite.underlying_key(selected[0]);spot_raw=self.kite._quotes([key]).get(key,{})
            if key not in master:raise ValueError('Current underlying identity is unavailable')
            spot_q=self._quote(key,master[key],spot_raw,self.clock());spot=spot_q['last']
            if spot<=0:raise ValueError('Underlying price is unavailable')
            strikes=sorted({float(r['strike']) for r in selected});atm=min(range(len(strikes)),key=lambda i:abs(strikes[i]-spot));wanted=set(strikes[max(0,atm-body.width):atm+body.width+1]);selected=[r for r in selected if float(r['strike']) in wanted]
            raw=self.kite._quotes([key]+['NFO:'+r['tradingsymbol'] for r in selected]);at=self.clock();spot_q=self._quote(key,master[key],raw.get(key,{}),at);spot=spot_q['last'];contracts=[];rejected=[]
            if spot<=0:raise ValueError('Invalid underlying price')
            old={p['key']:p for p in previous['contracts']} if previous else {}
            for row in selected:
                identity='NFO:'+row['tradingsymbol']
                try:
                    q=self._quote(identity,row,raw.get(identity,{}),at);strike=float(row['strike']);expiry_at=datetime.fromisoformat(expiry).replace(hour=15,minute=30,tzinfo=IST);years=(expiry_at-at).total_seconds()/(365*86400)
                    if years<=0:continue
                    mid=(q['bid']+q['ask'])/2 if q['bid'] is not None and q['ask'] is not None else q['last'];iv=implied_volatility(mid,spot,strike,years,row['instrument_type'])
                    g=option_greeks(spot,strike,years,iv,row['instrument_type']) if iv else {}
                    age=(at-datetime.fromisoformat(q['exchange_time'])).total_seconds();spot_age=(at-datetime.fromisoformat(spot_q['exchange_time'])).total_seconds()
                    intrinsic=max(0,spot-strike) if row['instrument_type']=='CE' else max(0,strike-spot)
                    contracts.append(dict(key=identity,symbol=row['tradingsymbol'],kind=row['instrument_type'],strike=strike,expiry=expiry_at.isoformat(),lot_size=int(row['lot_size']),tick_size=float(row['tick_size']),**q,iv_pct=iv*100 if iv else None,greeks=g,intrinsic=intrinsic,time_value=max(0,mid-intrinsic),fresh=bool(-2<=age<=30 and -2<=spot_age<=30 and abs(age-spot_age)<=5),oi_change=q['oi']-old[identity]['oi'] if identity in old and q['oi'] is not None and old[identity]['oi'] is not None else None,iv_change_pp=iv*100-old[identity]['iv_pct'] if iv and identity in old and old[identity]['iv_pct'] is not None else None))
                except (ValueError,KeyError,TypeError,OverflowError):rejected.append(identity)
            if not contracts:raise ValueError('No usable option observations; earlier saved chain was retained')
            calls=sum(p['oi'] or 0 for p in contracts if p['kind']=='CE');puts=sum(p['oi'] or 0 for p in contracts if p['kind']=='PE')
            pain={k:sum((p['oi'] or 0)*(max(0,k-p['strike']) if p['kind']=='CE' else max(0,p['strike']-k)) for p in contracts) for k in sorted(wanted)}
            item=dict(width=body.width,underlying=body.underlying,underlying_key=key,spot=spot,spot_time=spot_q['exchange_time'],expiry=expiry,expiries=expiries,contracts=contracts,rejected=rejected,observed_at=at.isoformat(),source='KITE',master_hash=self.kite.master_hash,put_call_oi_ratio=puts/calls if calls else None,visible_chain_max_pain=min(pain,key=pain.get) if calls+puts else None,coverage='BOUNDED_STRIKES',model='EUROPEAN_BLACK_SCHOLES',execution_authority=False)
            item['id']=digest(item)
            with self.lock,self.db:
                if self.db.execute('SELECT count(*) FROM chains').fetchone()[0]>=5000:raise ValueError('Chain history capacity reached; preserve and review storage before adding snapshots')
                self.db.execute('INSERT OR IGNORE INTO chains VALUES(?,?,?,?)',(item['id'],body.underlying,expiry,canonical(item)))
            return self.chain(body.underlying,expiry)
    def chain_history(self,underlying,expiry):
        with self.lock:items=[json.loads(r[0]) for r in self.db.execute('SELECT document FROM chains WHERE underlying=? AND expiry=? ORDER BY rowid DESC LIMIT 200',(underlying,expiry))]
        return [dict(time=i['observed_at'],put_call_ratio=i['put_call_oi_ratio'],iv_pct=(sum(p['iv_pct'] for p in i['contracts'] if p['iv_pct'] is not None)/sum(p['iv_pct'] is not None for p in i['contracts'])) if any(p['iv_pct'] is not None for p in i['contracts']) else None) for i in reversed(items)]
    def save(self,body):
        core=dict(basket=body.basket.model_dump(mode='json'),parent_id=body.parent_id);identifier=digest(core)
        with self.lock,self.db:
            previous=self.db.execute('SELECT document FROM drafts WHERE request_id=?',(body.request_id,)).fetchone()
            if previous:
                item=json.loads(previous[0])
                if item['id']!=identifier:raise ValueError('Request ID already binds a different strategy draft')
                return item
            if body.parent_id:self.draft(body.parent_id)
            existing=self.db.execute('SELECT document FROM drafts WHERE id=?',(identifier,)).fetchone()
            if existing:return json.loads(existing[0])
            if self.db.execute('SELECT count(*) FROM drafts').fetchone()[0]>=2000:raise ValueError('Saved strategy capacity reached; review storage before adding drafts')
            item=dict(core,id=identifier,created_at=self.clock().isoformat(),status='DRAFT',execution_authority=False)
            self.db.execute('INSERT INTO drafts VALUES(?,?,?)',(identifier,body.request_id,canonical(item)));return item
    def draft(self,identifier):
        with self.lock:row=self.db.execute('SELECT document FROM drafts WHERE id=?',(identifier,)).fetchone()
        if not row:raise ValueError('Saved strategy not found')
        item=json.loads(row[0]);Basket.model_validate(item['basket'])
        if digest(dict(basket=item['basket'],parent_id=item['parent_id']))!=identifier:raise ValueError('Saved strategy integrity failed')
        return item
    def drafts(self):
        with self.lock:ids=[r[0] for r in self.db.execute('SELECT id FROM drafts ORDER BY rowid DESC LIMIT 200')]
        return [self.draft(i) for i in ids]
    def backtest(self,body):
        item=self.draft(body.draft_id);b=dict(item['basket']);dataset=self.research.catalog.verify(body.dataset_id);instruments={i['id']:i for i in dataset['manifest']['instruments']};mapping=body.mapping
        if 'underlying' not in mapping or mapping['underlying'] not in instruments:raise ValueError('Explicitly map the signal underlying to an instrument in the selected historical dataset')
        if not any(l.get('enabled',True) for l in b['legs']):raise ValueError('Enable at least one leg before backtesting')
        legs=[]
        for index,l in enumerate(b['legs']):
            if not l.get('enabled',True):continue
            identifier=mapping.get(str(index));target=instruments.get(identifier)
            expected='OPT' if l['kind'] in {'CE','PE'} else l['kind']
            if not target or target['kind']!=expected or (expected=='OPT' and target['option_type']!=l['kind']):raise ValueError('Map every leg to a compatible historical instrument; no current contract is silently substituted')
            legs.append(dict(instrument_id=identifier,ratio=l['lots']*(1 if l['side']=='BUY' else -1),product='DERIVATIVE' if expected in {'OPT','FUT'} else 'DELIVERY' if b['rules']['allow_overnight'] else 'INTRADAY'))
        rules=b['rules'];family=rules['family'] if b['kind']=='RULES' else 'RULES'
        spec=dict(id='draft-'+item['id'][:20],name=b['name'],family=family,version=item['id'][:20],signal_instruments=[mapping['underlying']],legs=legs,parameters=rules['parameters'],stop_pct=rules['stop_pct'],target_multiple=rules['target_multiple'],allocation_pct=rules['allocation_pct'],allow_short=any(l['side']=='SELL' for l in b['legs'] if l.get('enabled',True)),allow_overnight=rules['allow_overnight'],size_mode='LOTS',lots=1)
        if family=='RULES':spec['entry_rule']={'left':{'feature':'close','instrument_id':mapping['underlying']},'op':'gt','right':0}
        strategy=Strategy.model_validate(spec).model_dump(mode='json');config=Run.model_validate(dict(dataset_id=body.dataset_id,name=b['name']+' · historical test',capital=body.capital,strategies=[strategy],execution={'allow_uncovered_options':body.allow_uncovered_options})).model_dump(mode='json')
        return self.research.create(config)
    def build_template(self,body):
        if body.source=='HYPOTHETICAL':return self.model_template(body)
        if body.template_id not in RECIPES:raise ValueError('Unknown template')
        name,category,recipe=RECIPES[body.template_id];legs=[];chains={}
        for rank in {e for k,o,r,e in recipe}:
            expiry=body.next_expiry if rank else body.expiry
            if expiry is None:raise ValueError('A later expiry is required for this template')
            chain=self.chain(body.underlying,expiry.astimezone(IST).date().isoformat())['data']
            if not chain:raise ValueError('No recorded chain exists for a required expiry; refresh it first')
            chains[rank]=chain
        for kind,offset,ratio,rank in recipe:
            chain=chains[rank]
            if kind=='E':legs.append(dict(key=chain['underlying_key'],kind='EQ',side='BUY' if ratio>0 else 'SELL',strike=0,expiry=None,lots=abs(ratio)*chain['contracts'][0]['lot_size'],lot_size=1,premium=chain['spot'],iv_pct=None,price_source='KITE_LAST'));continue
            strikes=sorted({p['strike'] for p in chain['contracts']});atm=min(range(len(strikes)),key=lambda i:abs(strikes[i]-chain['spot']));index=atm+offset
            if not 0<=index<len(strikes):raise ValueError('The recorded chain lacks a required strike')
            contract=next((p for p in chain['contracts'] if p['strike']==strikes[index] and p['kind']==kind),None)
            if not contract:raise ValueError('A required contract observation is missing')
            side='BUY' if ratio>0 else 'SELL';field='ask' if side=='BUY' else 'bid';quoted=contract[field] is not None
            legs.append(dict(key=contract['key'],kind=kind,side=side,strike=contract['strike'],expiry=contract['expiry'],lots=abs(ratio),lot_size=contract['lot_size'],premium=contract[field] if quoted else contract['last'],iv_pct=contract['iv_pct'],price_source=('KITE_ASK' if side=='BUY' else 'KITE_BID') if quoted else 'KITE_LAST'))
        return Basket.model_validate(dict(name=name,underlying=body.underlying,spot=body.spot,volatility_pct=body.volatility_pct,legs=legs,source='KITE',notes='Entry prices come from recorded Kite chain observations, not generated model prices. Underlying and volatility can be explicit scenario assumptions.')).model_dump(mode='json')

    def screen(self,body):
        from .models import Analysis,TemplateApply
        from .analytics import analyze
        results=[];unavailable=[]
        for entry in catalog():
            if body.category!='All' and entry['category']!=body.category:continue
            try:
                request=body.template.model_copy(update={'template_id':entry['id']});basket=self.build_template(request);basket['cost_estimate']=body.cost_estimate
                result=analyze(Analysis(basket=Basket.model_validate(basket),target_move_pct=body.target_move_pct,days_forward=body.days_forward,iv_shift_pp=body.iv_shift_pp),self.clock())
                if body.max_loss and (result['max_loss'] is None or result['unlimited_loss'] or result['max_loss']>body.max_loss):continue
                results.append(dict(template_id=entry['id'],name=entry['name'],category=entry['category'],source=basket['source'],target_pnl=result['target_pnl'],max_profit=result['max_profit'],max_loss=result['max_loss'],unlimited_profit=result['unlimited_profit'],unlimited_loss=result['unlimited_loss'],model_pop_pct=result['model_pop_pct'],net_debit=result['net_debit'],single_expiry=result['single_expiry']))
            except ValueError as exc:unavailable.append(dict(template_id=entry['id'],reason=str(exc)))
        results.sort(key=lambda x:x['target_pnl'],reverse=True)
        return dict(results=results,unavailable=unavailable,ranking='Selected price/date/IV scenario P&L, not expected realized performance or a recommendation',margin_verified=False,execution_authority=False)

    def model_template(self,body):
        if body.template_id not in RECIPES:raise ValueError('Unknown strategy template')
        name,category,recipe=RECIPES[body.template_id];legs=[];atm=math.floor(body.spot/body.strike_step+.5)*body.strike_step
        for kind,offset,ratio,rank in recipe:
            expiry=body.next_expiry if rank else body.expiry
            if expiry is None:raise ValueError('This calendar/diagonal template needs a later expiry')
            strike=atm+offset*body.strike_step
            if strike<=0:raise ValueError('Strike spacing makes a nonpositive strike; choose a smaller step')
            years=max(0,(expiry-self.clock()).total_seconds()/(365*86400))
            value=body.spot if kind=='E' else option_value(body.spot,strike,years,body.volatility_pct/100,kind)
            legs.append(dict(kind='EQ' if kind=='E' else kind,side='BUY' if ratio>0 else 'SELL',strike=0 if kind=='E' else strike,expiry=None if kind=='E' else expiry.isoformat(),lots=abs(ratio)*body.lot_size if kind=='E' else abs(ratio),lot_size=1 if kind=='E' else body.lot_size,premium=round(value,2),iv_pct=body.volatility_pct if kind!='E' else None,price_source='MODEL'))
        return Basket.model_validate(dict(name=name,underlying=body.underlying,spot=body.spot,volatility_pct=body.volatility_pct,legs=legs,source='HYPOTHETICAL',notes='Explicit hypothetical template. Model prices are not broker observations.')).model_dump(mode='json')

    def paper_basket(self,body,forward):
        from ..forward.defaults import defaults
        from ..forward.schemas import Portfolio
        item=self.draft(body.draft_id);b=dict(item['basket']);b['legs']=[l for l in b['legs'] if l.get('enabled',True)];master=self._master();bindings={};legs=[]
        if not b['legs']:raise ValueError('Enable at least one leg before a paper handoff')
        if b['source']=='HYPOTHETICAL':raise ValueError('Hypothetical drafts can be analyzed; bind actual current contracts before forward testing')
        for index,l in enumerate(b['legs']):
            raw=master.get(l['key']);identifier='LEG_'+str(index)
            if not raw or raw.get('segment')=='INDICES' or raw['instrument_type']!=l['kind'] or int(raw['lot_size'])!=l['lot_size']:raise ValueError('Refresh and bind exact current contracts and lot sizes before forward testing')
            if l['kind'] in {'CE','PE','FUT'} and (not l['expiry'] or raw.get('expiry')!=datetime.fromisoformat(l['expiry']).astimezone(IST).date().isoformat() or (l['kind'] in {'CE','PE'} and float(raw['strike'])!=l['strike'])):raise ValueError('The saved strike or expiry does not match the exact current contract')
            bindings[identifier]=l['key'];legs.append(dict(instrument_id=identifier,ratio=l['lots']*(1 if l['side']=='BUY' else -1),product='DERIVATIVE' if l['kind'] in {'CE','PE','FUT'} else 'DELIVERY' if b['rules']['allow_overnight'] else 'INTRADAY'))
        underlying_key=next((self.kite.underlying_key(master[l['key']]) for l in b['legs'] if l['kind'] in {'CE','PE','FUT'}),b['legs'][0]['key'])
        reference=next((identifier for identifier,key in bindings.items() if key==underlying_key),'REFERENCE')
        if reference not in bindings:bindings[reference]=underlying_key
        rules=b['rules'];spec=dict(id='basket_'+item['id'][:16],name=b['name'],family='SCHEDULED' if b['kind']=='OPTIONS' else rules['family'],signal_instruments=[reference],legs=legs,parameters=rules['parameters'],size_mode='LOTS',lots=1,allocation_pct=rules['allocation_pct'],stop_pct=rules['stop_pct'],target_multiple=rules['target_multiple'],allow_short=any(l['side']=='SELL' for l in b['legs'] if l.get('enabled',True)),allow_overnight=rules['allow_overnight'])
        if b['kind']=='OPTIONS':spec['schedule']=[dict(at=self.clock().isoformat(),action='ENTER')]
        elif spec['family']=='RULES':spec['entry_rule']={'left':{'feature':'close','instrument_id':reference},'op':'gt','right':0}
        protocol=defaults(self.clock().isoformat());protocol.pop('notes',None);protocol.update(request_id='workspace-'+uuid.uuid4().hex,name=b['name']+' · forward paper',capital=body.capital,strategies=[Strategy.model_validate(spec).model_dump(mode='json')],bindings=bindings,reviewer=body.reviewer,review_note=body.review_note,data_rights_confirmed=body.data_rights_confirmed,assumptions_reviewed=body.assumptions_reviewed)
        protocol['execution']={'max_quote_age_seconds':30,'latency_seconds':1,'allow_uncovered_options':body.allow_uncovered_options}
        return forward.create(Portfolio.model_validate(protocol))

    def close(self):
        with self.lock:self.db.close()
