"""Deterministic portfolio replay. All market/strategy state uses the injected event clock."""
import copy
import json
import math
from datetime import timedelta
from zoneinfo import ZoneInfo

from ..charges import estimate
from ..execution import PaperExecutionAdapter
from ..finance import paise, rupees, tick_price, option_value, option_greeks, implied_volatility
from ..risk import budget_checks, fill_cash_delta
from .catalog import aware, iso, canonical, digest, bounds, settlement_date
from .signals import signal


class Replay:
    def __init__(self,dataset,config,emit,state=None):
        self.dataset,self.config,self.emit=dataset,config,emit
        self.manifest=dataset['manifest']
        self.instruments={i['id']:copy.deepcopy(i) for i in self.manifest['instruments']}
        self.specs={s['id']:s for s in config['strategies']}
        self.adapter=PaperExecutionAdapter()
        capital=paise(config['capital'])
        self.state=state or dict(cursor=0,clock=None,cash=capital,ledger_total=capital,capital=capital,peak=capital,
            day_start=capital,week_start=capital,day=None,week=None,halt=None,sequence=0,
            quotes={},histories={},positions={},orders={},local={s['id']:{} for s in config['strategies']},
            fees=0,realised={s['id']:0 for s in config['strategies']},fee_by_book={s['id']:0 for s in config['strategies']},
            actions_done=[],settlements_done=[],unsettled=[],dividends=[],dp=[],violations=[],
            obligations=[],post_train_count=0,filled_orders=0,rejected_orders=0,submitted_orders=0,turnover=0,closed_wins=0,closed_losses=0,
            benchmark=None,last_equity=None,margin_peak=0,slippage_cost=0,ambiguities=0)
        self.phase='CLOSE'
        self.used={}
        self.changed_orders={}
        self.records=[]

    def event(self,kind,reason,strategy=None,instrument=None,**details):
        self.state['sequence']+=1
        instrument=details.pop('instrument_id',instrument)
        record=dict(sequence=self.state['sequence'],time=self.state['clock'],phase=self.phase,kind=kind,
            reason=reason,strategy_id=strategy,instrument_id=instrument,**details)
        self.records.append(record)
        return record

    def ledger(self,amount,kind,book=None,**details):
        self.state['cash']+=amount
        self.state['ledger_total']+=amount
        self.event('LEDGER',kind,strategy=book,amount=rupees(amount),balance=rupees(self.state['cash']),**details)

    def key(self,book,instrument): return book+'\x1f'+instrument

    def terms(self,identifier):
        item=copy.deepcopy(self.instruments[identifier])
        for term in item['terms']:
            if aware(term['effective_at'])<=aware(self.state['clock']): item.update(term)
        return item

    def lots(self,book=None,identifier=None):
        for key,position in self.state['positions'].items():
            owner,asset=key.split('\x1f')
            if (book is None or book==owner) and (identifier is None or identifier==asset):
                for lot in position['lots']: yield owner,asset,lot

    def quote(self,identifier): return self.state['quotes'].get(identifier)

    def fresh(self,identifier):
        quote=self.quote(identifier)
        return bool(quote and 0<quote['bid']<=quote['ask'] and 0<=
            (aware(self.state['clock'])-aware(quote['timestamp'])).total_seconds()<=self.config['execution']['max_quote_age_seconds'])

    def mark(self,identifier,quantity):
        quote=self.quote(identifier)
        return paise(quote['bid'] if quantity>0 else quote['ask']) if quote else 0

    def spot(self,item):
        quote=self.quote(item['underlying'])
        if quote: return quote['close']
        own=self.quote(item['id']) or {}
        return own.get('underlying_price')

    def greeks(self,identifier):
        item=self.terms(identifier)
        if item['kind']!='OPT': return None
        quote=self.quote(identifier);spot=self.spot(item)
        years=(aware(item['expiry'])-aware(self.state['clock'])).total_seconds()/(365*86400)
        if not quote or not spot or years<=0: return None
        iv=quote.get('iv') or implied_volatility((quote['bid']+quote['ask'])/2,spot,item['strike'],years,item['option_type'],self.config['execution']['interest_rate'],self.config['execution']['dividend_yield'])
        if not iv: return None
        return option_greeks(spot,item['strike'],years,iv,item['option_type'],self.config['execution']['interest_rate'],self.config['execution']['dividend_yield'])

    def margin(self,identifier,quantity):
        item=self.terms(identifier);quote=self.quote(identifier) or {}
        if item['kind']=='EQ' and quantity<0: return paise(quote.get('close',0)*abs(quantity))
        if item['kind'] not in {'FUT','OPT'} or (item['kind']=='OPT' and quantity>=0): return 0
        if self.config['execution']['margin_model']=='HISTORICAL_GROSS':
            amount=quote.get('margin_per_lot')
            if amount is None: raise ValueError('Historical margin observation missing for '+identifier)
            return paise(amount*abs(quantity)/item['lot_size'])
        reference=quote.get('close',0) if item['kind']=='FUT' else self.spot(item)
        if reference is None: raise ValueError('Underlying spot is required for short-option margin')
        rate=self.config['execution']['futures_margin_rate'] if item['kind']=='FUT' else self.config['execution']['short_option_margin_rate']
        for term in item['terms']:
            if aware(term['effective_at'])<=aware(self.state['clock']): rate=term['margin_rate']
        return paise(reference*abs(quantity)*rate)

    def planned_risk(self):
        return sum(max(0,lot['quantity']*(lot['price']-lot['stop'])) for _,_,lot in self.lots())

    def reserve(self,exclude=None,book=None):
        funding=planned=0
        for order in self.state['orders'].values():
            if order['id']==exclude or order['reduce_only'] or order['status'] not in {'PENDING_ACK','OPEN','PARTIAL','CANCEL_PENDING'} or (book and order['strategy_id']!=book): continue
            remaining=(order['quantity']-order['filled_quantity'])*(1 if order['side']=='BUY' else -1)
            item=self.terms(order['instrument_id']);q=self.quote(item['id'])
            if not q: continue
            price=order['limit_price'] if order['order_type']=='LIMIT' else q['ask'] if remaining>0 else q['bid']
            needed=self.margin(item['id'],remaining) if item['kind']=='FUT' or remaining<0 else paise(price*remaining)
            funding+=needed+paise(estimate(item['kind'],order['side'],price*abs(remaining),product=order['product'],profile=self.fee_profile())['total'])
            planned+=paise(price*abs(remaining)*order['stop_pct']/100)
        return funding,planned

    def stress(self,extra=None):
        exposures=[(identifier,lot['quantity'],self.mark(identifier,lot['quantity'])/100) for _,identifier,lot in self.lots()]
        for order in self.state['orders'].values():
            if not order['reduce_only'] and order['status'] in {'PENDING_ACK','OPEN','PARTIAL','CANCEL_PENDING'}:
                qty=(order['quantity']-order['filled_quantity'])*(1 if order['side']=='BUY' else -1)
                if not extra or order['id']!=extra.get('exclude'):
                    q=self.quote(order['instrument_id'])
                    if q: exposures.append((order['instrument_id'],qty,q['ask'] if qty>0 else q['bid']))
        if extra:
            for leg in extra.get('legs',[]): exposures.append((leg['instrument_id'],leg['quantity'],leg['price']))
        groups={}
        for identifier,qty,mark in exposures:
            item=self.terms(identifier);groups.setdefault(item['underlying'],[]).append((item,qty,mark))
        result=0
        for positions in groups.values():
            worst=0
            for move in (-.2,-.1,-.05,.05,.1,.2):
                for vol_shift in (-.1,.1):
                    pnl=0
                    for item,qty,mark in positions:
                        if item['kind']!='OPT': pnl+=qty*mark*move;continue
                        greek=self.greeks(item['id']);spot=self.spot(item)
                        if not greek or not spot:
                            pnl-=abs(qty)*mark*(3 if qty<0 else 1);continue
                        years=max(0,(aware(item['expiry'])-aware(self.state['clock'])).total_seconds()/(365*86400)-1/365)
                        stressed=option_value(spot*(1+move),item['strike'],years,max(.01,greek['iv']/100+vol_shift),item['option_type'],self.config['execution']['interest_rate'],self.config['execution']['dividend_yield'])
                        pnl+=qty*(stressed-mark)
                    worst=min(worst,pnl)
            result+=-worst
        return paise(result)

    def account(self):
        equity=self.state['cash']+sum(d['amount'] for d in self.state['dividends'])
        unrealised=margin=gross=net=0
        underlying={};books={s:dict(unrealised=0,committed=0) for s in self.specs}
        futures_loss=0
        for owner,identifier,lot in self.lots():
            item=self.terms(identifier);quantity=lot['quantity'];mark=self.mark(identifier,quantity)
            unreal=(mark-lot['price'])*quantity
            unrealised+=unreal;books[owner]['unrealised']+=unreal
            equity+=unreal if item['kind']=='FUT' else mark*quantity
            if item['kind']=='FUT': futures_loss+=min(0,unreal)
            requirement=self.margin(identifier,quantity)
            margin+=requirement
            books[owner]['committed']+=requirement if item['kind']=='FUT' or quantity<0 else max(0,lot['price']*quantity)
            spot=self.spot(item) or mark/100
            greek=self.greeks(identifier)
            exposure=quantity*(greek['delta'] if greek else 1)*(spot if item['kind']=='OPT' else mark/100)
            gross+=abs(exposure);net+=exposure
            group=underlying.setdefault(item['underlying'],dict(net=0.,gross=0.,delta=0.,gamma=0.,theta=0.,vega=0.))
            group['net']+=exposure;group['gross']+=abs(exposure)
            group['delta']+=quantity*(greek['delta'] if greek else 1)
            if greek:
                for name in ('gamma','theta','vega'): group[name]+=greek[name]*quantity
        reserved,reserved_risk=self.reserve()
        unsettled=sum(p['amount'] for p in self.state['unsettled'])
        stale=sorted({identifier for _,identifier,_ in self.lots() if not self.fresh(identifier)})
        return dict(stale_instruments=stale,cash=self.state['cash'],equity=equity,unrealised=unrealised,realised_net=equity-self.state['capital']-unrealised,
            margin=margin,reserved=reserved,available=self.state['cash']+futures_loss-margin-reserved-unsettled,
            unsettled=unsettled,planned_risk=self.planned_risk()+reserved_risk,gross=paise(gross),net=paise(net),
            underlying=underlying,books=books,drawdown_pct=max(0,(self.state['peak']-equity)/max(1,self.state['peak'])*100))

    def fee_profile(self):
        matches=[r for r in self.manifest['fee_rules'] if aware(r['effective_from'])<=aware(self.state['clock'])]
        if not matches: raise ValueError('No fee rules effective at replay time')
        return matches[-1]

    def resolve(self,leg,exit_dte=0):
        if leg['instrument_id']: return leg['instrument_id']
        selector=leg['selector'];at=aware(self.state['clock'])
        eligible=[]
        for identifier,item in self.instruments.items():
            if item['kind']!=selector['kind'] or item['underlying']!=selector['underlying'] or not self.fresh(identifier): continue
            if selector['kind']=='OPT' and item['option_type']!=selector['option_type']: continue
            dte=(aware(item['expiry'])-at).total_seconds()/86400
            if selector['min_dte']<=dte<=selector['max_dte'] and dte>exit_dte: eligible.append(item)
        expiries=sorted({i['expiry'] for i in eligible})
        if len(expiries)<=selector['expiry_rank']: return None
        eligible=[i for i in eligible if i['expiry']==expiries[selector['expiry_rank']]]
        if selector['kind']=='FUT': return sorted(eligible,key=lambda i:i['id'])[0]['id']
        spot=self.spot(eligible[0]) if eligible else None
        if not spot: return None
        eligible.sort(key=lambda i:i['strike'])
        if selector['target_delta'] is not None:
            usable=[(abs(abs(self.greeks(i['id'])['delta'])-selector['target_delta']),i['id']) for i in eligible if self.greeks(i['id'])]
            return min(usable)[1] if usable else None
        index=min(range(len(eligible)),key=lambda j:abs(eligible[j]['strike']-spot))+selector['strike_offset']
        return eligible[index]['id'] if 0<=index<len(eligible) else None

    def entry_checks(self,spec,legs,exclude=None):
        account=self.account();risk=self.config['risk'];checks=[]
        def check(name,ok,reason,observed=None,limit=None):
            checks.append(dict(name=name,status='PASS' if ok else 'FAIL',reason='Within policy' if ok else reason,observed=observed,limit=limit))
        check('account_halt',not self.state['halt'],self.state['halt'] or 'Account halted')
        check('portfolio_freshness',not account['stale_instruments'],'Existing portfolio valuations are stale: '+', '.join(account['stale_instruments']))
        needed=planned=0;future_margin=0
        extra_exposure={};gross=0
        for leg in legs:
            identifier=leg['instrument_id'];item=self.terms(identifier);quantity=leg['quantity'];price=leg['price'];q=self.quote(identifier)
            check('whole_lots',quantity!=0 and abs(quantity)%item['lot_size']==0,'Quantity must align with dated contract lot size')
            check('fresh_data',self.fresh(identifier),'No fresh executable observation for '+identifier)
            check('instrument_type',item['kind']!='INDEX','An index reference cannot be traded')
            at=aware(self.state['clock'])
            check('listed',not item['listed_at'] or at>=aware(item['listed_at']),'Instrument not yet listed')
            check('delisted',not item['delisted_at'] or at<aware(item['delisted_at']),'Instrument has delisted')
            if item['expiry']: check('expiry',at<aware(item['expiry'])-timedelta(days=spec['exit_dte_days']),'Derivative expiry/exit cutoff reached')
            if item['kind']=='EQ' and quantity<0 and leg['product']!='INTRADAY':
                borrowed=sum(abs(l['quantity']) for _,asset,l in self.lots(identifier=identifier) if l['quantity']<0)
                check('borrow',bool(q) and (q.get('borrow_quantity') or 0)>=borrowed+abs(quantity),'Overnight stock short requires dated available borrow')
            if item['kind']=='OPT' and quantity<0:
                check('underlying',bool(self.spot(item)),'Short options require an observed underlying price')
                if not self.config['execution']['allow_uncovered_options']:
                    cover=sum(l['quantity'] for _,asset,l in self.lots(book=spec['id']) if self.terms(asset)['kind']=='OPT' and self.terms(asset)['underlying']==item['underlying'] and self.terms(asset)['option_type']==item['option_type'] and aware(self.terms(asset)['expiry'])>=aware(item['expiry']))
                    if item['option_type']=='CE': cover+=sum(l['quantity'] for _,asset,l in self.lots(book=spec['id'],identifier=item['underlying']) if l['quantity']>0)
                    # An accepted basket may reserve a hedge; execution rechecks actual filled protection.
                    cover+=sum(p['quantity'] for p in legs if p['quantity']>0 and self.terms(p['instrument_id'])['underlying']==item['underlying'] and (self.terms(p['instrument_id'])['kind']=='EQ' and item['option_type']=='CE' or self.terms(p['instrument_id'])['kind']=='OPT' and self.terms(p['instrument_id'])['option_type']==item['option_type'] and aware(self.terms(p['instrument_id'])['expiry'])>=aware(item['expiry'])))
                    check('hedge_ready',cover>=abs(quantity),'Short option leg waits for sufficient filled protection')
            cost=paise(estimate(item['kind'],'BUY' if quantity>0 else 'SELL',price*abs(quantity),product=leg['product'],profile=self.fee_profile())['total'])
            exit_cost=paise(estimate(item['kind'],'SELL' if quantity>0 else 'BUY',price*abs(quantity),product=leg['product'],profile=self.fee_profile())['total'])
            requirement=self.margin(identifier,quantity)
            needed+=(requirement if item['kind']=='FUT' or quantity<0 else paise(price*quantity))+cost+exit_cost
            future_margin+=requirement
            planned+=paise(price*abs(quantity)*(spec['stop_pct']/100+2*risk['slippage_bps']/10000))+cost+exit_cost
            greek=self.greeks(identifier);notional=(self.spot(item) or price) if item['kind']=='OPT' else price
            exposure=quantity*(greek['delta'] if greek else 1)*notional
            extra_exposure[item['underlying']]=extra_exposure.get(item['underlying'],0)+exposure
            gross+=abs(exposure)
        own_reserved,own_risk=self.reserve(exclude=exclude)
        excluded=self.state['orders'].get(exclude)
        excluded_funding=excluded_risk=0
        if excluded:
            all_funding,all_risk=self.reserve()
            excluded_funding=all_funding-own_reserved;excluded_risk=all_risk-own_risk
        book_funding=self.reserve(book=spec['id'],exclude=exclude)[0]
        committed=account['books'][spec['id']]['committed']
        check('allocation',needed+book_funding+committed<=account['equity']*spec['allocation_pct']/100,'Strategy allocation is exhausted',rupees(needed+book_funding+committed),rupees(round(account['equity']*spec['allocation_pct']/100)))
        checks.extend(budget_checks(equity=account['equity'],trade_risk=planned,open_risk=account['planned_risk']-excluded_risk+planned,
            funding_needed=needed,funding_available=account['available']+excluded_funding-round(account['equity']*risk['cash_buffer_pct']/100),
            margin=account['margin']+future_margin+sum(self.margin(o['instrument_id'],(o['quantity']-o['filled_quantity'])*(1 if o['side']=='BUY' else -1)) for o in self.state['orders'].values() if o['id']!=exclude and not o['reduce_only'] and o['status'] in {'PENDING_ACK','OPEN','PARTIAL','CANCEL_PENDING'}),stress=self.stress(dict(legs=legs,exclude=exclude)),risk=risk))
        check('gross_exposure',account['gross']+paise(gross)<=account['equity']*risk['gross_exposure_pct']/100,'Gross account exposure limit exceeded')
        for underlying,extra in extra_exposure.items():
            current=account['underlying'].get(underlying,{}).get('net',0)
            check('underlying_concentration',paise(abs(current+extra))<=account['equity']*risk['underlying_exposure_pct']/100,'Underlying exposure limit exceeded for '+underlying)
        return checks

    def place(self,spec,identifier,quantity,reason,group=None,reduce_only=False,lot_id=None,order_type=None,limit_price=None,product=None):
        self.state['submitted_orders']+=1
        order_id='O'+str(self.state['sequence']+1)
        self.event('ORDER_INTENT',reason,strategy=spec['id'],instrument=identifier,order_id=order_id)
        item=self.terms(identifier)
        product=product or ('DERIVATIVE' if item['kind'] in {'FUT','OPT'} else 'DELIVERY' if spec['allow_overnight'] else 'INTRADAY')
        q=self.quote(identifier)
        side='BUY' if quantity>0 else 'SELL'
        kind=order_type or spec['order_type']
        if kind=='LIMIT' and limit_price is None and q:
            reference=q['ask'] if quantity>0 else q['bid']
            limit_price=tick_price(reference*(1+(-1 if quantity>0 else 1)*spec['limit_offset_bps']/10000),item['tick_size'],side)
        order=dict(id=order_id,strategy_id=spec['id'],strategy_version=spec['version'],strategy_hash=digest(spec),
            instrument_id=identifier,symbol=identifier,side=side,quantity=abs(quantity),filled_quantity=0,fill_notional=0,fees=0,
            order_type=kind,limit_price=limit_price,reduce_only=reduce_only,lot_id=lot_id,group_id=group,
            status='PENDING_ACK',created_at=self.state['clock'],active_at=iso(aware(self.state['clock'])+timedelta(seconds=self.config['execution']['latency_seconds'])),
            age=0,reason=reason,product=product,stop_pct=spec['stop_pct'],target_multiple=spec['target_multiple'])
        self.state['orders'][order_id]=order;self.changed_orders[order_id]=copy.deepcopy(order)
        return order

    def enter(self,spec,direction=1,selected=None):
        legs=[]
        for definition in spec['legs']:
            identifier=self.resolve(definition,spec['exit_dte_days'])
            if not identifier or not self.fresh(identifier):
                self.event('SKIP','No eligible fresh contract for a strategy leg',strategy=spec['id'],selector=definition);return
            if selected is not None and identifier not in selected: continue
            item=self.terms(identifier);q=self.quote(identifier)
            product=definition['product'] if definition['product']!='AUTO' else 'DERIVATIVE' if item['kind'] in {'FUT','OPT'} else 'DELIVERY' if spec['allow_overnight'] else 'INTRADAY'
            quantity=definition['ratio']*direction*item['lot_size']
            legs.append(dict(instrument_id=identifier,quantity=quantity,price=q['ask'] if quantity>0 else q['bid'],product=product))
        if not legs: return
        if len({l['instrument_id'] for l in legs})!=len(legs):
            self.event('SKIP','Multiple basket legs resolve to the same contract; supply distinct selectors',strategy=spec['id']);return
        max_lots=min(100000,min(10000000//abs(l['quantity']) for l in legs))
        def scaled(count): return [dict(l,quantity=l['quantity']*count) for l in legs]
        if spec['size_mode']=='LOTS': lots=min(spec['lots'],max_lots)
        else:
            low,high=0,max_lots
            while low<high:
                mid=(low+high+1)//2
                if all(c['status']=='PASS' for c in self.entry_checks(spec,scaled(mid))): low=mid
                else: high=mid-1
            lots=low
        candidate=scaled(max(1,lots));checks=self.entry_checks(spec,candidate)
        if not lots or any(c['status']=='FAIL' for c in checks):
            self.state['rejected_orders']+=1
            self.event('REJECT','No eligible whole-lot basket within policy',strategy=spec['id'],checks=checks,sizing=dict(lots=lots,requested=spec['lots'],mode=spec['size_mode']));return
        self.state['local'][spec['id']]['last_direction']=direction
        group='G'+str(self.state['sequence']+1)
        self.event('ACCEPT','Basket passes shared capital and risk checks',strategy=spec['id'],group_id=group,
            checks=checks,sizing=dict(lots=lots,mode=spec['size_mode'],allocation_pct=spec['allocation_pct']),known_quotes=copy.deepcopy(self.state['quotes']))
        # Positive hedges submit first. Their fills must exist before protected shorts execute.
        for leg in sorted(scaled(lots),key=lambda l:(l['quantity']<0,l['instrument_id'])):
            self.place(spec,leg['instrument_id'],leg['quantity'],'Strategy basket entry',group=group,product=leg['product'])
        return True

    def cancel(self,order,reason):
        if order['status'] in {'FILLED','CANCELLED','REJECTED'}: return
        latency=self.config['execution']['cancel_latency_seconds']
        order['cancel_at']=iso(aware(self.state['clock'])+timedelta(seconds=latency))
        order['status']='CANCEL_PENDING' if latency else 'CANCELLED';order['reason']=reason
        self.changed_orders[order['id']]=copy.deepcopy(order)
        self.event('CANCEL_REQUEST' if latency else 'CANCELLED',reason,strategy=order['strategy_id'],instrument=order['instrument_id'],order=copy.deepcopy(order))

    def close_book(self,book,reason,identifier=None,lot_id=None):
        for order in list(self.state['orders'].values()):
            if order['strategy_id']==book and not order['reduce_only'] and (identifier is None or order['instrument_id']==identifier): self.cancel(order,reason)
        for owner,asset,lot in list(self.lots(book=book,identifier=identifier)):
            if lot_id and lot['id']!=lot_id: continue
            reserved=sum((o['quantity']-o['filled_quantity']) for o in self.state['orders'].values() if o['strategy_id']==book and o['instrument_id']==asset and o['reduce_only'] and o['status'] in {'PENDING_ACK','OPEN','PARTIAL','CANCEL_PENDING'} and (o['lot_id'] is None or o['lot_id']==lot['id']))
            remaining=max(0,abs(lot['quantity'])-reserved)
            if remaining: self.place(self.specs[book],asset,-remaining if lot['quantity']>0 else remaining,reason,reduce_only=True,lot_id=lot['id'],order_type='MARKET',product=lot['product'])

    def fill(self,order,count,price,reference):
        identifier=order['instrument_id'];item=self.terms(identifier);book=order['strategy_id']
        signed=count*(1 if order['side']=='BUY' else -1);price_paise=paise(price)
        cumulative=order['fill_notional']+price_paise*count
        charge=estimate(item['kind'],order['side'],rupees(cumulative),product=order['product'],profile=self.fee_profile())
        previous_fee=paise(estimate(item['kind'],order['side'],rupees(order['fill_notional']),product=order['product'],profile=self.fee_profile())['total']) if order['fill_notional'] else 0
        incremental_fee=paise(charge['total'])-previous_fee
        fee=incremental_fee
        dp_key=self.state['day']+':'+identifier
        if item['kind']=='EQ' and signed<0 and order['product']=='DELIVERY' and dp_key not in self.state['dp']:
            fee+=paise(self.fee_profile()['dp_fee']);self.state['dp'].append(dp_key);order['dp_fee']=paise(self.fee_profile()['dp_fee'])
        key=self.key(book,identifier);position=self.state['positions'].setdefault(key,dict(lots=[]))
        realised=0;remaining=signed
        for lot in list(position['lots']):
            if remaining*lot['quantity']>=0 or (order['lot_id'] and order['lot_id']!=lot['id']): continue
            closing=min(abs(remaining),abs(lot['quantity']))
            pnl=(price_paise-lot['price'])*closing*(1 if lot['quantity']>0 else -1)
            realised+=pnl
            self.state['closed_wins']+=int(pnl>0);self.state['closed_losses']+=int(pnl<=0)
            lot['quantity']+=closing if lot['quantity']<0 else -closing
            remaining+=closing if remaining<0 else -closing
            if not lot['quantity']: position['lots'].remove(lot)
            if not remaining: break
        if remaining and order['reduce_only']: raise RuntimeError('Reduce-only fill would reverse exposure')
        if remaining:
            distance=paise(price*order['stop_pct']/100)
            position['lots'].append(dict(id=order['id']+':'+str(order['filled_quantity']),quantity=remaining,price=price_paise,
                stop=price_paise-distance if remaining>0 else price_paise+distance,
                target=(price_paise+round(distance*order['target_multiple'])*(1 if remaining>0 else -1)) if order['target_multiple'] else None,
                opened=self.state['clock'],opened_cursor=self.state['cursor'],product=order['product'],group_id=order['group_id']))
        if not position['lots']: del self.state['positions'][key]
        delta=fill_cash_delta(item['kind'],signed,price_paise,fee,realised)
        self.ledger(delta,'FILL',book,order_id=order['id'])
        if item['kind']=='EQ' and signed<0 and order['product']=='DELIVERY':
            self.state['unsettled'].append(dict(amount=max(0,price_paise*count-fee),date=settlement_date(self.manifest['calendar'],self.state['clock'])))
        self.state['realised'][book]+=realised-fee
        self.state['fees']+=fee;self.state['fee_by_book'][book]+=fee;self.state['turnover']+=price_paise*count
        self.state['slippage_cost']+=paise((price-reference)*signed)
        order.update(filled_quantity=order['filled_quantity']+count,fill_notional=cumulative,fees=order['fees']+incremental_fee)
        order['status']='FILLED' if order['filled_quantity']==order['quantity'] else 'PARTIAL'
        if order['status']=='FILLED': self.state['filled_orders']+=1
        self.changed_orders[order['id']]=copy.deepcopy(order)
        self.event('FILL','Simulated fill; liquidity and price assumptions recorded',strategy=book,instrument=identifier,order_id=order['id'],
            group_id=order['group_id'],quantity=signed,price=price,reference=reference,fees=rupees(fee),realised=rupees(realised),
            delay_seconds=max(0,(aware(self.state['clock'])-aware(order['created_at'])).total_seconds()),
            quote=copy.deepcopy(self.quote(identifier)),account=self.account_view(),order=copy.deepcopy(order))

    def order_eligible(self,order,rows):
        return True

    def strategy_exits_enabled(self,spec):
        return True

    def match(self,rows):
        for order in list(self.state['orders'].values()):
            if order['status'] not in {'PENDING_ACK','OPEN','PARTIAL','CANCEL_PENDING'}: continue
            if order.get('cancel_at') and aware(self.state['clock'])>=aware(order['cancel_at']):
                order['status']='CANCELLED';self.changed_orders[order['id']]=copy.deepcopy(order)
                self.event('CANCELLED',order['reason'],strategy=order['strategy_id'],order=copy.deepcopy(order));continue
            identifier=order['instrument_id']
            if not self.order_eligible(order,rows):continue
            if identifier not in rows or aware(order['active_at'])>aware(self.state['clock']): continue
            if order['status']=='PENDING_ACK':
                order['status']='OPEN';self.event('ACK','Simulator acknowledged the order',strategy=order['strategy_id'],order=copy.deepcopy(order))
            order['age']+=1
            if order['age']>self.config['execution']['order_ttl_bars'] and not order['reduce_only']:
                self.cancel(order,'Entry remainder expired');continue
            if identifier in self.config['execution']['forced_reject_legs'] and not order['reduce_only']:
                order.update(status='REJECTED',reason='Configured rejection sensitivity scenario')
                self.state['rejected_orders']+=1;self.changed_orders[order['id']]=copy.deepcopy(order)
                self.event('REJECT',order['reason'],strategy=order['strategy_id'],order=copy.deepcopy(order))
                self.close_book(order['strategy_id'],'A basket leg was rejected; unwind surviving exposure');continue
            item=self.terms(identifier);quote=self.quote(identifier);row=rows[identifier]
            if not quote or row['halted'] or not self.fresh(identifier):
                self.event('WAIT','Halted or stale executable observation',strategy=order['strategy_id'],order=copy.deepcopy(order));continue
            if self.manifest['frequency']=='QUOTE':
                liquidity_key=identifier+':'+order['side']
            else: liquidity_key=identifier
            proposal=self.adapter.propose(order,item,quote,self.config['risk']['slippage_bps'],self.used.get(liquidity_key,0))
            count=proposal.quantity
            if order['reduce_only']:
                available=sum(abs(l['quantity']) for _,_,l in self.lots(book=order['strategy_id'],identifier=identifier) if (not order['lot_id'] or l['id']==order['lot_id']) and (l['quantity']>0)!=(order['side']=='BUY'))
                count=min(count,available)
            if not count:
                self.event('WAIT',proposal.reason if not order['reduce_only'] else 'Exit waits for executable whole-lot depth',strategy=order['strategy_id'],order=copy.deepcopy(order));continue
            if not order['reduce_only']:
                leg=dict(instrument_id=identifier,quantity=count*(1 if order['side']=='BUY' else -1),price=proposal.price,product=order['product'])
                checks=self.entry_checks(self.specs[order['strategy_id']],[leg],exclude=order['id'])
                if any(c['status']=='FAIL' for c in checks):
                    self.event('WAIT_RISK','Entry recheck did not pass; remainder stays pending until TTL/cancellation',strategy=order['strategy_id'],order_id=order['id'],checks=checks);continue
            else: checks=[dict(name='reduce_only',status='PASS',reason='Fill reduces existing owned exposure')]
            self.event('EXECUTION_CHECK','Shared controls checked immediately before fill',strategy=order['strategy_id'],order_id=order['id'],checks=checks)
            self.fill(order,count,proposal.price,proposal.reference)
            self.used[liquidity_key]=self.used.get(liquidity_key,0)+count
        for identifier in [key for key,o in self.state['orders'].items() if o['status'] in {'FILLED','CANCELLED','REJECTED'}]: del self.state['orders'][identifier]

    def account_view(self):
        a=self.account()
        money=('cash','equity','unrealised','realised_net','margin','reserved','available','unsettled','planned_risk','gross','net')
        return dict(**{k:rupees(a[k]) for k in money},stale_instruments=a['stale_instruments'],drawdown_pct=a['drawdown_pct'],underlying=a['underlying'],
            books={key:dict(unrealised=rupees(value['unrealised']),realised_net=rupees(self.state['realised'][key]),fees=rupees(self.state['fee_by_book'][key]),committed=rupees(value['committed'])) for key,value in a['books'].items()})

    def halt_check(self):
        a=self.account();r=self.config['risk'];reason=None
        if a['equity']-self.state['day_start'] < -self.state['day_start']*r['daily_pct']/100: reason='Daily loss threshold breached'
        if a['equity']-self.state['week_start'] < -self.state['week_start']*r['weekly_pct']/100: reason='Weekly loss threshold breached'
        if a['drawdown_pct']>=r['drawdown_pct']: reason='Drawdown threshold breached'
        if a['margin']>a['equity']*r['margin_pct']/100: reason='Margin utilisation threshold breached'
        if a['cash']<0 or a['equity']<=0: reason='Funding exhausted; losses remain recorded'
        if reason and not self.state['halt']:
            self.state['halt']=reason;self.event('RISK_HALT',reason,account=self.account_view())
            for spec in self.specs.values(): self.close_book(spec['id'],reason)

    def protected_exits(self,rows):
        for owner,identifier,lot in list(self.lots()):
            if identifier not in rows: continue
            row=rows[identifier];sign=1 if lot['quantity']>0 else -1
            if self.manifest['frequency']=='QUOTE':
                mark=self.mark(identifier,lot['quantity']);stop=sign*(mark-lot['stop'])<=0;target=lot['target'] and sign*(mark-lot['target'])>=0
            else:
                stop=(paise(row['low'])<=lot['stop']) if sign>0 else (paise(row['high'])>=lot['stop'])
                target=lot['target'] and ((paise(row['high'])>=lot['target']) if sign>0 else (paise(row['low'])<=lot['target']))
            if stop or target:
                source='STOP' if stop and (not target or self.config['execution']['bar_ambiguity']=='ADVERSE_FIRST') else 'TARGET'
                if stop and target:
                    self.state['ambiguities']+=1;self.event('BAR_AMBIGUITY','Both stop and target were inside the bar; declared ordering applies',strategy=owner,instrument=identifier,policy=self.config['execution']['bar_ambiguity'])
                self.close_book(owner,source,identifier,lot['id'])
                if self.manifest['frequency']!='QUOTE':
                    # The path is unknown: trigger-price execution is an explicit bar approximation.
                    trigger=(lot['stop'] if source=='STOP' else lot['target'])/100
                    opening=row['open']
                    trigger=min(opening,trigger) if source=='STOP' and sign>0 else max(opening,trigger) if source=='STOP' and sign<0 else trigger
                    q=self.state['quotes'][identifier];saved=copy.deepcopy(q)
                    q['bid']=q['ask']=trigger
                    self.match({identifier:row})
                    self.state['quotes'][identifier]=saved

    def apply_actions(self):
        for index,action in enumerate(self.manifest['actions']):
            if index in self.state['actions_done'] or aware(action['effective_at'])>aware(self.state['clock']): continue
            identifier=action['instrument_id'];kind=action['kind'];self.state['actions_done'].append(index)
            self.event('CORPORATE_ACTION',kind,instrument=identifier,action=action)
            if kind in {'SPLIT','CONTRACT_ADJUSTMENT'}:
                ratio=action['ratio']
                for _,_,lot in self.lots(identifier=identifier):
                    quantity=lot['quantity']*ratio
                    if quantity!=round(quantity):
                        self.state['violations'].append('Fractional action entitlement needs cash-in-lieu evidence: '+identifier);continue
                    lot['quantity']=round(quantity);lot['price']=round(lot['price']/ratio);lot['stop']=round(lot['stop']/ratio)
                    if lot['target']: lot['target']=round(lot['target']/ratio)
                for order in self.state['orders'].values():
                    if order['instrument_id']==identifier: self.cancel(order,'Corporate action requires a fresh order decision')
                prior=self.quote(identifier)
                if prior:
                    for field in ('open','high','low','close','bid','ask'):
                        if prior.get(field) is not None: prior[field]/=ratio
                for row in self.state['histories'].get(identifier,[]):
                    for field in ('open','high','low','close'): row[field]/=ratio
            elif kind=='DIVIDEND':
                for owner,_,lot in self.lots(identifier=identifier):
                    amount=paise(action['amount']*lot['quantity'])
                    payable=action['payable_at'] or action['effective_at']
                    self.state['dividends'].append(dict(amount=amount,date=payable,book=owner))
                    self.state['realised'][owner]+=amount
            elif kind=='SYMBOL_CHANGE': self.instruments[identifier]['symbol']=action['new_symbol'] or self.instruments[identifier]['symbol']
            elif kind=='DELIST':
                for owner,_,_ in list(self.lots(identifier=identifier)): self.close_book(owner,'Delisting exposure requires exit or settlement evidence',identifier)
                self.state['violations'].append('Delisting handling requires terminal valuation evidence for '+identifier)
        for dividend in list(self.state['dividends']):
            if aware(dividend['date'])<=aware(self.state['clock']):
                self.ledger(dividend['amount'],'DIVIDEND_PAYMENT',dividend['book']);self.state['dividends'].remove(dividend)

    def settle(self,rows):
        zone=ZoneInfo(self.manifest['calendar']['timezone']);at=aware(self.state['clock']);day=at.astimezone(zone).date()
        span=bounds(self.manifest['calendar'],day)
        if not span or at<span[1]: return
        for owner,identifier,lot in list(self.lots()):
            item=self.terms(identifier);q=self.quote(identifier)
            if item['kind']=='FUT':
                key=f'{day}:{lot["id"]}'
                if key not in self.state['settlements_done'] and identifier in rows:
                    settlement=paise(rows[identifier].get('settlement') or rows[identifier]['close'])
                    pnl=(settlement-lot['price'])*lot['quantity']
                    self.ledger(pnl,'FUTURES_MTM',owner,instrument_id=identifier,price=rupees(settlement),official=rows[identifier].get('settlement') is not None)
                    self.state['realised'][owner]+=pnl;lot['price']=settlement;self.state['settlements_done'].append(key)
            if item['expiry'] and at>=aware(item['expiry']):
                spot=self.spot(item)
                observed=q and 0<=(at-aware(q['timestamp'])).total_seconds()<=self.config['execution']['max_quote_age_seconds']
                if not observed or (item['kind']=='OPT' and not spot):
                    self.state['violations'].append('Expiry settlement lacks fresh evidence: '+identifier);continue
                settlement=(max(0,spot-item['strike']) if item['option_type']=='CE' else max(0,item['strike']-spot)) if item['kind']=='OPT' else (q.get('settlement') or q['close'])
                if item['settlement']=='PHYSICAL' and (item['kind']=='FUT' or settlement>0):
                    underlying=self.instruments.get(item['underlying'])
                    if not underlying or underlying['kind']!='EQ' or not self.fresh(item['underlying']):
                        obligation=dict(instrument_id=identifier,lot_id=lot['id'],quantity=lot['quantity'],underlying=item['underlying'],reason='Physical delivery needs fresh underlying equity evidence')
                        if obligation not in self.state['obligations']: self.state['obligations'].append(obligation)
                        self.state['halt']='Unresolved physical delivery obligation'
                        self.event('DELIVERY_OBLIGATION',obligation['reason'],strategy=owner,instrument=identifier,quantity=lot['quantity'])
                        continue
                quantity=lot['quantity'];price=paise(settlement)
                pnl=(price-lot['price'])*quantity
                self.ledger(pnl if item['kind']=='FUT' else price*quantity,'EXPIRY_SETTLEMENT',owner,instrument_id=identifier,settlement=settlement)
                self.state['realised'][owner]+=pnl
                position=self.state['positions'][self.key(owner,identifier)];position['lots'].remove(lot)
                if not position['lots']: del self.state['positions'][self.key(owner,identifier)]
                self.event('SETTLEMENT','Contract expired; settlement type applied',strategy=owner,instrument=identifier,quantity=quantity,price=settlement,settlement_type=item['settlement'])
                if item['settlement']=='PHYSICAL' and (item['kind']=='FUT' or settlement>0):
                    underlying=item['underlying'];underlying_item=self.instruments.get(underlying)
                    if not underlying_item or underlying_item['kind']!='EQ' or not self.fresh(underlying):
                        self.state['violations'].append('Physical delivery cannot be resolved without fresh underlying equity: '+identifier)
                        self.state['halt']='Unresolved physical delivery obligation'
                        self.event('DELIVERY_OBLIGATION','Delivery remains unresolved',strategy=owner,instrument=identifier,quantity=quantity);continue
                    shares=quantity*(-1 if item['kind']=='OPT' and item['option_type']=='PE' else 1)
                    order=self.place(self.specs[owner],underlying,shares,'Physical delivery transfer; internal exercise value and delivery cash net to strike',order_type='MARKET',product='DELIVERY')
                    # Cash intrinsic settlement plus an underlying transfer at spot has the same net strike cash flow.
                    self.fill(order,abs(shares),self.quote(underlying)['close'],self.quote(underlying)['close'])
                    self.state['orders'].pop(order['id'],None)
                    held=sum(l['quantity'] for _,_,l in self.lots(identifier=underlying))
                    if held<0 or self.state['cash']<0:
                        self.state['violations'].append('Delivery shortfall/funding deficit: '+underlying)
                        self.state['halt']='Physical delivery shortfall requires review'

    def step(self,rows,cursor,timer_at=None):
        self.state['cursor']=cursor;self.used={};self.records=[];self.changed_orders={}
        if rows:
            end=rows[0]['timestamp'];opening=min(r['open_at'] for r in rows)
        else:
            if not self.config.get('contract_protocol') or timer_at is None:raise ValueError('Empty replay batch requires a frozen protocol clock event')
            end=opening=iso(timer_at)
        self.state['clock']=opening;self.phase='OPEN'
        if not rows:self.event('CLOCK','Declared protocol clock; no market observation fabricated')
        zone=ZoneInfo(self.manifest['calendar']['timezone']);day=aware(opening).astimezone(zone).date().isoformat()
        week=str(aware(opening).astimezone(zone).isocalendar()[:2])
        if day!=self.state['day']:
            previous_equity=self.account()['equity']
            self.state['unsettled']=[p for p in self.state['unsettled'] if p['date']>day]
            if self.state['day'] is not None:
                elapsed=(aware(opening).date()-__import__('datetime').date.fromisoformat(self.state['day'])).days
                for owner,identifier,lot in self.lots():
                    prior=self.quote(identifier) or {}
                    if self.terms(identifier)['kind']=='EQ' and lot['quantity']<0:
                        charge=paise(abs(lot['quantity'])*prior.get('close',0)*(prior.get('borrow_rate') or 0)*elapsed/365)
                        self.ledger(-charge,'BORROW_FEE',owner,instrument_id=identifier)
                        self.state['realised'][owner]-=charge;self.state['fees']+=charge;self.state['fee_by_book'][owner]+=charge
                for order in self.state['orders'].values():
                    if not order['reduce_only'] and order['age']>0: self.cancel(order,'DAY order expired at session boundary')
                for owner,identifier,lot in list(self.lots()):
                    if lot['product']=='INTRADAY':
                        self.state['violations'].append('Unfilled intraday exposure carried into '+day)
                        self.close_book(owner,'Overdue intraday exit',identifier,lot['id'])
            self.state['day']=day;self.state['dp']=[]
            self.state['day_start']=previous_equity
            self.apply_actions()
            if week!=self.state['week']: self.state['week']=week;self.state['week_start']=previous_equity
            if self.state['halt']=='Daily loss threshold breached': self.state['halt']=None
        else: self.apply_actions()
        current={r['instrument_id']:r for r in rows}
        for row in rows:
            identifier=row['instrument_id'];previous=self.quote(identifier) or {}
            if self.manifest['frequency']=='QUOTE': quote=copy.deepcopy(row)
            else:
                spread=row['open']*self.config['execution']['spread_bps']/10000
                capacity=math.floor(row['volume']*self.config['execution']['participation_pct']/100)
                quote=dict(instrument_id=identifier,open_at=opening,open=row['open'],high=row['open'],low=row['open'],close=row['open'],
                    bid=max(.0001,row['open']-spread/2),ask=row['open']+spread/2,bid_quantity=capacity,ask_quantity=capacity,
                    volume=0,capacity_model='Observed next-bar volume participation assumption; exact intrabar execution timing unknown',
                    underlying_price=previous.get('underlying_price'),iv=previous.get('iv'),margin_per_lot=previous.get('margin_per_lot'),
                    borrow_quantity=previous.get('borrow_quantity'),borrow_rate=previous.get('borrow_rate'),features_json=previous.get('features_json','{}'),settlement=None)
            quote['timestamp']=opening
            self.state['quotes'][identifier]=quote
        self.halt_check()
        self.match(current)
        self.state['clock']=end;self.phase='CLOSE'
        self.protected_exits(current)
        for row in rows:
            identifier=row['instrument_id'];quote=copy.deepcopy(row)
            if self.manifest['frequency']!='QUOTE':
                spread=row['close']*self.config['execution']['spread_bps']/10000
                quote.update(bid=max(.0001,row['close']-spread/2),ask=row['close']+spread/2,
                    bid_quantity=math.floor(row['volume']*self.config['execution']['participation_pct']/100),ask_quantity=math.floor(row['volume']*self.config['execution']['participation_pct']/100))
            self.state['quotes'][identifier]=quote
            history=self.state['histories'].setdefault(identifier,[]);history.append(copy.deepcopy(row));del history[:-10000]
        self.settle(current)
        self.halt_check()
        active=(not self.config['start'] or aware(end)>=aware(self.config['start'])) and (not self.config['end'] or aware(end)<=aware(self.config['end']))
        if self.config['training_end'] and aware(end)>aware(self.config['training_end']): self.state['post_train_count']+=1
        if self.config['training_end'] and self.state['post_train_count']<=self.config['embargo_bars']: active=False
        for owner,spec in self.specs.items():
            owned=list(self.lots(book=owner))
            for _,identifier,lot in owned:
                if not self.strategy_exits_enabled(spec):break
                item=self.terms(identifier)
                cutoff=(self.config['execution']['expiry_policy']=='EXIT_BEFORE' or spec['roll_on_expiry']) and item['expiry'] and aware(end)>=aware(item['expiry'])-timedelta(days=spec['exit_dte_days'])
                close=bounds(self.manifest['calendar'],aware(end).astimezone(zone).date())
                intraday=not spec['allow_overnight'] and close and aware(end)>=close[1]-timedelta(minutes=10)
                if cutoff and spec['roll_on_expiry']:
                    self.state['local'][owner]['roll_due']=True
                    self.close_book(owner,'Rollover: close expiring group before entering the next eligible contracts')
                if cutoff or intraday or cursor-lot['opened_cursor']>=spec['parameters']['hold_bars']:
                    self.close_book(owner,'Expiry cutoff' if cutoff else 'Intraday cutoff' if intraday else 'Maximum holding horizon',identifier,lot['id'])
            if not active: continue
            train_start=next((w['start'] for w in self.config['windows'] if w['label']=='TRAIN'),None)
            outcome=signal(spec,self.state['histories'],owned,end,self.state['local'][owner],self.config['training_end'],train_start)
            self.event('SIGNAL',outcome['reason'],strategy=owner,signal=outcome['action'],inputs=outcome['inputs'],strategy_version=spec['version'],strategy_hash=digest(spec))
            pending=any(o['strategy_id']==owner and o['status'] in {'PENDING_ACK','OPEN','PARTIAL','CANCEL_PENDING'} for o in self.state['orders'].values())
            if self.state['local'][owner].get('roll_due') and not owned and not pending and not self.state['halt']:
                self.event('ROLLOVER','Entering next eligible contracts after the old group closed',strategy=owner)
                if self.enter(spec,self.state['local'][owner].get('last_direction',1)): self.state['local'][owner]['roll_due']=False
            elif self.state['local'][owner].get('reversal_due') and not owned and not pending and not self.state['halt']:
                direction=self.state['local'][owner]['reversal_due']
                self.event('REVERSAL','Old exposure closed; evaluating opposite-direction entry',strategy=owner,direction=direction)
                if self.enter(spec,direction): self.state['local'][owner]['reversal_due']=None
            elif outcome['action']=='REVERSE':
                self.state['local'][owner]['reversal_due']=outcome['direction']
                self.close_book(owner,outcome['reason'])
            elif outcome['action']=='EXIT': self.close_book(owner,outcome['reason'])
            elif outcome['action']=='CANCEL':
                for order in self.state['orders'].values():
                    if order['strategy_id']==owner: self.cancel(order,outcome['reason'])
            elif outcome['action']=='REBALANCE':
                if owned: self.close_book(owner,'Ranking rebalance; reduce old exposure first')
                elif not pending and not self.state['halt']: self.enter(spec,1,outcome.get('selected'))
            elif outcome['action']=='ENTER' and not pending and not self.state['halt']:
                if not owned or spec['family']=='SCHEDULED': self.enter(spec,outcome.get('direction',1))
                else: self.event('SKIP','Strategy already owns exposure; awaiting its exit',strategy=owner)
        self.halt_check()
        account=self.account();self.state['peak']=max(self.state['peak'],account['equity'])
        self.state['margin_peak']=max(self.state['margin_peak'],account['margin'])
        if self.state['cash']!=self.state['ledger_total']: raise RuntimeError('Cash ledger reconciliation failed')
        if active:
            view=self.account_view();view.update(time=end,cursor=cursor,fees=rupees(self.state['fees']),halt=self.state['halt'])
            if self.config['benchmark_id'] and self.quote(self.config['benchmark_id']):
                price=self.quote(self.config['benchmark_id'])['close']
                if self.state['benchmark'] is None: self.state['benchmark']=price
                view['benchmark']=self.config['capital']*price/self.state['benchmark']
                view['benchmark_method']='Price return; distributions are not reinvested'
            self.state['last_equity']=view
            self.event('EQUITY','Account marked using available observations',account=view)
        self.emit(self.records,self.changed_orders,self.state)

    def final_positions(self):
        result=[]
        for key,position in self.state['positions'].items():
            owner,identifier=key.split('\x1f');quantity=sum(l['quantity'] for l in position['lots'])
            result.append(dict(strategy_id=owner,instrument_id=identifier,quantity=quantity,lots=position['lots'],mark=rupees(self.mark(identifier,quantity)),fresh=self.fresh(identifier),greeks=self.greeks(identifier)))
        return result
