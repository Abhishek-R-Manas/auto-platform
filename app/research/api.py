"""Local research APIs; immutable imports and isolated paper accounts only."""
import csv
import io
import json
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import Field, field_validator, model_validator

from .schemas import ImportData, Run, Suite, Control, Strict, Manifest, aware
from .catalog import canonical
from .fixtures import synthetic_fixture, templates
from .signals import capabilities, FAMILIES


class Preflight(Strict):
    config: Run


class WalkForward(Strict):
    base: Run
    train_observations: Annotated[int,Field(ge=20,le=100000)]=60
    test_observations: Annotated[int,Field(ge=5,le=100000)]=20
    step_observations: Annotated[int,Field(ge=5,le=100000)]=20
    embargo_observations: Annotated[int,Field(ge=0,le=10000)]=1
    max_folds: Annotated[int,Field(ge=1,le=32)]=8


class KiteImport(Strict):
    manifest: Manifest
    tokens: Annotated[dict[str,int],Field(min_length=1,max_length=2)]
    start: str
    end: str
    interval: Literal['minute','3minute','5minute','10minute','15minute','30minute','60minute','day']='day'
    @model_validator(mode='after')
    def range(self):
        if aware(self.start)>=aware(self.end) or (aware(self.end)-aware(self.start)).days>60: raise ValueError('Kite import requires an aware date range of at most 60 days')
        if any(type(v) is not int or v<=0 for v in self.tokens.values()): raise ValueError('Positive instrument tokens required')
        if set(self.tokens)!={i.id for i in self.manifest.instruments}: raise ValueError('Token map must match manifest instruments')
        if self.manifest.frequency=='QUOTE': raise ValueError('Kite historical candles cannot supply historical bid/ask quotes')
        return self


def router(research,kite):
    api=APIRouter(prefix='/api/research',tags=['Historical research'])

    @api.get('/capabilities')
    def families():
        return dict(mode='PAPER_RESEARCH',live_orders_enabled=False,families=FAMILIES,
            limitations=['Market making requires an additional order-book/queue implementation','Historical margin mode uses supplied contract observations, not a claimed SPAN reconstruction','AI generation and full AI supervision belong to later phases'])

    @api.get('/datasets')
    def datasets(): return research.catalog.datasets()

    @api.post('/datasets/import')
    def import_data(body:ImportData): return research.catalog.import_data(body)

    @api.post('/datasets/synthetic')
    def synthetic(): return research.catalog.import_data(synthetic_fixture())

    @api.post('/datasets/kite')
    def kite_import(body:KiteImport):
        import time
        from ..market import KiteData
        if not kite.status()['authenticated']: raise ValueError('Authenticate Kite market data on the server before importing')
        # Separate rate-limit lock so historical ingestion cannot monopolise the current quote feed.
        source=KiteData();source.key=kite.key;source.token=kite.token;source.secret=''
        rows=[]
        for identifier,token in body.tokens.items():
            start=aware(body.start);end=aware(body.end)
            while start<end:
                until=min(end,start+timedelta(days=30))
                data=source._request('GET',f'/instruments/historical/{token}/{body.interval}',params=dict(
                    **{'from':start.astimezone(__import__('zoneinfo').ZoneInfo(body.manifest.calendar.timezone)).strftime('%Y-%m-%d %H:%M:%S')},
                    to=until.astimezone(__import__('zoneinfo').ZoneInfo(body.manifest.calendar.timezone)).strftime('%Y-%m-%d %H:%M:%S'),oi=1,continuous=0))
                for candle in data.get('candles',[]):
                    if len(candle)<6: raise ValueError('Incomplete broker historical candle')
                    stamp=candle[0][:10] if body.interval=='day' else candle[0]
                    rows.append(dict(instrument_id=identifier,timestamp=stamp,open=candle[1],high=candle[2],low=candle[3],close=candle[4],volume=candle[5],oi=candle[6] if len(candle)>6 else ''))
                start=until+timedelta(seconds=1)
                time.sleep(.4)
        seen={}
        for row in rows: seen[(row['instrument_id'],row['timestamp'])]=row
        buffer=io.StringIO();writer=csv.DictWriter(buffer,fieldnames=['instrument_id','timestamp','open','high','low','close','volume','oi']);writer.writeheader();writer.writerows(seen.values())
        manifest=body.manifest.model_dump(mode='json');manifest.update(source_kind='BROKER',source='Kite historical candle API',timestamp_semantics='START')
        manifest['frequency']='DAY' if body.interval=='day' else 'MINUTE'
        manifest['interval_minutes']=1 if body.interval=='minute' else int(body.interval.replace('minute','')) if body.interval!='day' else 1
        return research.catalog.import_data(ImportData.model_validate(dict(manifest=manifest,format='CSV',content=buffer.getvalue())))

    @api.get('/datasets/{identifier}')
    def dataset(identifier:str): return research.catalog.get(identifier)

    @api.get('/datasets/{identifier}/rows')
    def observations(identifier:str,instrument_id:str|None=None,limit:Annotated[int,Query(ge=1,le=1000)]=100,offset:Annotated[int,Query(ge=0)]=0):
        return research.catalog.rows(identifier,instrument_id,limit,offset)

    @api.get('/datasets/{identifier}/manifest')
    def manifest(identifier:str):
        return Response(canonical(research.catalog.get(identifier)),media_type='application/json',headers={'Content-Disposition':'attachment; filename=dataset-manifest.json'})

    @api.get('/templates')
    def reference_templates(dataset_id:str): return templates(research.catalog.get(dataset_id))

    @api.post('/preflight')
    def preflight(body:Run): return capabilities(research.catalog.get(body.dataset_id),body.model_dump(mode='json'))

    @api.post('/runs')
    def run(body:Run): return research.create(body.model_dump(mode='json'))

    @api.get('/runs')
    def runs(): return research.runs()

    @api.get('/runs/{identifier}')
    def get_run(identifier:str): return research.get(identifier)

    @api.get('/runs/{identifier}/report')
    def report(identifier:str): return research.report(identifier)

    @api.get('/runs/{identifier}/curve')
    def curve(identifier:str): return research.curve(identifier)

    @api.get('/runs/{identifier}/orders')
    def orders(identifier:str): return research.orders(identifier)

    @api.get('/runs/{identifier}/events')
    def events(identifier:str,after:Annotated[int,Query(ge=0)]=0,limit:Annotated[int,Query(ge=1,le=500)]=100,kind:str|None=None):
        return research.events(identifier,after,limit,kind)

    @api.post('/runs/{identifier}/control')
    def control(identifier:str,body:Control): return research.control(identifier,body.action)

    @api.post('/runs/{identifier}/handoff')
    def handoff(identifier:str): return research.handoff(identifier)

    @api.get('/runs/{identifier}/export')
    def export(identifier:str):
        packet=dict(run=research.get(identifier),report=research.report(identifier),orders=research.orders(identifier),curve=research.curve(identifier))
        return Response(canonical(packet),media_type='application/json',headers={'Content-Disposition':'attachment; filename=artha-research-report.json'})

    @api.get('/compare')
    def compare(ids:str):
        selected=ids.split(',')
        if not 1<=len(selected)<=10: raise ValueError('Compare between one and ten runs')
        return [dict(id=identifier,run=research.get(identifier),report=research.report(identifier),curve=research.curve(identifier)) for identifier in selected]

    @api.post('/suites')
    def suite(body:Suite): return research.suite(body.base.model_dump(mode='json'),body.variants)

    @api.post('/walk-forward')
    def walk_forward(body:WalkForward):
        config=body.base.model_dump(mode='json')
        dates=sorted({r['timestamp'] for r in research.catalog.rows(config['dataset_id'])})
        starts=range(body.train_observations+body.embargo_observations,len(dates)-body.test_observations+1,body.step_observations)
        variants=[]
        for first in list(starts)[:body.max_folds]:
            train_end=first-body.embargo_observations-1
            variants.append({'start':dates[first],'end':dates[first+body.test_observations-1],
                'training_end':dates[train_end],'windows':[
                    dict(label='TRAIN',start=dates[max(0,train_end-body.train_observations+1)],end=dates[train_end]),
                    dict(label='WALK_FORWARD',start=dates[first],end=dates[first+body.test_observations-1])]})
        if not variants: raise ValueError('Dataset is too short for these walk-forward windows')
        experiment='walk-forward-'+__import__('uuid').uuid4().hex[:16]
        prepared=[]
        for index,variant in enumerate(variants):
            current={**config,**variant,'experiment_id':experiment,'name':config['name']+f' / fold {index+1}'}
            prepared.append(Run.model_validate(current).model_dump(mode='json'))
        return [research.create(item) for item in prepared]

    @api.get('/monitor/events')
    def monitor():
        return dict(authority='READ_ONLY_RESEARCH_EVIDENCE',ai_service_connected=False,live_orders_enabled=False,
            jobs=[dict(id=r['id'],status=r['status'],progress=r['progress'],clock=r.get('clock'),error=r.get('error')) for r in research.runs()],
            note='Deterministic process/job health interface for later monitoring services; no fabricated AI output')

    return api
