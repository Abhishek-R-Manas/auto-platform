"""Live event-clock orchestration over the historical engine's financial/risk primitives."""
import copy
import json
import math
import statistics
from datetime import timedelta
from zoneinfo import ZoneInfo
from ..research.replay import Replay
from ..research.signals import signal
from ..research.catalog import aware, iso, digest, bounds
from ..finance import paise, rupees, option_value
from ..execution import PaperExecutionAdapter
from dataclasses import replace

ACTIVE = {'PENDING_ACK', 'OPEN', 'PARTIAL', 'CANCEL_PENDING'}

class ProtectedPaperAdapter(PaperExecutionAdapter):
    def __init__(self,runtime):self.runtime=runtime
    def propose(self,order,instrument,quote,slippage_bps,used):
        executable=dict(quote)
        for field in ('bid_quantity','ask_quantity'):executable[field]=math.floor(executable[field]*self.runtime.config['execution']['participation_pct']/100)
        proposal=super().propose(order,instrument,executable,slippage_bps,used)
        runtime=self.runtime
        if not order['reduce_only'] or order['side']!='SELL' or runtime.config['execution']['allow_uncovered_options'] or instrument['kind'] not in {'EQ','OPT'}:return proposal
        owned=list(runtime.lots(book=order['strategy_id']));limit=proposal.quantity
        short_groups={}
        for _,identifier,lot in owned:
            item=runtime.terms(identifier)
            if lot['quantity']<0 and item['kind']=='OPT':short_groups.setdefault((item['underlying'],item['option_type']),[]).append((item,lot))
        for (underlying,kind),shorts in short_groups.items():
            covers=instrument['kind']=='EQ' and instrument['id']==underlying and kind=='CE' or instrument['kind']=='OPT' and instrument['underlying']==underlying and instrument['option_type']==kind
            if not covers:continue
            latest=max(aware(item['expiry']) for item,_ in shorts);coverage=0
            for _,identifier,lot in owned:
                item=runtime.terms(identifier)
                if lot['quantity']<=0:continue
                if item['kind']=='EQ' and item['id']==underlying and kind=='CE' or item['kind']=='OPT' and item['underlying']==underlying and item['option_type']==kind and aware(item['expiry'])>=latest:coverage+=lot['quantity']
            limit=min(limit,max(0,coverage-sum(abs(lot['quantity']) for _,lot in shorts)))
        limit=limit//instrument['lot_size']*instrument['lot_size']
        return replace(proposal,quantity=limit,reason='Hedge exit waits for filled short-leg buyback' if not limit else proposal.reason)

class ForwardReplay(Replay):
    def __init__(self, document, state=None):
        self.document=document
        super().__init__({'manifest':document['manifest']}, document['replay_config'], lambda *args: None, copy.deepcopy(state))
        self.policy=document['config']['supervision']
        self.adapter=ProtectedPaperAdapter(self)
        self.state.setdefault('forward', dict(paused=True, retired=False, blockers=[], seen={}, liquidity={},
            bar_watermarks={}, buffers={}, signal_times={}, sessions=[], observed_ticks=0, covered_ticks=0,
            last_tick=None, last_bar=None, last_poll=None, ever_running=False, started_at=None,
            benchmark_start=None, benchmark_value=None, gaps=[], last_healthy=None))
        self.state['clock']=self.state['clock'] or document['created_at']

    def fresh(self, identifier):
        if not super().fresh(identifier): return False
        quote=self.quote(identifier);at=aware(self.state['clock'])
        if not quote.get('received_at') or not 0 <= (at-aware(quote['received_at'])).total_seconds() <= self.config['execution']['max_quote_age_seconds']:
            return False
        item=self.instruments[identifier]
        return item['kind'] not in {'FUT','OPT'} or item['underlying']==identifier or super().fresh(item['underlying']) and bool(self.quote(item['underlying']).get('received_at')) and 0 <= (at-aware(self.quote(item['underlying'])['received_at'])).total_seconds() <= self.config['execution']['max_quote_age_seconds']

    def place(self, *args, **kwargs):
        order=super().place(*args, **kwargs)
        order['created_cursor']=self.state['cursor']
        self.changed_orders[order['id']]=copy.deepcopy(order)
        return order

    def entry_checks(self, spec, legs, exclude=None):
        checks=super().entry_checks(spec, legs, exclude)
        def check(name, ok, reason, observed=None, limit=None):
            checks.append(dict(name=name, status='PASS' if ok else 'FAIL', reason='Within policy' if ok else reason, observed=observed, limit=limit))
        f=self.state['forward'];at=aware(self.state['clock'])
        span=bounds(self.manifest['calendar'],at.astimezone(ZoneInfo(self.manifest['calendar']['timezone'])).date())
        check('intraday_entry_cutoff',spec['allow_overnight'] or bool(span and at<span[1]-timedelta(minutes=self.policy['intraday_exit_minutes'])),'Intraday exit window blocks new entries')
        for leg in legs:
            item=self.terms(leg['instrument_id'])
            if item['expiry']:check('expiry_exit_buffer',at<aware(item['expiry'])-timedelta(minutes=self.policy['expiry_exit_minutes']),'Derivative expiry exit buffer blocks new entries')
        check('forward_entry_authority', not f['paused'] and not f['retired'] and not f['blockers'], 'Entries paused: '+', '.join(f['blockers']))
        check('active_order_budget', sum(o['status'] in ACTIVE for o in self.state['orders'].values()) < self.policy['max_active_orders'], 'Active order capacity reached')
        recent=sum(aware(o['created_at'])>=at-timedelta(minutes=1) for o in self.state['orders'].values() if not o['reduce_only'])
        recent+=sum(aware(t)>=at-timedelta(minutes=1) for t in f.get('recent_orders',[]))
        check('order_rate', recent < self.policy['max_orders_per_minute'], 'Entry rate limit reached')
        exposures=[]
        for _,identifier,lot in self.lots(): exposures.append((identifier,lot['quantity']))
        for order in self.state['orders'].values():
            if order['id']!=exclude and not order['reduce_only'] and order['status'] in ACTIVE:
                exposures.append((order['instrument_id'],(order['quantity']-order['filled_quantity'])*(1 if order['side']=='BUY' else -1)))
        for leg in legs:
            exposures.append((leg['instrument_id'],leg['quantity']))
            quote=self.quote(leg['instrument_id'])
            spread=(quote['ask']-quote['bid'])/max(.0001,(quote['ask']+quote['bid'])/2)*10000 if quote else math.inf
            check('spread',spread<=self.policy['max_spread_bps'],'Quoted spread exceeds execution policy',spread if math.isfinite(spread) else None,self.policy['max_spread_bps'])
        gamma_loss=theta_loss=vega_loss=0.;underlying={}
        for identifier,quantity in exposures:
            item=self.terms(identifier);q=self.quote(identifier) or {};greek=self.greeks(identifier);spot=self.spot(item)
            if item['kind']=='OPT':
                check('options_estimate_available',greek is not None and self.fresh(item['underlying']),'Fresh underlying and an option sensitivity estimate are required')
                if greek and spot:
                    gamma_loss+=abs(quantity*greek['gamma'])*(.05*spot)**2/2
                    theta_loss+=max(0,-quantity*greek['theta'])
                    vega_loss+=abs(quantity*greek['vega'])*10
            exposure=abs(quantity*(greek['delta'] if greek else 1)*(spot if item['kind']=='OPT' and spot else q.get('close',0)))
            underlying[item['underlying']]=underlying.get(item['underlying'],0)+exposure
        equity=max(1,rupees(self.account()['equity']))
        for name,amount,limit in [('gamma_shock',gamma_loss,self.policy['gamma_shock_loss_pct']),('theta_daily',theta_loss,self.policy['theta_daily_loss_pct']),('vega_ten_points',vega_loss,self.policy['vega_ten_point_loss_pct'])]:
            check(name,amount<=equity*limit/100,'Option sensitivity budget exceeded',amount,equity*limit/100)
        cluster=max(underlying.values(),default=0)
        unknown=[]
        for left in underlying:
            correlated=underlying[left]
            for right in underlying:
                if left==right: continue
                a={r['timestamp']:r['close'] for r in self.state['histories'].get(left,[])[-61:]}
                paired=[(a[r['timestamp']],r['close']) for r in self.state['histories'].get(right,[])[-61:] if r['timestamp'] in a]
                if len(paired)<21:
                    unknown.append((left,right));correlated+=underlying[right];continue
                ra=[b[0]/a_[0]-1 for a_,b in zip(paired,paired[1:])];rb=[b[1]/a_[1]-1 for a_,b in zip(paired,paired[1:])]
                corr=statistics.correlation(ra,rb) if statistics.pstdev(ra) and statistics.pstdev(rb) else 1
                if abs(corr)>=self.policy['correlation_threshold']: correlated+=underlying[right]
            cluster=max(cluster,correlated)
        check('correlated_gross_exposure',cluster<=equity*self.policy['correlated_exposure_pct']/100,'Correlated exposure limit exceeded; unknown correlations are conservatively grouped',cluster,equity*self.policy['correlated_exposure_pct']/100)
        return checks

    def halt_check(self):
        super().halt_check()
        if not self.state['halt'] and self.state['positions']:
            checks=self.entry_checks(next(iter(self.specs.values())),[])
            continuous={'joint_stress','gamma_shock','theta_daily','vega_ten_points','correlated_gross_exposure'}
            failed=[check for check in checks if check['name'] in continuous and check['status']=='FAIL']
            if failed:
                self.state['halt']='Continuous options/portfolio risk threshold breached'
                self.event('RISK_HALT',self.state['halt'],checks=failed,account=self.account_view())
                for owner in self.specs:self.close_book(owner,self.state['halt'])
        if self.state['halt']:self.cancel_entries(self.state['halt'])

    def match(self, rows):
        self.state['orders']=dict(sorted(self.state['orders'].items(),key=lambda pair:(0 if pair[1]['reduce_only'] and pair[1]['side']=='BUY' else 1 if pair[1]['reduce_only'] else 2 if pair[1]['side']=='BUY' else 3)))
        for order in self.state['orders'].values(): order['age']=max(-1,self.state['cursor']-order.get('created_cursor',self.state['cursor'])-1)
        self.used=copy.deepcopy(self.state['forward']['liquidity'])
        before={k:v for k,v in self.state['orders'].items()}
        super().match(rows)
        self.state['forward']['liquidity']=copy.deepcopy(self.used)
        recent=self.state['forward'].setdefault('recent_orders',[])
        recent.extend(o['created_at'] for k,o in before.items() if k not in self.state['orders'] and not o['reduce_only'])
        recent[:]=[t for t in recent if aware(t)>=aware(self.state['clock'])-timedelta(minutes=1)]

    def cancel(self,order,reason):
        if order['status']=='CANCEL_PENDING' and order.get('cancel_at'):return
        super().cancel(order,reason)

    def cancel_entries(self, reason):
        for order in list(self.state['orders'].values()):
            if not order['reduce_only']: self.cancel(order,reason)

    def pause(self, reason):
        self.state['forward']['paused']=True
        self.state['forward']['daily_signals']={}
        self.cancel_entries(reason)
        self.event('PAUSE',reason)

    def process(self, quotes, at, blockers, completed_bars=None):
        self.records=[];self.changed_orders={};self.state['clock']=iso(at);self.phase='FORWARD'
        f=self.state['forward'];previous_tick=f['last_tick'];f['last_tick']=iso(at);f['blockers']=list(blockers)
        zone=ZoneInfo(self.manifest['calendar']['timezone']);day=aware(at).astimezone(zone).date()
        span=bounds(self.manifest['calendar'],day);in_session=bool(span and span[0]<=aware(at)<=span[1])
        if not in_session: f['blockers'].append('MARKET_CLOSED')
        if day.isoformat()>self.document['config']['calendar_valid_until']: f['blockers'].append('CALENDAR_REVIEW_EXPIRED')
        self._day_boundary(day)
        prior_violations=len(self.state['violations'])
        self.apply_actions()
        for reason in self.state['violations'][prior_violations:]:
            obligation=dict(kind='CORPORATE_ACTION',reason=reason)
            if obligation not in self.state['obligations']:self.state['obligations'].append(obligation)
            self.state['halt']='Unresolved corporate-action evidence'
        # Forward orders also age against real elapsed time when candle delivery stalls.
        ttl_seconds=min(86400,self.config['execution']['order_ttl_bars']*self.document['config']['interval_minutes']*60)
        for order in list(self.state['orders'].values()):
            if not order['reduce_only'] and (aware(at)-aware(order['created_at'])).total_seconds()>ttl_seconds:
                self.cancel(order,'Entry remainder expired by forward wall-clock TTL')
            if order.get('cancel_at') and aware(order['cancel_at'])<=aware(at) and order['status'] in ACTIVE:
                order['status']='CANCELLED';self.changed_orders[order['id']]=copy.deepcopy(order)
                self.event('CANCELLED',order['reason'],strategy=order['strategy_id'],order=copy.deepcopy(order))
                del self.state['orders'][order['id']]
        current={}
        for identifier,raw in quotes.items():
            if identifier not in self.instruments: continue
            try:
                row=self.normalize_quote(identifier,raw,at)
            except (ValueError,TypeError,KeyError,OverflowError): continue
            signature=digest({k:row[k] for k in ('timestamp','close','bid','ask','bid_quantity','ask_quantity','volume')})
            old=self.quote(identifier)
            if old and aware(row['timestamp'])<aware(old['timestamp']): continue
            if f['seen'].get(identifier)==signature: continue
            f['seen'][identifier]=signature
            f['liquidity'][identifier+':BUY']=0;f['liquidity'][identifier+':SELL']=0
            self.state['quotes'][identifier]=row;current[identifier]=row
        # Install the complete observation batch before checking derivative references.
        for identifier,row in current.items():
            if in_session and self.fresh(identifier) and self.document['config']['bar_source']=='QUOTE_SAMPLES': self._sample_bar(identifier,row,span)
        needed=set(self.instruments)
        if in_session and not f['paused']:
            f['observed_ticks']+=1
            elapsed=min(5,max(0,(aware(at)-aware(previous_tick)).total_seconds())) if previous_tick else 0
            key=day.isoformat();f.setdefault('observed_seconds',{})[key]=f.get('observed_seconds',{}).get(key,0)+elapsed
            if all(self.fresh(identifier) for identifier in needed) and not f['blockers']:
                f['covered_ticks']+=1
                f.setdefault('covered_seconds',{})[key]=f.get('covered_seconds',{}).get(key,0)+elapsed
                if f['covered_seconds'][key]>=self.policy['minimum_session_minutes']*60 and key not in f['sessions']:f['sessions'].append(key)
            else: f['blockers'].append('STALE_OR_MISSING_QUOTES')
        if f['blockers']: self.cancel_entries('Independent supervision blocks new exposure')
        self.halt_check()
        if in_session and current:
            self.match(current);self.protected_exits(current)
            self.release_daily_signals(at,span)
        bars=list(completed_bars or [])+f.pop('completed_samples',[])
        if self.document['config']['bar_source']=='QUOTE_SAMPLES':
            for identifier,buffer in list(f['buffers'].items()):
                if aware(buffer['timestamp'])<=aware(at): bars.append(buffer);del f['buffers'][identifier]
        self.apply_bars(bars,at)
        for owner,spec in self.specs.items():
            for _,identifier,lot in list(self.lots(book=owner)):
                item=self.terms(identifier)
                expired=item['expiry'] and aware(at)>=aware(item['expiry'])-max(timedelta(days=spec['exit_dte_days']),timedelta(minutes=self.policy['expiry_exit_minutes']))
                intraday=not spec['allow_overnight'] and span and aware(at)>=span[1]-timedelta(minutes=self.policy['intraday_exit_minutes'])
                overdue=not spec['allow_overnight'] and aware(lot['opened']).astimezone(zone).date()<day
                horizon=self.state['cursor']-lot['opened_cursor']>=spec['parameters']['hold_bars']
                if expired or intraday or overdue or horizon:
                    if expired and spec['roll_on_expiry']: self.state['local'][owner]['roll_due']=True
                    self.close_book(owner,'Expiry/roll cutoff' if expired else 'Intraday exit cutoff' if intraday or overdue else 'Maximum holding horizon',identifier,lot['id'])
                if item['expiry'] and aware(at)>=aware(item['expiry']):
                    obligation=dict(instrument_id=identifier,lot_id=lot['id'],quantity=lot['quantity'],reason='Expired exposure requires official settlement/delivery evidence; no synthetic settlement applied')
                    if obligation not in self.state['obligations']:
                        self.state['obligations'].append(obligation);self.event('DELIVERY_OBLIGATION',obligation['reason'],strategy=owner,instrument=identifier)
                    self.state['halt']='Unresolved expiry obligation'
        self.daily_mtm(at,span)
        self.halt_check();account=self.account()
        f['max_drawdown_pct']=max(f.get('max_drawdown_pct',0),account['drawdown_pct'])
        self.state['peak']=max(self.state['peak'],account['equity']);self.state['margin_peak']=max(self.state['margin_peak'],account['margin'])
        if self.state['cash']!=self.state['ledger_total']: raise RuntimeError('Forward cash ledger reconciliation failed')
        if current or bars:
            view=self.account_view();view.update(time=iso(at),cursor=self.state['cursor'],fees=rupees(self.state['fees']),halt=self.state['halt'])
            self.state['last_equity']=view
        return self.records

    def daily_mtm(self,at,span):
        if not span or aware(at)<span[1]:return
        day=span[1].date().isoformat()
        for owner,identifier,lot in list(self.lots()):
            if self.terms(identifier)['kind']!='FUT':continue
            key=day+':'+lot['id'];quote=self.quote(identifier)
            if key in self.state['settlements_done']:continue
            if not quote or not span[1]-timedelta(seconds=30)<=aware(quote['timestamp'])<=span[1]:
                continue
            settlement=paise(quote['close']);pnl=(settlement-lot['price'])*lot['quantity']
            self.ledger(pnl,'ESTIMATED_FUTURES_MTM',owner,instrument_id=identifier,price=rupees(settlement),official=False,source_time=quote['timestamp'])
            self.state['realised'][owner]+=pnl;lot['price']=settlement;self.state['settlements_done'].append(key)
            self.event('FUTURES_MTM','Estimated closing quote mark; official exchange settlement is unavailable',strategy=owner,instrument=identifier,official=False)

    def _day_boundary(self, day):
        key=day.isoformat();week=str(day.isocalendar()[:2])
        if key!=self.state['day']:
            equity=self.account()['equity']
            self.state['day']=key;self.state['day_start']=equity;self.state['dp']=[]
            self.state['unsettled']=[p for p in self.state['unsettled'] if p['date']>key]
            for order in list(self.state['orders'].values()):
                if not order['reduce_only'] and aware(order['created_at']).astimezone(ZoneInfo(self.manifest['calendar']['timezone'])).date()<day: self.cancel(order,'DAY order expired at session boundary')
            if self.state['week']!=week:self.state['week']=week;self.state['week_start']=equity
            self.apply_actions()

    def normalize_quote(self, identifier, raw, at):
        stamp=aware(raw['exchange_time']);received=aware(raw['received_at']) if isinstance(raw['received_at'],str) else __import__('datetime').datetime.fromtimestamp(raw['received_at'],__import__('datetime').timezone.utc)
        if stamp>aware(at) or received>aware(at): raise ValueError('Future market observation')
        last=float(raw['last']);index=self.instruments[identifier]['kind']=='INDEX'
        bid=float(raw.get('bid',last if index else 0));ask=float(raw.get('ask',last if index else 0))
        buy_value=float(raw.get('bid_quantity',0));sell_value=float(raw.get('ask_quantity',0))
        if not all(math.isfinite(v) and v>=0 and v==int(v) for v in (buy_value,sell_value)):raise ValueError('Executable depth must be integral')
        buy=int(buy_value);sell=int(sell_value)
        if any(not math.isfinite(v) or v<=0 for v in (last,bid,ask)) or bid>ask or buy<0 or sell<0: raise ValueError('Invalid executable depth')
        return dict(instrument_id=identifier,timestamp=iso(stamp),open_at=iso(stamp),received_at=iso(received),open=last,high=last,low=last,close=last,
            bid=bid,ask=ask,bid_quantity=buy,ask_quantity=sell,volume=max(0,int(raw.get('cumulative_volume',0))),oi=max(0,int(raw.get('oi',0))),
            underlying_price=None,iv=None,settlement=None,margin_per_lot=None,borrow_quantity=None,borrow_rate=None,halted=False,features_json='{}',transport=raw.get('transport','KITE'))

    def _sample_bar(self, identifier, row, span):
        seconds=self.document['config']['interval_minutes']*60
        index=min(int((aware(row['timestamp'])-span[0]).total_seconds())//seconds,max(0,math.ceil((span[1]-span[0]).total_seconds()/seconds)-1))
        end=min(span[1],span[0]+timedelta(seconds=(index+1)*seconds));f=self.state['forward'];buffer=f['buffers'].get(identifier)
        if buffer and aware(buffer['timestamp'])<end:
            f.setdefault('completed_samples',[]).append(buffer);buffer=None
        if not buffer:
            buffer=dict(instrument_id=identifier,timestamp=iso(end),open_at=iso(span[0]+timedelta(seconds=index*seconds)),open=row['close'],high=row['close'],low=row['close'],close=row['close'],volume=0,source='COALESCED_QUOTE_SAMPLES',samples=0)
            f['buffers'][identifier]=buffer
        buffer.update(high=max(buffer['high'],row['close']),low=min(buffer['low'],row['close']),close=row['close'],samples=buffer['samples']+1)

    def apply_bars(self, bars, available_at, warmup=False):
        f=self.state['forward'];groups={}
        for row in sorted(bars,key=lambda r:(r['timestamp'],r['instrument_id'])):
            identifier=row['instrument_id'];end=aware(row['timestamp'])
            if identifier not in self.instruments or end>aware(available_at): continue
            if identifier in f['bar_watermarks'] and end<=aware(f['bar_watermarks'][identifier]): continue
            if any(not math.isfinite(float(row[k])) or float(row[k])<=0 for k in ('open','high','low','close')): raise ValueError('Invalid completed candle')
            if row['low']>min(row['open'],row['close']) or row['high']<max(row['open'],row['close']): raise ValueError('Invalid completed OHLC bounds')
            history_row=copy.deepcopy(row)
            lookup=getattr(self,'feature_lookup',None)
            if lookup is not None:
                existing=json.loads(history_row.get('features_json') or '{}')
                for name in self.document['config'].get('evidence_features',[]):existing.pop(name,None)
                existing.update(lookup(row['timestamp']));history_row['features_json']=json.dumps(existing,sort_keys=True,separators=(',',':'))
            history=self.state['histories'].setdefault(identifier,[]);history.append(history_row);del history[:-2048]
            f['bar_watermarks'][identifier]=row['timestamp'];groups.setdefault(row['timestamp'],set()).add(identifier)
        if warmup:
            self.event('WARMUP','Completed historical candles loaded as signal history only; no executable quotes or orders were created',bars=sum(len(v) for v in groups.values()))
            return
        # All supplied rows are historical by available_at. Evaluate only the newest group:
        # evaluating earlier groups after loading later history would introduce lookahead.
        if len(groups)>1:self.event('DATA_GAP','Backfilled completed bars updated history; only the newest bar can produce a prospective decision',backfilled_groups=len(groups)-1)
        self.state['cursor']+=max(0,len(groups)-1)
        for stamp,identifiers in list(groups.items())[-1:]:
            self.state['cursor']+=1;f['last_bar']=stamp
            span=bounds(self.manifest['calendar'],aware(stamp).astimezone(ZoneInfo(self.manifest['calendar']['timezone'])).date())
            if not span or not span[0]<aware(stamp)<=span[1]: continue
            if aware(stamp)<aware(self.document['created_at']):continue
            lag=(aware(available_at)-aware(stamp)).total_seconds()
            if lag>max(120,self.document['config']['interval_minutes']*60):
                self.event('DATA_GAP','Missed bars were retained for history; historical signals were not traded',bar_time=stamp,available_at=iso(available_at));continue
            for owner,spec in self.specs.items():
                references=spec['signal_instruments'] or [l['instrument_id'] for l in spec['legs'] if l['instrument_id']] or [spec['legs'][0]['selector']['underlying']]
                if not set(references)<=identifiers:
                    self.event('DATA_GAP','Required signal bars are not aligned; strategy waits',strategy=owner,bar_time=stamp);continue
                if f['signal_times'].get(owner)==stamp:continue
                f['signal_times'][owner]=stamp
                owned=list(self.lots(book=owner));local=self.state['local'][owner]
                outcome=signal(spec,self.state['histories'],owned,stamp,local,self.config['training_end'])
                evidence_inputs={name:value for reference in references for name,value in json.loads((self.state['histories'].get(reference) or [{}])[-1].get('features_json') or '{}').items() if name in self.document['config'].get('evidence_features',[])}
                self.event('SIGNAL',outcome['reason'],strategy=owner,signal=outcome['action'],inputs=outcome['inputs'],evidence_inputs=evidence_inputs,evidence_policy_hash=self.document['config'].get('evidence_policy_hash'),bar_time=stamp,available_at=iso(available_at),strategy_hash=digest(spec),strategy_version=spec['version'])
                if self.document['config']['interval_minutes']==1440 and not f['paused'] and not any(code!='MARKET_CLOSED' for code in f['blockers']) and 'MARKET_CLOSED' in f['blockers']:
                    pending_outcome=outcome
                    if local.get('roll_due'):pending_outcome=dict(outcome,action='ENTER',direction=local.get('last_direction',1))
                    if pending_outcome['action'] in {'ENTER','REBALANCE','REVERSE'}:
                        f.setdefault('daily_signals',{})[owner]=dict(bar_time=stamp,outcome=copy.deepcopy(pending_outcome))
                        self.event('NEXT_SESSION_SIGNAL','Completed daily signal awaits the next eligible session and fresh executable quotes',strategy=owner,bar_time=stamp)
                        continue
                pending=any(o['strategy_id']==owner and o['status'] in ACTIVE for o in self.state['orders'].values())
                if outcome['action']=='EXIT':self.close_book(owner,outcome['reason'])
                elif outcome['action']=='CANCEL':
                    for order in list(self.state['orders'].values()):
                        if order['strategy_id']==owner:self.cancel(order,outcome['reason'])
                elif outcome['action']=='REVERSE':local['reversal_due']=outcome['direction'];self.close_book(owner,outcome['reason'])
                elif not pending and not self.state['halt'] and not f['paused'] and not f['blockers']:
                    if local.get('roll_due') and not owned:
                        if self.enter(spec,local.get('last_direction',1)):local['roll_due']=False
                    elif local.get('reversal_due') and not owned:
                        if self.enter(spec,local['reversal_due']):local['reversal_due']=None
                    elif outcome['action']=='REBALANCE':
                        if owned:self.close_book(owner,'Ranking rebalance; reduce old exposure first')
                        else:self.enter(spec,1,outcome.get('selected'))
                    elif outcome['action']=='ENTER' and (not owned or spec['family']=='SCHEDULED'):self.enter(spec,outcome.get('direction',1))
            if self.config['benchmark_id']:
                rows=self.state['histories'].get(self.config['benchmark_id'],[])
                if rows:
                    if f['benchmark_start'] is None:f['benchmark_start']=rows[-1]['close']
                    f['benchmark_value']=self.config['capital']*rows[-1]['close']/f['benchmark_start']
            self.event('EQUITY','Prospective account mark after completed-bar decisions',account=self.account_view(),bar_time=stamp)

    def release_daily_signals(self,at,span):
        f=self.state['forward']
        if f['paused'] or f['blockers'] or self.state['halt'] or not span:return
        zone=ZoneInfo(self.manifest['calendar']['timezone']);today=aware(at).astimezone(zone).date()
        for owner,item in list(f.get('daily_signals',{}).items()):
            day=aware(item['bar_time']).astimezone(zone).date()+timedelta(days=1)
            while not bounds(self.manifest['calendar'],day):day+=timedelta(days=1)
            if today<day:continue
            if today>day or aware(at)>span[0]+timedelta(seconds=self.policy['next_session_signal_window_seconds']):
                self.event('SKIP','Next-session daily signal window expired; old instructions were not traded',strategy=owner,bar_time=item['bar_time']);del f['daily_signals'][owner];continue
            if any(o['strategy_id']==owner and o['status'] in ACTIVE for o in self.state['orders'].values()):continue
            spec=self.specs[owner];outcome=item['outcome'];owned=list(self.lots(book=owner))
            if outcome['action'] in {'REBALANCE','REVERSE'} and owned:
                self.close_book(owner,'Next-session reduction before rebalance/reversal');continue
            if owned:del f['daily_signals'][owner];continue
            self.event('NEXT_SESSION_RELEASE','Daily decision released against current session observations and independent risk',strategy=owner,bar_time=item['bar_time'],released_at=iso(at))
            self.enter(spec,outcome.get('direction',1),outcome.get('selected') if outcome['action']=='REBALANCE' else None)
            self.state['local'][owner]['roll_due']=False
            del f['daily_signals'][owner]

    def stress_grid(self):
        result=[]
        for move,vol_shift,days in [(-.2,.2,1),(-.1,.1,1),(-.05,0,1),(.05,0,1),(.1,.1,1),(.2,.2,1),(0,-.1,3),(0,.2,1)]:
            pnl=0.;unknown=[]
            for _,identifier,lot in self.lots():
                item=self.terms(identifier);quantity=lot['quantity'];mark=rupees(self.mark(identifier,quantity));spot=self.spot(item)
                if item['kind']!='OPT':pnl+=quantity*mark*move;continue
                greek=self.greeks(identifier)
                if not greek or not spot:unknown.append(identifier);continue
                years=max(0,(aware(item['expiry'])-aware(self.state['clock'])).total_seconds()/(365*86400)-days/365)
                value=option_value(spot*(1+move),item['strike'],years,max(.01,greek['iv']/100+vol_shift),item['option_type'],self.config['execution']['interest_rate'],self.config['execution']['dividend_yield'])
                pnl+=quantity*(value-mark)
            result.append(dict(spot_move_pct=move*100,iv_shift_points=vol_shift*100,elapsed_days=days,pnl=pnl,unknown=sorted(set(unknown)),model='Estimated European repricing; no liquidity/portfolio-margin guarantee'))
        return result
