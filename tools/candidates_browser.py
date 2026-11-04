"""Real Chromium + real research workers, with an explicitly controlled model and dataset."""
import asyncio
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def serve(database):
    import uvicorn
    from app.api import create_app
    for name in ('KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN','ARTHA_AI_API_KEY','ARTHA_AI_MODEL'):os.environ.pop(name,None)
    app=create_app(database)
    async def no_external_market(stop):await stop.wait()
    app.state.market_hub.run=no_external_market
    class ControlledModel:
        def status(self):return dict(configured=True,provider='CONTROLLED_TEST',model='CONTROLLED_TEST_MODEL',message='Controlled browser fixture; no genuine AI request or market validation',tools_enabled=False)
        def request(self,idea,context,tokens):
            asset=next(i['id'] for i in context['instruments'] if i['kind']=='EQ')
            spec=dict(id='controlled-ai',name='Controlled AI proposal',family='TREND',legs=[dict(instrument_id=asset)],signal_instruments=[asset],parameters=dict(fast=3,slow=8),stop_pct=2)
            return dict(hypothesis='Controlled research hypothesis only; no empirical trading edge is claimed.',strategy_json=json.dumps(spec),risks=['CONTROLLED TEST DATA: no genuine market qualification'],assumptions=['CONTROLLED TEST MODEL: no external AI was called']),dict(provider='CONTROLLED_TEST',model='CONTROLLED_TEST_MODEL',live_provider_response=False,input_tokens=10,output_tokens=20,raw_output=json.dumps(spec))
    app.state.candidates.provider=ControlledModel()
    uvicorn.run(app,host='127.0.0.1',port=8793,access_log=False,log_level='error')


def main():
    from playwright.sync_api import sync_playwright,expect
    import re
    checks=[]
    with tempfile.TemporaryDirectory(prefix='artha-phase3-browser-',dir=ROOT/'data') as directory:
        process=multiprocessing.get_context('spawn').Process(target=serve,args=(str(Path(directory)/'paper.sqlite3'),));process.start()
        try:
            for _ in range(150):
                try:urllib.request.urlopen('http://127.0.0.1:8793/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Controlled Strategy lab server did not start')
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport=dict(width=1440,height=1100));errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto('http://127.0.0.1:8793/#candidates',wait_until='networkidle')
                expect(page.locator('#view-candidates')).to_be_visible();expect(page.locator('#lab-status')).to_contain_text('CONTROLLED_TEST_MODEL');checks.append('Strategy lab and honest controlled provider status')
                page.locator('#lab-fixture').click();expect(page.locator('#lab-dataset option')).to_have_count(1,timeout=15000)
                page.locator('#lab-study-name').fill('Controlled AI candidate study');page.locator('#lab-study-form button[type=submit]').click()
                expect(page.locator('#lab-panel-builder')).to_be_visible(timeout=15000);expect(page.locator('#lab-protocol')).to_contain_text('UNOPENED');checks.append('immutable study and unopened holdout')
                page.locator('#lab-idea').fill('Propose a controlled trend research hypothesis for software validation only.')
                page.locator('#lab-generate').click();expect(page.locator('#lab-generations')).to_contain_text('SUCCEEDED',timeout=15000)
                expect(page.locator('#lab-generations')).to_contain_text('CONTROLLED_TEST');checks.append('asynchronous generation journal and provenance')
                page.locator('#lab-tabs [data-lab-panel=registry]').click();expect(page.locator('#lab-registry [data-lab-inspect]')).to_have_count(1)
                page.locator('#lab-registry [data-lab-revise]').click();expect(page.locator('#lab-parent')).to_contain_text('Revision of')
                value=json.loads(page.locator('#lab-spec').input_value());value['name']='Controlled authored revision';value['stop_pct']=2.1
                page.locator('#lab-spec').fill(json.dumps(value));page.locator('#lab-save-version').click()
                expect(page.locator('#lab-registry [data-lab-inspect]')).to_have_count(2,timeout=15000);checks.append('immutable revision and manual/AI origin separation')
                for box in page.locator('#lab-registry [data-lab-compare-id]').all():box.check()
                page.locator('#lab-compare').click();expect(page.locator('#lab-comparison')).to_contain_text('Controlled authored revision');checks.append('candidate comparison and protocol identities')
                page.locator('#lab-registry tbody tr').filter(has_text='Controlled authored revision').locator('[data-lab-inspect]').click()
                page.locator('#lab-screen').click();expect(page.locator('#lab-version-stage')).to_contain_text('SCREENED',timeout=120000)
                expect(page.locator('#lab-evaluations')).to_contain_text('REPRODUCE');expect(page.locator('#lab-evaluations')).to_contain_text('WALK_FORWARD_2');expect(page.locator('#lab-evaluations')).to_contain_text('COST_STRESS');checks.append('real worker screening, replay, walk-forward, cost and sensitivity evidence')
                page.once('dialog',lambda dialog:dialog.accept());page.locator('#lab-holdout').click()
                expect(page.locator('#lab-version-stage')).to_contain_text('HOLDOUT_REVIEW',timeout=60000)
                expect(page.locator('#lab-holdout')).to_be_disabled();expect(page.locator('#lab-generate')).to_be_disabled();checks.append('finalist freeze and single-use holdout')
                page.locator('#lab-data-reviewer').fill('Controlled browser reviewer');page.locator('#lab-data-note').fill('Controlled software-test assertions; no genuine provider data or trading edge was verified.')
                for name in ('source','calendar','charges','execution'):page.locator('#lab-data-'+name).check()
                page.locator('#lab-data-review button[type=submit]').click();expect(page.locator('#toast')).to_contain_text('Data/model review recorded');checks.append('independent data and model review record')
                page.locator('#lab-reviewer').fill('Controlled browser reviewer');page.locator('#lab-review-note').fill('Controlled decision record: synthetic data cannot qualify a candidate for forward paper trading.')
                page.locator('#lab-review-action').select_option('APPROVE_PAPER');page.locator('#lab-review button[type=submit]').click()
                expect(page.locator('#toast')).to_contain_text('Synthetic');checks.append('backend blocks synthetic-data paper approval')
                page.locator('#lab-review-action').select_option('REJECT');page.locator('#lab-review button[type=submit]').click()
                expect(page.locator('#lab-version-stage')).to_contain_text('REJECTED',timeout=10000);checks.append('recorded rejection retains candidate evidence')
                with page.expect_download() as download:page.locator('#lab-export').click()
                destination=Path(directory)/'candidate.json';download.value.save_as(destination);packet=json.loads(destination.read_text())
                assert packet['version']['status']=='REJECTED' and packet['version']['parent_version_id']
                assert not packet['live_orders_enabled'] and not packet['forward_trading_enabled'];checks.append('complete lineage and evidence export')
                page.screenshot(path=str(ROOT/'docs/screenshots/phase3-controlled-evidence.png'),full_page=True)
                page.locator('#lab-tabs [data-lab-panel=registry]').click();page.locator('#lab-audit').click();expect(page.locator('#lab-audit-evidence')).to_contain_text('"ok": true');checks.append('audit verification')
                state=page.request.get('http://127.0.0.1:8793/api/state').json()
                assert state['mode']=='PAPER' and not state['orders'] and not state['positions'] and state['reconcile']['ok'] and not state['live_trading_available'];checks.append('main account and execution isolation')
                page.set_viewport_size(dict(width=390,height=844));page.screenshot(path=str(ROOT/'docs/screenshots/phase3-controlled-mobile.png'),full_page=True)
                assert page.locator('body').bounding_box()['width']<=390;checks.append('mobile layout')
                page.reload(wait_until='networkidle');page.locator('#lab-tabs [data-lab-panel=registry]').click();expect(page.locator('#lab-registry')).to_contain_text('REJECTED',timeout=15000);checks.append('durable registry after browser reload')
                assert not errors,errors;browser.close()
            result=dict(controlled_model_and_data=True,real_ai_provider_validated=False,passed=len(checks),checks=checks,javascript_errors=errors)
            (ROOT/'docs/phase-3-browser-validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
        finally:
            process.terminate();process.join(15)
            if process.is_alive():process.kill();process.join()

if __name__=='__main__':main()
