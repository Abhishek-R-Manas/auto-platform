"""Phase 3 contracts, governance and execution tests use controlled data and model responses."""
import copy
from datetime import datetime
import json
import time
from types import SimpleNamespace
import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.candidates.schemas import Generate,Review,DataReview
from app.candidates.service import StrategyLab
from app.candidates.provider import OpenAIProvider,ModelError
from app.research.jobs import Research,worker_setup
from app.research.fixtures import synthetic_fixture
from app.research.catalog import digest,canonical,aware

HEADERS={'X-Artha-Client':'paper-workspace'}

class ControlledModel:
    def __init__(self,spec): self.spec=spec;self.calls=[]
    def status(self): return dict(configured=True,model='CONTROLLED_TEST_MODEL',provider='CONTROLLED_TEST',message='Controlled software fixture, not a genuine model',live_provider_response=False)
    def request(self,idea,context,tokens):
        self.calls.append(copy.deepcopy(context))
        return dict(hypothesis='Controlled unvalidated software-test hypothesis',strategy_json=json.dumps(self.spec),risks=['Controlled fixture has no demonstrated trading edge'],assumptions=['Software validation only']),dict(provider='CONTROLLED_TEST',model='CONTROLLED_TEST_MODEL',live_provider_response=False,input_tokens=10,output_tokens=10,raw_output=json.dumps(self.spec))

@pytest.fixture
def lab(tmp_path):
    research=Research(tmp_path/'research')
    dataset=research.catalog.import_data(synthetic_fixture())
    lab=StrategyLab(tmp_path/'lab',research)
    study=lab.create_study(dict(name='Controlled study',dataset_id=dataset['id'],policy=dict(min_return_pct=-100,max_drawdown_pct=50)))
    yield lab,study,dataset
    lab.close();research.close()


def spec(lab,study):
    rows=lab.research.catalog.rows(study['config']['dataset_id']);identifier=next(i for i in study['training_instruments'] if i=='NSE:ALPHA')
    dates=sorted({r['timestamp'] for r in rows})
    return dict(id='controlled-entry',name='Controlled scheduled equity',family='SCHEDULED',legs=[dict(instrument_id=identifier)],
                signal_instruments=[identifier],schedule=[dict(at=at,action='ENTER') for at in dates],parameters=dict(hold_bars=3),
                size_mode='LOTS',lots=1,stop_pct=5,allocation_pct=25)


def add(lab,study,**changes):
    value=dict(study_id=study['id'],strategy=spec(lab,study),hypothesis='Controlled research hypothesis; no performance claim',risks=['Fixture uncertainty'],assumptions=['Controlled inputs'],**changes)
    return lab.add_version(value)


def complete(lab,identifier,seconds=40):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        lab.tick();current=next(e for e in lab.evaluations() if e['id']==identifier)
        if current['status'] in {'COMPLETED','FAILED','CANCELLED'}: return current
        time.sleep(.03)
    raise AssertionError('Controlled evaluation did not complete within its test budget')


def test_protocol_has_disjoint_immutable_windows_and_training_only_context(lab):
    lab,study,dataset=lab
    assert study['windows']['TRAIN']['end']<study['windows']['VALIDATION']['start']<study['windows']['VALIDATION']['end']<study['windows']['HOLDOUT']['start']
    assert all(r['timestamp']<=study['windows']['TRAIN']['end'] for i in study['training_context']['instruments'] for r in i['training_observations'])
    with pytest.raises(ValueError,match='already has a study'): lab.create_study(dict(name='Adaptive second study',dataset_id=dataset['id']))


def test_immutable_candidate_lineage_and_idempotent_save(lab):
    lab,study,_=lab;first=add(lab,study);same=add(lab,study)
    assert same['id']==first['id'] and lab.study(study['id'])['version_attempts']==1
    revised=add(lab,study,parent_version_id=first['id'])
    assert revised['id']!=first['id'] and revised['parent_version_id']==first['id']
    assert lab.verify_audit()['ok']


def test_unsupported_family_is_retained_as_blocked(lab):
    lab,study,_=lab;s=spec(lab,study);s.update(family='MARKET_MAKING')
    version=lab.add_version(dict(study_id=study['id'],strategy=s,hypothesis='Controlled unsupported family hypothesis'))
    assert version['status']=='BLOCKED'
    with pytest.raises(ValueError,match='blocked'): lab.evaluate(version['id'],'blocked-evaluation')


def test_nested_transaction_rolls_back_candidate_and_budget(lab):
    lab,study,_=lab
    with pytest.raises(RuntimeError):
        with lab.transaction(): add(lab,study);raise RuntimeError('Controlled transaction failure')
    assert not lab.versions() and lab.study(study['id'])['version_attempts']==0
    assert lab.verify_audit()['ok']


def test_audit_tampering_disables_writes(lab):
    lab,study,_=lab
    with lab.db: lab.db.execute('UPDATE audit SET document=? WHERE sequence=1',(canonical(dict(sequence=1,hash='wrong',previous_hash='0'*64)),))
    assert not lab.verify_audit()['ok']
    with pytest.raises(ValueError,match='integrity'): add(lab,study)


def test_candidate_hash_detects_immutable_content_changes(lab):
    lab,study,_=lab;version=add(lab,study)
    altered={k:v for k,v in version.items() if k!='status'};altered['hypothesis']='Altered hypothesis after its original creation'
    with lab.db: lab.db.execute('UPDATE versions SET document=? WHERE id=?',(canonical(altered),version['id']))
    with pytest.raises(ValueError,match='hash mismatch'): lab.version(version['id'])


def test_controlled_ai_generation_records_context_usage_and_origin(lab):
    lab,study,_=lab;provider=ControlledModel(spec(lab,study));lab.provider=provider
    request=Generate(study_id=study['id'],idea='Generate a controlled equity research hypothesis',request_id='controlled-ai-1')
    queued=lab.generate(request);lab.futures[queued['id']].result(timeout=5)
    generation=lab.generations()[0];version=lab.version(generation['version_id'])
    assert generation['status']=='SUCCEEDED' and version['origin']=='AI'
    assert version['provenance']['live_provider_response'] is False
    assert lab.generate(request)['id']==queued['id'] and len(provider.calls)==1
    assert all(r['timestamp']<=study['windows']['TRAIN']['end'] for i in provider.calls[0]['instruments'] for r in i['training_observations'])
    assert lab.study(study['id'])['reserved_output_tokens']==6000


def test_model_failure_is_retained_without_retry_or_fabricated_candidate(lab):
    lab,study,_=lab
    class BrokenModel(ControlledModel):
        def request(self,*args): self.calls.append(1);raise ModelError('Controlled model refusal')
    provider=BrokenModel({});lab.provider=provider
    queued=lab.generate(Generate(study_id=study['id'],idea='Controlled failure request for the provider',request_id='failure-1'))
    lab.futures[queued['id']].result(timeout=5)
    assert lab.generations()[0]['status']=='FAILED' and not lab.versions() and len(provider.calls)==1
    assert lab.study(study['id'])['generation_attempts']==1


def test_model_cannot_inject_execution_code_or_risk_overrides(lab):
    lab,study,_=lab;unsafe=spec(lab,study);unsafe['python']='import os; os.system("invalid")'
    lab.provider=ControlledModel(unsafe)
    queued=lab.generate(Generate(study_id=study['id'],idea='Controlled schema rejection test request',request_id='unsafe-model'))
    lab.futures[queued['id']].result(timeout=5)
    assert lab.generations()[0]['status']=='FAILED' and not lab.versions()
    assert lab.generations()[0]['evidence'] is not None


def test_model_budget_is_reserved_before_dispatch(lab):
    lab,study,_=lab;lab.provider=ControlledModel(spec(lab,study))
    changed=lab.study(study['id']);changed['config']['max_ai_output_tokens']=1024
    with lab.transaction():lab._save('studies',changed)
    with pytest.raises(ValueError,match='token budget'): lab.generate(Generate(study_id=study['id'],idea='Budget should prevent this controlled request',request_id='budget-fail'))
    assert not lab.provider.calls


def test_evaluation_pipeline_repeatability_and_holdout_governance(lab):
    lab,study,_=lab;version=add(lab,study)
    screen=lab.evaluate(version['id'],'screen-controlled');done=complete(lab,screen['id'])
    assert done['status']=='COMPLETED',done
    assert len(done['jobs'])==8 and all(j['status']=='COMPLETED' for j in done['jobs']),done
    assert done['summary']['technical_and_research_thresholds_passed'],done['summary']['checks']
    assert next(c for c in done['summary']['checks'] if c['name']=='Reproducible training replay')['passed']
    holdout=lab.seal(version['id'],'holdout-controlled')
    assert lab.seal(version['id'],'holdout-repeat')['id']==holdout['id']
    with pytest.raises(ValueError,match='frozen'): add(lab,study,parent_version_id=version['id'])
    with pytest.raises(ValueError,match='frozen'): lab.evaluate(version['id'],'forbidden-screen')
    result=complete(lab,holdout['id']);assert result['status']=='COMPLETED'
    assert lab.study(study['id'])['reserved_runs']==9
    evidence=lab.evidence(version['id'])
    assert any('Synthetic' in reason for reason in evidence['approval_blockers'])
    with pytest.raises(ValueError,match='Synthetic'):lab.review(version['id'],Review(action='APPROVE_PAPER',reviewer='Test reviewer',note='Controlled synthetic data cannot receive paper approval.',expected_status='HOLDOUT_REVIEW'))
    exported=lab.export(version['id']);assert exported['live_orders_enabled'] is False and exported['audit']['ok']


def test_rejection_retirement_and_stale_review_control(lab):
    lab,study,_=lab;version=add(lab,study)
    rejected=lab.review(version['id'],Review(action='REJECT',reviewer='Test reviewer',note='Controlled rejection because independent evidence is insufficient.',expected_status='VALIDATED'))
    assert rejected['status']=='REJECTED'
    with pytest.raises(ValueError,match='status changed'):lab.review(version['id'],Review(action='RETIRE',reviewer='Reviewer',note='Controlled stale review must fail without changing the record.',expected_status='VALIDATED'))
    retired=lab.review(version['id'],Review(action='RETIRE',reviewer='Reviewer',note='Controlled retirement preserves all candidate research evidence.',expected_status='REJECTED'))
    assert retired['status']=='RETIRED'


def test_restart_pauses_evaluations_and_preserves_versions(lab,tmp_path):
    lab,study,_=lab;version=add(lab,study);evaluation=lab.evaluate(version['id'],'restart-test')
    other=StrategyLab(lab.root,lab.research)
    try:
        assert other.version(version['id'])['status']=='EVALUATING'
        assert next(e for e in other.evaluations() if e['id']==evaluation['id'])['status']=='PAUSED'
        assert other.verify_audit()['ok']
    finally:other.close()


def test_replay_worker_strips_model_credentials(monkeypatch):
    monkeypatch.setenv('ARTHA_AI_API_KEY','controlled-test-key');monkeypatch.setenv('KITE_ACCESS_TOKEN','controlled-test-session')
    worker_setup()
    import os
    assert 'ARTHA_AI_API_KEY' not in os.environ and 'KITE_ACCESS_TOKEN' not in os.environ


def test_responses_transport_is_fixed_structured_and_has_no_tools(monkeypatch):
    monkeypatch.setenv('ARTHA_AI_API_KEY','controlled-test-key');monkeypatch.setenv('ARTHA_AI_MODEL','controlled-test-model')
    original=httpx.Client;requests=[]
    proposal=dict(hypothesis='Controlled model proposal for schema testing',strategy_json='{}',risks=['Controlled risk'],assumptions=['Controlled assumption'])
    def handle(request):
        requests.append(request);body=json.loads(request.content)
        assert str(request.url)=='https://api.openai.com/v1/responses'
        assert body['store'] is False and not body['tools'] and body['text']['format']['strict']
        assert 'controlled-test-key' not in request.content.decode()
        return httpx.Response(200,json=dict(status='completed',id='controlled-response',model='controlled-test-model',usage=dict(input_tokens=10,output_tokens=20),output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(proposal))])]))
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    result,evidence=OpenAIProvider().request('Controlled idea',{},1024)
    assert result==proposal and evidence['output_tokens']==20 and len(requests)==1


def test_transport_errors_never_leak_key_or_retry(monkeypatch):
    monkeypatch.setenv('ARTHA_AI_API_KEY','controlled-test-key');monkeypatch.setenv('ARTHA_AI_MODEL','controlled-test-model')
    original=httpx.Client;calls=[]
    def handle(request):calls.append(1);raise httpx.ConnectError('controlled-test-key private diagnostic')
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    with pytest.raises(ModelError) as caught: OpenAIProvider().request('Controlled idea',{},1024)
    assert 'controlled-test-key' not in str(caught.value) and len(calls)==1


def test_api_boundaries_and_unconfigured_ai_keep_account_isolated(tmp_path,monkeypatch):
    monkeypatch.delenv('ARTHA_AI_API_KEY',raising=False);monkeypatch.delenv('ARTHA_AI_MODEL',raising=False)
    app=create_app(tmp_path/'paper.sqlite3',background=False)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        before=client.get('/api/state').json()
        assert client.get('/api/candidates/status').json()['provider']['configured'] is False
        assert client.post('/api/candidates/studies',json={}).status_code==403
        assert client.post('/api/candidates/versions',json={'python':'forbidden'},headers=HEADERS).status_code==422
        assert client.post('/api/candidates/generations',json={'study_id':'a'*32,'idea':'controlled intent','request_id':'request1'},headers=HEADERS).status_code==400
        after=client.get('/api/state').json()
        assert after['account']==before['account'] and after['orders']==before['orders']
        assert not after['live_trading_available'] and client.get('/api/live/orders').status_code==404


def test_running_model_cancellation_retains_usage_without_candidate(lab):
    import threading
    lab,study,_=lab;started=threading.Event();release=threading.Event()
    class SlowModel(ControlledModel):
        def request(self,*args):started.set();release.wait(3);return super().request(*args)
    lab.provider=SlowModel(spec(lab,study))
    queued=lab.generate(Generate(study_id=study['id'],idea='Controlled cancellation of a running model request',request_id='cancel-running'))
    assert started.wait(2)
    assert lab.cancel_generation(queued['id'])['status']=='CANCEL_REQUESTED'
    release.set();lab.futures[queued['id']].result(timeout=5)
    assert lab.generations()[0]['status']=='CANCELLED' and lab.generations()[0]['evidence'] and not lab.versions()


def test_durable_dispatch_recovers_existing_run_without_duplicate(lab):
    lab,study,_=lab;version=add(lab,study);evaluation=lab.evaluate(version['id'],'dispatch-recovery')
    original=lab.research.create(evaluation['jobs'][0]['config'])
    lab.tick();recovered=lab.evaluations()[0]
    assert recovered['jobs'][0]['run_id']==original['id'] and len(lab.research.runs())==1
    lab.control(evaluation['id'],'CANCEL')


def test_restart_build_mismatch_cannot_resume_evaluation(lab):
    lab,study,_=lab;version=add(lab,study);evaluation=lab.evaluate(version['id'],'build-mismatch')
    evaluation['build']=dict(evaluation['build'],code_hash='changed')
    with lab.transaction():lab._save('evaluations',evaluation)
    with pytest.raises(ValueError,match='build mismatch'):lab.control(evaluation['id'],'RESUME')


def test_paper_approval_creates_packet_after_controlled_evidence_and_review(tmp_path):
    research=Research(tmp_path/'research');fixture=synthetic_fixture()
    # Simulated supplier assertions exercise the approval boundary; these are NOT real market data.
    fixture.manifest.source_kind='VENDOR';fixture.manifest.source='CONTROLLED TEST SUPPLIER ASSERTIONS ONLY'
    fixture.manifest.rights_confirmed=True;fixture.manifest.point_in_time_universe=True;fixture.manifest.corporate_actions_complete=True
    dataset=research.catalog.import_data(fixture);lab=StrategyLab(tmp_path/'lab',research)
    try:
        study=lab.create_study(dict(name='Controlled approval boundary',dataset_id=dataset['id'],policy=dict(min_return_pct=-100,max_drawdown_pct=50)))
        version=add(lab,study);assert complete(lab,lab.evaluate(version['id'],'approval-screen')['id'])['status']=='COMPLETED'
        assert complete(lab,lab.seal(version['id'],'approval-holdout')['id'])['status']=='COMPLETED'
        lab.data_review(study['id'],DataReview(reviewer='Controlled reviewer',note='Controlled test assertions only; no genuine market validation is claimed.',source_and_rights=True,calendar_and_actions=True,charges_margin_and_settlement=True,execution_assumptions=True))
        assert not lab.evidence(version['id'])['approval_blockers']
        approved=lab.review(version['id'],Review(action='APPROVE_PAPER',reviewer='Controlled reviewer',note='Approve only the controlled paper review packet; do not activate execution.',expected_status='HOLDOUT_REVIEW'))
        assert approved['status']=='PAPER_APPROVED'
        packet=lab.export(version['id'])['packets'][0]
        assert packet['mode']=='PAPER_REVIEW_PACKET' and not packet['live_orders_enabled'] and not packet['forward_trading_enabled']
        assert packet['evaluation_hashes'] and packet['data_review']['source_and_rights']
    finally:lab.close();research.close()


def test_failed_structured_output_retains_response_and_usage(monkeypatch):
    monkeypatch.setenv('ARTHA_AI_API_KEY','controlled-test-key');monkeypatch.setenv('ARTHA_AI_MODEL','controlled-test-model')
    original=httpx.Client
    def handle(request):return httpx.Response(200,json=dict(status='completed',id='controlled-malformed',usage=dict(input_tokens=10,output_tokens=8),output=[dict(type='message',content=[dict(type='output_text',text='invalid controlled model output')])]))
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    with pytest.raises(ModelError) as caught:OpenAIProvider().request('Controlled malformed response',{},1024)
    assert caught.value.evidence['raw_output']=='invalid controlled model output'
    assert caught.value.evidence['output_tokens']==8


def test_learned_training_replay_uses_a_prior_fit_cutoff(lab):
    lab,study,_=lab;s=spec(lab,study);s.update(family='LINEAR_MODEL',schedule=[])
    config=lab._run_config(study,s,'TRAIN',study['windows']['TRAIN'])
    assert config['training_end']<config['start']<config['end']
    assert aware(config['end'])==aware(study['windows']['TRAIN']['end'])


def test_renamed_dataset_cannot_reset_study_protocol(lab):
    lab,study,_=lab;fixture=synthetic_fixture();fixture.manifest.name='Renamed controlled dataset with identical observations'
    alias=lab.research.catalog.import_data(fixture)
    with pytest.raises(ValueError,match='already has a study'):lab.create_study(dict(name='Attempted alias protocol',dataset_id=alias['id']))


def test_optimistic_fill_ordering_cannot_qualify_for_paper_approval(lab):
    lab,study,_=lab
    changed=lab.study(study['id']);changed['config']['execution']['bar_ambiguity']='FAVORABLE_FIRST'
    with lab.transaction():lab._save('studies',changed)
    version=add(lab,changed)
    assert any('favorable-first' in reason for reason in lab.evidence(version['id'])['approval_blockers'])


def test_dispatch_does_not_reuse_another_engine_build(lab):
    lab,study,_=lab;version=add(lab,study);evaluation=lab.evaluate(version['id'],'build-cache-guard')
    old=lab.research.create(evaluation['jobs'][0]['config'])
    with lab.research.catalog.lock,lab.research.catalog.db:
        row=lab.research.catalog.db.execute('SELECT document FROM runs WHERE id=?',(old['id'],)).fetchone()
        metadata=json.loads(row[0]);metadata['engine_hash']='0'*64
        lab.research.catalog.db.execute('UPDATE runs SET document=? WHERE id=?',(canonical(metadata),old['id']))
    lab.tick();updated=next(e for e in lab.evaluations() if e['id']==evaluation['id'])
    assert updated['jobs'][0]['run_id']!=old['id'] and len(lab.research.runs())==2
    lab.control(evaluation['id'],'CANCEL')


def test_screening_budget_preserves_one_final_holdout_run(lab):
    lab,study,_=lab;changed=lab.study(study['id']);changed['config']['max_runs']=16
    with lab.transaction():lab._save('studies',changed)
    first=add(lab,changed);lab.evaluate(first['id'],'budget-screen-first')
    second=add(lab,changed,parent_version_id=first['id'])
    with pytest.raises(ValueError,match='run budget'):lab.evaluate(second['id'],'budget-screen-second')
    assert lab.study(study['id'])['reserved_runs']==8


def test_forward_activation_packet_requires_current_approval_and_intact_hash(lab):
    lab,study,_=lab;version=add(lab,study)
    with pytest.raises(ValueError,match='not currently paper-approved'):lab.paper_packet(version['id'])
    packet=dict(id='controlled-packet',version_id=version['id'],study_id=study['id'],protocol_hash=study['protocol_hash'],data_review=study['data_review'],strategy=version['strategy'],mode='PAPER_REVIEW_PACKET',live_orders_enabled=False)
    packet['packet_hash']=digest(packet)
    with lab.transaction():lab._save('packets',packet);lab._set_state(version['id'],'PAPER_APPROVED')
    assert lab.paper_packet(version['id'])['packet_hash']==packet['packet_hash']
    with lab.transaction():lab._set_state(version['id'],'RETIRED')
    with pytest.raises(ValueError,match='not currently paper-approved'):lab.paper_packet(version['id'])
