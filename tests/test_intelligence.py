"""Timestamped external inputs and derived Kite surfaces use isolated controlled evidence."""
import copy
from datetime import datetime,timedelta,timezone
import json,time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.api import create_app
from app.finance import option_value
from app.intelligence.schemas import ImportReport,FeedPolicy
from app.intelligence.service import DataFeeds

AT=datetime(2026,11,4,5,0,tzinfo=timezone.utc)
FII='Category,Date,Buy Value (Rs. Crores),Sell Value (Rs. Crores),Net Value (Rs. Crores)\nFII/FPI,03-Nov-2026,1200,1500,-300\nDII,03-Nov-2026,1800,1000,800\n'
MACRO='series,period,value,unit,base_year,published_at,status\nCPI_YOY,2026-09,5.54,PERCENT,2024,2026-10-12T10:30:00+00:00,PROVISIONAL\n'

class Quotes:
    authenticated=True
    def status(self):return dict(authenticated=self.authenticated)
    def master(self):
        rows=[dict(exchange='NSE',tradingsymbol='NIFTY 50',instrument_type='INDEX',segment='INDICES',instrument_token='257',name='NIFTY',expiry='',strike='0',lot_size='1',tick_size='.05')]
        for index,(strike,kind) in enumerate((k,t) for k in range(80,121,5) for t in ('CE','PE')):
            rows.append(dict(exchange='NFO',tradingsymbol=f'NIFTY26NOV{strike}{kind}',instrument_type=kind,segment='NFO-OPT',name='NIFTY',expiry='2026-11-26',strike=str(strike),lot_size='10',tick_size='.05',instrument_token=str(514+256*index)))
        return rows
    def _quotes(self,keys):
        by_key={r['exchange']+':'+r['tradingsymbol']:r for r in self.master()};result={}
        for key in keys:
            row=by_key[key];price=100
            if row['exchange']=='NFO':
                years=(datetime(2026,11,26,10,tzinfo=timezone.utc)-AT).total_seconds()/(365*86400)
                price=option_value(100,float(row['strike']),years,.28 if row['instrument_type']=='CE' else .32,row['instrument_type'])
            result[key]=dict(instrument_token=int(row['instrument_token']),last_price=price,timestamp=AT.isoformat(),oi=1000,volume=500,depth=dict(buy=[dict(price=max(.00001,price-.001),quantity=100)],sell=[dict(price=price+.001,quantity=100)]))
        return result

class OfflineSources:
    def fetch(self,source):raise ValueError('Controlled unavailable official source')

@pytest.fixture
def feeds(tmp_path):
    now=[AT];store=DataFeeds(tmp_path/'inputs',Quotes(),clock=lambda:now[0],providers=OfflineSources())
    yield store,now
    store.close()

def report(kind='FII_CSV',content=FII):
    return ImportReport(kind=kind,source='NSE' if kind=='FII_CSV' else 'MOSPI',scope='COMBINED_CASH' if kind=='FII_CSV' else 'INDIA',source_url='https://www.nseindia.com/reports/fii-dii' if kind=='FII_CSV' else 'https://esankhyiki.mospi.gov.in/',published_at='2026-11-03T13:00:00+00:00' if kind=='FII_CSV' else None,content=content,rights_confirmed=True,reviewer='Controlled owner')

def finish(store,job):
    for _ in range(300):
        store.tick();item=next(j for j in store.jobs() if j['id']==job['id'])
        if item['status'] not in {'QUEUED','RUNNING'}:return item
        time.sleep(.01)
    raise AssertionError('Controlled data job did not finish')

def test_official_fii_report_is_atomic_idempotent_and_preserves_units(feeds):
    store,now=feeds;item=store.ingest(report());assert store.ingest(report())['id']==item['id']
    values=store.features(now[0],['fii.COMBINED_CASH.net','dii.COMBINED_CASH.net'])
    assert values['fii.COMBINED_CASH.net']['value']==-300 and values['dii.COMBINED_CASH.net']['value']==800
    assert values['fii.COMBINED_CASH.net']['unit']=='INR_CRORE'
    assert store.audit()['ok']
    invalid=FII.replace('1500,-300','1500,-200')
    with pytest.raises(ValueError,match='net'):store.ingest(report(content=invalid))
    assert len(store.artifacts())==1

def test_receipt_and_publication_cutoffs_prevent_lookahead_and_revisions(feeds):
    store,now=feeds;store.ingest(report());key='fii.COMBINED_CASH.net'
    assert not store.features(AT-timedelta(seconds=1),[key])
    first=store.features(AT,[key])[key]['observation_id']
    now[0]+=timedelta(minutes=1);store.ingest(report(content=FII.replace('1500,-300','1600,-400')))
    assert store.features(AT,[key])[key]['value']==-300
    assert store.features(now[0],[key])[key]['value']==-400
    assert store.features(now[0],[key])[key]['observation_id']!=first
    assert len(store.artifacts())==2

def test_future_or_unreviewed_reports_are_rejected(feeds):
    store,_=feeds
    with pytest.raises(ValueError,match='rights'):store.ingest(report().model_copy(update={'rights_confirmed':False}))
    with pytest.raises(ValueError,match='future'):store.ingest(report().model_copy(update={'published_at':AT+timedelta(days=1)}))
    assert not store.artifacts()

def test_macro_bases_stay_separate_and_old_values_expire(feeds):
    store,now=feeds;store.ingest(report('MACRO_CSV',MACRO));key='macro.CPI_YOY.2024'
    assert store.features(AT,[key])[key]['value']==5.54
    assert not store.features(AT,['macro.CPI_YOY.2012'])
    now[0]+=timedelta(days=100)
    assert not store.features(now[0],[key])

def test_new_receipt_of_old_revision_does_not_replace_a_newer_reference_period(feeds):
    store,now=feeds;store.ingest(report('MACRO_CSV',MACRO))
    now[0]+=timedelta(seconds=1)
    older=MACRO.replace('2026-09,5.54','2026-08,5.00').replace('2026-10-12','2026-09-12')
    store.ingest(report('MACRO_CSV',older))
    assert store.features(now[0],['macro.CPI_YOY.2024'])['macro.CPI_YOY.2024']['value']==5.54

def test_macro_import_requires_known_numeric_units_and_safe_source(feeds):
    store,_=feeds
    for content in (MACRO.replace('5.54','NaN'),MACRO.replace('PERCENT','RUPEES')):
        with pytest.raises(ValueError):store.ingest(report('MACRO_CSV',content))
    with pytest.raises(ValueError):store.ingest(report('MACRO_CSV',MACRO).model_copy(update={'source_url':'http://127.0.0.1/internal'}))

def test_kite_chain_surface_has_iv_skew_and_expires_with_its_quotes(feeds):
    store,now=feeds;store.set_policy(FeedPolicy(kite_enabled=True,underlyings=['NIFTY'],strikes_each_side=4,expiries=1))
    job=finish(store,store.refresh('KITE','one-surface'))
    assert job['status']=='SUCCEEDED',job
    surface=store.surfaces()[0]
    assert surface['underlying']=='NIFTY' and surface['estimated'] and surface['points']
    assert surface['skew25_pp']==pytest.approx(4,abs=.1)
    features=store.features(AT,['iv.NIFTY.near.atm_pct','iv.NIFTY.near.skew25_pp'])
    assert features['iv.NIFTY.near.skew25_pp']['value']==pytest.approx(4,abs=.1)
    now[0]+=timedelta(seconds=31)
    assert not store.features(now[0],['iv.NIFTY.near.atm_pct'])

def test_stale_quotes_do_not_become_fresh_when_downloaded_again(feeds):
    store,now=feeds;now[0]+=timedelta(minutes=2)
    store.set_policy(FeedPolicy(kite_enabled=True,underlyings=['NIFTY']))
    result=finish(store,store.refresh('KITE','stale-surface'))
    assert result['status']=='FAILED' and not store.surfaces()
    assert not store.features(now[0],['iv.NIFTY.near.atm_pct'])

def test_unauthenticated_quotes_cannot_claim_connected_skew(feeds):
    store,_=feeds;store.kite.authenticated=False
    store.set_policy(FeedPolicy(kite_enabled=True,underlyings=['NIFTY']))
    result=finish(store,store.refresh('KITE','unauthenticated-chain'))
    assert result['status']=='FAILED' and not store.surfaces()

def test_source_failure_is_visible_and_preserves_previous_evidence(feeds):
    store,_=feeds;item=store.ingest(report('MACRO_CSV',MACRO))
    result=finish(store,store.refresh('RBI','failed-public-source'))
    assert result['status']=='FAILED' and store.artifact(item['id'])['id']==item['id']
    assert store.status()['sources']['RBI']['last_error']

def test_research_enrichment_creates_a_new_dataset_with_only_known_features(feeds,tmp_path):
    from app.research.catalog import Catalog
    from app.research.fixtures import synthetic_fixture
    store,_=feeds;store.ingest(report());catalog=Catalog(tmp_path/'research')
    try:
        original=catalog.import_data(synthetic_fixture());before=copy.deepcopy(catalog.rows(original['id']))
        enriched=store.enrich(original['id'],['fii.COMBINED_CASH.net'],catalog)
        assert enriched['id']!=original['id'] and enriched['manifest']['parent_dataset_id']==original['id']
        assert catalog.rows(original['id'])==before
        assert all('fii.COMBINED_CASH.net' not in json.loads(row['features_json']) for row in catalog.rows(enriched['id']))
    finally:catalog.close()

def test_public_api_same_origin_bounds_and_original_account_isolation(tmp_path,monkeypatch):
    for key in ('KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN','ARTHA_AI_API_KEY','ARTHA_AI_MODEL'):monkeypatch.delenv(key,raising=False)
    app=create_app(tmp_path/'paper.sqlite3',background=False);app.state.intelligence.clock=lambda:AT
    with TestClient(app,base_url='http://127.0.0.1') as client:
        before=client.get('/api/state').json()
        assert client.post('/api/intelligence/import',json=report().model_dump(mode='json')).status_code==403
        saved=client.post('/api/intelligence/import',json=report().model_dump(mode='json'),headers={'X-Artha-Client':'paper-workspace'})
        assert saved.status_code==200,saved.text
        assert client.get('/api/intelligence/status').json()['live_orders_enabled'] is False
        assert client.get('/api/intelligence/observations').json()
        assert client.get('/api/state').json()['account']==before['account']



def test_unknown_fii_publication_time_keeps_the_receipt_cutoff(feeds):
    store,_=feeds
    item=store.ingest(report().model_copy(update={'published_at':None}))
    assert all(row['published_at'] is None and row['available_at']==AT.isoformat() for row in item['observations'])
    assert not store.features(AT-timedelta(seconds=1),['fii.COMBINED_CASH.net'])


def test_changed_policy_cannot_relabel_an_inflight_surface(feeds):
    store,_=feeds;policy=FeedPolicy(kite_enabled=True,underlyings=['NIFTY'],expiries=1,strikes_each_side=4)
    original=store.set_policy(policy)['policy_hash'];job=store.refresh('KITE','policy-bound-surface');store.tick()
    store.set_policy(policy.model_copy(update={'interest_rate':.08}))
    result=finish(store,job)
    assert result['status']=='SUCCEEDED'
    artifact=store.artifact(result['result']['artifact_id'])
    assert artifact['extra']['policy_hash']==original and not store.surfaces()[0]['fresh']
    assert not store.features(AT,['iv.NIFTY.near.atm_pct'])


def test_rbi_releases_are_dated_not_invented_numeric_policy_rates(feeds):
    store,_=feeds
    class Source:
        def fetch(self,name):return dict(source='RBI',kind='RBI_RSS',source_url='https://www.rbi.org.in/pressreleases_rss.xml',content='<rss><channel><item><title>Monetary policy release</title><link>https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=1</link><pubDate>Wed, 04 Nov 2026 10:00:00 +0530</pubDate><description>&lt;b&gt;Official release notice&lt;/b&gt;</description></item></channel></rss>')
    store.providers=Source();job=finish(store,store.refresh('RBI','controlled-release'))
    assert job['status']=='SUCCEEDED'
    observation=store.observations()[0]
    assert observation['published_at']=='2026-11-04T04:30:00+00:00' and observation['value'] is None
    assert not store.features(AT,['event.RBI','macro.REPO_RATE.NONE'])


def test_selected_feature_tampering_is_rejected(feeds):
    store,_=feeds;item=store.ingest(report());row=item['observations'][0]
    with store.transaction():store.db.execute('UPDATE observations SET document=? WHERE id=?',(json.dumps(dict(row,value=99999)),row['id']))
    assert not store.audit()['ok']
    with pytest.raises(ValueError,match='integrity'):store.features(AT,[row['series']])


def test_expired_external_features_are_not_used_by_rule_evaluation():
    from app.research.signals import value
    row=dict(timestamp=AT.isoformat(),close=100,features_json=json.dumps({'dated':dict(value=3,available_at=(AT-timedelta(minutes=2)).isoformat(),valid_until=(AT-timedelta(seconds=1)).isoformat())}))
    assert value(dict(feature='feature',name='dated'),{'X':[row]},'X') is None


class OfficialFixture:
    def __init__(self):self.calls=[]
    def rpc(self,name,arguments):
        self.calls.append((name,copy.deepcopy(arguments)));dataset=arguments['dataset']
        if name=='get_indicators':return dict(data=dict(base_year=[dict(base_year='2024' if dataset=='CPI' else '2022-23')],indicator=[dict(name='GDP Growth Rate',indicator_code=77)]))
        if name=='get_metadata':
            if dataset=='CPI':return dict(data=[dict(year=[dict(year=2026)],state=[dict(state_name='All India',state_code=54)],sector=[dict(sector_name='Combined',sector_code=92)],division=[dict(division_name='CPI (General)',division_code=45)])])
            if dataset=='IIP':return dict(data=dict(year=[dict(year=2026)],category=[dict(category_name='General',category_code=19)]))
            return dict(data=dict(year=[dict(year='2026-27')]))
        assert name=='get_data';filters=arguments['filters']
        if dataset=='CPI':
            assert filters['state_code']=='54' and filters['sector_code']=='92' and filters['division_code']=='45' and 'level' not in filters
            row=dict(base_year='2024',year='2026',month='September',state='All India',sector='Combined',division='CPI (General)',index='109.49',inflation='5.54')
        elif dataset=='IIP':
            assert filters['category_code']=='19'
            row=dict(base_year='2022-23',year=2026,month='September',type='General',category='General',sub_category='',index='124.3',growth_rate='6.7')
        else:
            assert filters['indicator_code']=='77' and filters['frequency_code']=='Quarterly'
            row=dict(base_year='2022-23',year='2026-27',quarter='Q1',indicator='GDP Growth Rate',frequency='Quarterly',unit='%',constant_price='7.8',current_price='10.3')
        return dict(data=[row],meta_data=dict(totalPages=1),statusCode=True)


def test_official_macro_queries_use_metadata_codes_and_constant_price_gdp():
    from app.intelligence.mospi import download
    client=OfficialFixture();result=download(client,AT)
    values={item['series']:item for item in result['observations']}
    assert values['macro.GDP_YOY.2022-23']['value']==7.8
    assert values['macro.GDP_YOY.2022-23']['period']=='2026-06-30'
    assert values['macro.CPI_YOY.2024']['value']==5.54
    assert all(item['published_at'] is None for item in result['observations'])
    assert len(client.calls)==9 and not result['extra']['warnings']


def test_macro_rejects_ambiguous_or_wrong_scope_data():
    from app.intelligence.mospi import normalize
    row=dict(base_year='2024',year='2026',month='September',state='All India',sector='Urban',division='CPI (General)',index='109.49',inflation='5.54')
    assert not normalize('CPI','2024',[row],AT)
    with pytest.raises(ValueError,match='base year'):normalize('CPI','2012',[row],AT)


def test_forward_inputs_are_required_frozen_and_included_in_model_evidence(tmp_path):
    from app.forward.drills import DrillKite,DrillHub,DrillLab,protocol
    from app.forward.service import ForwardService
    from app.configuration import PrivateConfig
    store=DataFeeds(tmp_path/'evidence',Quotes(),clock=lambda:AT,providers=OfflineSources())
    forward=ForwardService(tmp_path/'forward',DrillKite(),DrillHub(),DrillLab(),PrivateConfig(tmp_path/'.env.local'),clock=lambda:AT);forward.intelligence=store;store.policy_guard=forward.lock
    try:
        body=protocol().model_dump(mode='json');body['strategies'][0]['schedule'][0]['at']=AT.isoformat();body.update(request_id='required-input-portfolio',evidence_features=['fii.COMBINED_CASH.net'],evidence_policy_hash=store.policy_hash())
        key=forward.create(body)['id'];assert 'REQUIRED_INPUT_STALE_OR_MISSING' in forward.portfolio(key)['readiness']
        store.ingest(report());assert 'REQUIRED_INPUT_STALE_OR_MISSING' not in forward.portfolio(key)['readiness']
        facts=forward.monitor_context(key)['facts'];assert any(fact['type']=='DATED_RESEARCH_INPUT' and fact['value']['value']==-300 for fact in facts)
        captured=forward.report(key);assert captured['research_inputs']['fii.COMBINED_CASH.net']['value']==-300
        store.ingest(report(content=FII.replace('1500,-300','1600,-400')))
        assert forward.report(key)['evidence_hash']!=captured['evidence_hash']
        store.set_policy(FeedPolicy(fii_max_age_hours=72))
        assert 'EVIDENCE_POLICY_CHANGED' in forward.portfolio(key)['readiness']
    finally:forward.close();store.close()


def test_forward_bar_join_cannot_use_a_value_received_after_the_bar(tmp_path):
    from app.forward.drills import DrillKite,DrillHub,DrillLab,protocol
    from app.forward.service import ForwardService
    from app.forward.runtime import ForwardReplay
    from app.configuration import PrivateConfig
    now=[AT];store=DataFeeds(tmp_path/'evidence',Quotes(),clock=lambda:now[0],providers=OfflineSources());store.ingest(report())
    forward=ForwardService(tmp_path/'forward',DrillKite(),DrillHub(),DrillLab(),PrivateConfig(tmp_path/'.env.local'),clock=lambda:AT);forward.intelligence=store
    try:
        body=protocol().model_dump(mode='json');body['strategies'][0]['schedule'][0]['at']=AT.isoformat();body.update(evidence_features=['fii.COMBINED_CASH.net'],evidence_policy_hash=store.policy_hash())
        key=forward.create(body)['id'];doc,state=forward._load(key);runtime=forward._attach_inputs(ForwardReplay(doc,state))
        rows=[dict(instrument_id='DRILL',timestamp=stamp.isoformat(),open=100,high=100,low=100,close=100,volume=1) for stamp in (AT-timedelta(minutes=5),AT)]
        runtime.apply_bars(rows,AT,warmup=True);history=runtime.state['histories']['DRILL']
        assert not json.loads(history[0]['features_json'])
        assert json.loads(history[1]['features_json'])['fii.COMBINED_CASH.net']['value']==-300
    finally:forward.close();store.close()



def test_input_store_restart_retains_vintages_and_marks_queued_work_interrupted(tmp_path):
    root=tmp_path/'durable-inputs';store=DataFeeds(root,Quotes(),clock=lambda:AT,providers=OfflineSources())
    item=store.ingest(report());job=store.refresh('RBI','queued-before-restart');store.close()
    restored=DataFeeds(root,Quotes(),clock=lambda:AT+timedelta(seconds=1),providers=OfflineSources())
    try:
        assert restored.artifact(item['id'])==item and restored.audit()['ok']
        assert next(row for row in restored.jobs() if row['id']==job['id'])['status']=='INTERRUPTED'
        assert restored.features(AT,['fii.COMBINED_CASH.net'])['fii.COMBINED_CASH.net']['value']==-300
    finally:restored.close()


def test_input_backup_verifies_artifact_and_observation_copies(feeds,tmp_path):
    import sqlite3
    from tools.forward_ops import check_database
    store,_=feeds;item=store.ingest(report());saved=tmp_path/'inputs-backup.sqlite3'
    with sqlite3.connect(saved) as db:store.db.backup(db)
    assert check_database(saved)['input_artifacts_verified']==1
    with sqlite3.connect(saved) as db:db.execute('DELETE FROM observations WHERE id=?',(item['observations'][0]['id'],))
    with pytest.raises(ValueError,match='Input observation'):check_database(saved)



def test_rbi_dates_without_offsets_keep_the_timezone_assumption_visible():
    from app.intelligence.parsers import rbi_items
    rows=rbi_items('<rss><channel><item><title>Official dated notice</title><link>https://www.rbi.org.in/</link><pubDate>Wed, 04 Nov 2026 00:00:00</pubDate></item></channel></rss>',AT)
    assert rows[0]['publication_precision']=='DATE_ONLY' and rows[0]['publication_timezone_assumed']=='Asia/Kolkata'
    assert rows[0]['published_at']=='2026-11-03T18:30:00+00:00'



def test_report_url_credentials_are_rejected_without_echo_or_storage(tmp_path):
    app=create_app(tmp_path/'paper.sqlite3',background=False)
    secret='CONTROLLED_ACCIDENTAL_QUERY_CREDENTIAL'
    with TestClient(app,base_url='http://127.0.0.1') as client:
        payload=report().model_dump(mode='json');payload['source_url']+='?access_token='+secret
        response=client.post('/api/intelligence/import',json=payload,headers={'X-Artha-Client':'paper-workspace'})
        assert response.status_code==422 and secret not in response.text
        assert not client.get('/api/intelligence/artifacts').json()



def test_slow_public_source_cannot_starve_kite_surface_updates(feeds):
    from concurrent.futures import Future
    store,_=feeds;public=store.refresh('MOSPI','slow-public-data')
    with store.transaction():
        item=next(row for row in store.jobs() if row['id']==public['id']);item.update(status='RUNNING',started_at=store.now());store._save_job(item)
    store.futures[public['id']]=dict(group='PUBLIC',future=Future())
    policy=FeedPolicy(kite_enabled=True,underlyings=['NIFTY'],expiries=1,strikes_each_side=4)
    assert policy.quote_interval_seconds<policy.max_quote_age_seconds/2
    store.set_policy(policy)
    result=finish(store,store.refresh('KITE','independent-kite-quotes'))
    assert result['status']=='SUCCEEDED' and store.surfaces()[0]['fresh']
    assert next(row for row in store.jobs() if row['id']==public['id'])['status']=='RUNNING'


def test_timed_out_source_discards_late_output(feeds):
    from concurrent.futures import Future
    store,now=feeds;job=store.refresh('MOSPI','late-public-output');future=Future()
    with store.transaction():
        item=next(row for row in store.jobs() if row['id']==job['id']);item.update(status='RUNNING',started_at=store.now());store._save_job(item)
    store.futures[job['id']]=dict(group='PUBLIC',future=future)
    now[0]+=timedelta(seconds=91);store.tick()
    assert next(row for row in store.jobs() if row['id']==job['id'])['status']=='TIMED_OUT'
    future.set_result(dict(source='MOSPI',kind='UNTRUSTED_LATE_PAYLOAD'));store.tick()
    assert not store.artifacts() and next(row for row in store.jobs() if row['id']==job['id'])['status']=='TIMED_OUT'
