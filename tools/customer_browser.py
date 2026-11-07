"""MVP-5 controlled two-user browser workflow; all stores, clocks and money are fictional."""
from datetime import datetime
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.research.catalog import digest
from tools.workspace_fingerprint import fingerprints

PASSWORD="Controlled customer browser password 12345"


def serve(root):
    import uvicorn
    from app.team.api import create_team_app
    from app.research.catalog import aware
    for key in ('KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN','ARTHA_AI_API_KEY','OPENAI_API_KEY','ARTHA_LIVE_TRANSPORT_ENABLED'):
        os.environ.pop(key,None)
    current=[aware('2026-11-07T10:00:00+00:00')]
    app=create_team_app(root,clock=lambda:current[0].timestamp())
    @app.post('/fixture/clock')
    def fixture_clock(body:dict):
        current[0]=aware(body['at']);return dict(controlled_clock=True)
    uvicorn.run(app,host='127.0.0.1',port=8797,access_log=False,log_level='error',timeout_graceful_shutdown=3)


def main():
    from playwright.sync_api import sync_playwright,expect
    from app.research.fixtures import contract_fixture
    checks=[];errors=[];base='http://127.0.0.1:8797'
    def passed(message):
        checks.append(message);print(json.dumps(dict(passed_stage=message)),flush=True)
    with tempfile.TemporaryDirectory(prefix='artha-customer-browser-',dir=ROOT/'data') as tmp:
        root=Path(tmp)/'organization'
        process=multiprocessing.get_context('spawn').Process(target=serve,args=(str(root),));process.start()
        try:
            for _ in range(100):
                try:urllib.request.urlopen(base+'/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Controlled customer server did not start')
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
                author=browser.new_page(viewport=dict(width=1440,height=1100))
                reviewer=browser.new_page(viewport=dict(width=1440,height=1100))
                for page in (author,reviewer):page.on('pageerror',lambda error:errors.append(str(error)))
                def post(page,path,body):
                    session=page.request.get(base+'/api/team/session').json()
                    result=page.request.post(base+'/api/team'+path,data=body,headers={'X-Artha-Client':'paper-workspace','X-Artha-CSRF':session['csrf']})
                    assert result.ok,result.text();return result.json()
                def clock(at):
                    assert author.request.post(base+'/fixture/clock',data=dict(at=at)).ok
                def sign_in(page,email):
                    page.goto(base+'/team',wait_until='networkidle')
                    if page.locator('#auth-email').count():
                        page.locator('#auth-email').fill(email);page.locator('#auth-password').fill(PASSWORD);page.locator('#auth-submit').click()
                    page.locator('[data-tab="customer"]').click()
                    expect(page.locator('#cp-engagement')).to_be_visible(timeout=15000)
                author.goto(base+'/team',wait_until='networkidle')
                author.locator('#setup-name').fill('CONTROLLED MVP-5 EXERCISE')
                author.locator('#setup-key').fill((root/'bootstrap.key').read_text())
                author.locator('#auth-email').fill('author@example.test');author.locator('#auth-password').fill(PASSWORD);author.locator('#auth-submit').click()
                author.locator('[data-tab="customer"]').click()
                expect(author.locator('#cp-engagement-form')).to_be_visible()
                author.locator('#cp-name').fill('Controlled workflow <img src=x onerror=alert(1)>')
                author.locator('#cp-baseline-reference').fill('Controlled 600-second baseline')
                author.locator('#cp-workflow-reference').fill('Controlled customer workflow artifact')
                author.locator('#cp-engagement-form button').click()
                expect(author.locator('#cp-engagement')).not_to_have_value('',timeout=15000)
                assert author.locator('#panel img').count()==0
                passed('Administrator creates an explicit controlled engagement; untrusted names render as text')
                definition,body=contract_fixture()
                definition['rules']['entry_at']='2026-11-09T09:30:00+05:30';definition['rules']['exit_at']='2026-11-09T15:00:00+05:30'
                dataset=post(author,'/datasets',body.model_dump(mode='json'))
                saved=post(author,'/contracts',dict(request_id='controlled-future-definition',definition=definition,dataset_id=dataset['id']))
                protocol=post(author,'/protocols',dict(request_id='controlled-future-protocol',contract_id=saved['id'],dataset_id=dataset['id'],observation_cutoff='2026-11-09T15:00:10+05:30',allow_unverified_data=True,max_trials=6))
                trial=post(author,'/trials',dict(request_id='controlled-preentry-trial',protocol_id=protocol['id'],kind='BASELINE'))
                for _ in range(100):
                    details=author.request.get(base+'/api/team/protocols/'+protocol['id']).json()
                    if details['trials'][0]['status'] in {'COMPLETED','FAILED','CANCELLED'}:break
                    time.sleep(.1)
                else:raise RuntimeError('Controlled pre-entry research attempt did not terminate')
                packet=post(author,'/packets',dict(request_id='controlled-future-capture',protocol_id=protocol['id']))
                author.locator('#cp-refresh').click()
                author.locator('#cp-cycle-form').locator('xpath=..').locator('summary').click()
                author.locator('#cp-packet').select_option(packet['id'])
                author.locator('#cp-question').fill('Controlled repeated options review question')
                author.locator('#cp-cycle-form button').click()
                expect(author.locator('#cp-timer-start')).to_be_visible(timeout=15000)
                cycle_id=author.locator('#cp-cycle').input_value()
                passed('Customer cycle binds the exact current future-dated research packet without inventing successful historical fills')
                invite=post(author,'/members',dict(email='reviewer@example.test',name='Controlled independent reviewer',roles=['REVIEWER']))
                reviewer.goto(base+'/team#invite='+invite['invitation_token'],wait_until='networkidle')
                reviewer.locator('#auth-password').fill(PASSWORD);reviewer.locator('#auth-submit').click()
                expect(reviewer.locator('#auth-email')).to_be_visible()
                sign_in(reviewer,'reviewer@example.test')
                expect(reviewer.locator('#cp-finance-form')).to_have_count(0)
                reviewer.locator('#cp-timer-start').click()
                expect(reviewer.locator('#cp-timer-abandon')).to_be_enabled()
                clock('2026-11-07T10:00:10+00:00')
                reviewer.locator('#cp-open-review').click()
                reviewer.locator('#review-comment').fill('Controlled prospective-specification review; source and execution remain unqualified.')
                reviewer.locator('#submit-review').click()
                expect(reviewer.locator('#message')).to_contain_text('recorded',timeout=15000)
                reviewer.locator('[data-tab="customer"]').click()
                own=reviewer.request.get(base+'/api/team/customer/dashboard').json()['review_options'][0]['id']
                reviewer.locator('#cp-review').select_option(own);reviewer.locator('#cp-timer-finish').click()
                expect(reviewer.locator('#panel')).to_contain_text('10 seconds',timeout=15000)
                passed('Independent reviewer records a snapshot decision and an actual server-clock elapsed timer; finance stays administrator-only')
                author.locator('#cp-refresh').click()
                author.locator('#cp-plan-review').select_option(own);author.locator('#cp-paper-reference').fill('Controlled separately operated paper book; no orders dispatched by this journal')
                author.locator('#cp-plan-form button').click()
                expect(author.locator('#cp-observation-form')).to_be_visible(timeout=15000)
                author.locator('#cp-observation-note').fill('This note is deliberately too early')
                author.locator('#cp-observation-reference').fill('Controlled pre-start note')
                author.locator('#cp-observation-form button').click()
                expect(author.locator('#cp-message')).to_contain_text('after the planned start')
                passed('Observation is precommitted before the future fixed entry; early observations fail without dispatching an engine or broker action')
                clock('2026-11-09T04:01:00+00:00');sign_in(author,'author@example.test')
                author.locator('#cp-observation-kind').select_option('DATA_GAP')
                author.locator('#cp-observation-note').fill('Controlled source gap with no invented quote or P&L')
                author.locator('#cp-observation-reference').fill('Controlled tape gap reference')
                author.locator('#cp-observation-form button').click()
                expect(author.locator('#cp-observations')).to_contain_text('DATA_GAP',timeout=15000)
                sign_in(reviewer,'reviewer@example.test')
                reviewer.locator('#cp-correction-form').locator('xpath=..').locator('summary').click()
                reviewer.locator('#cp-correction-note').fill('Controlled independent assessment confirms missing source evidence')
                reviewer.locator('#cp-correction-reference').fill('Controlled independent gap evidence')
                reviewer.locator('#cp-correction-form button').click()
                expect(reviewer.locator('#cp-observations')).to_contain_text('CONFIRMED',timeout=15000)
                passed('Researcher appends a source gap and another reviewer assesses the discrepancy without rewriting history')
                clock('2026-11-09T09:30:11+00:00');sign_in(author,'author@example.test')
                author.locator('#cp-observation-kind').select_option('ENDED')
                author.locator('#cp-observation-note').fill('Controlled observation period ended after the declared cutoff')
                author.locator('#cp-observation-reference').fill('Controlled paper book closing evidence')
                author.locator('#cp-observation-form button').click()
                expect(author.locator('#cp-observation-form')).to_have_count(0,timeout=15000)
                author.locator('#cp-effort-minutes').fill('15');author.locator('#cp-effort-reference').fill('Controlled onboarding timesheet');author.locator('#cp-effort-form button').click();expect(author.locator('#cp-effort-minutes')).to_have_value('',timeout=15000)
                author.locator('#cp-integration').fill('vendor-import');author.locator('#cp-problem').fill('Controlled repeated manual data import effort');author.locator('#cp-demand-reference').fill('Controlled demand note');author.locator('#cp-demand-form button').click();expect(author.locator('#cp-integration')).to_have_value('',timeout=15000)
                author.locator('#cp-finance-amount').fill('100.25')
                author.locator('#cp-finance-reference').fill('controlled-invoice-001')
                author.locator('#cp-period-start').fill('2026-11-01T00:00');author.locator('#cp-period-end').fill('2026-12-01T00:00')
                author.locator('#cp-finance-evidence').fill('Controlled invoice evidence; no actual invoice issued')
                author.locator('#cp-finance-form button').click()
                expect(author.locator('#cp-invoice option')).to_have_count(2,timeout=15000)
                admin_data=author.request.get(base+'/api/team/customer/dashboard').json()
                invoice=next(r for r in admin_data['records'] if r['core']['kind']=='INVOICE')
                assert invoice['core']['amount_paise']==10025
                author.locator('#cp-finance-kind').select_option('PAYMENT')
                author.locator('#cp-invoice').select_option(invoice['id']);author.locator('#cp-finance-amount').fill('100.25')
                author.locator('#cp-finance-reference').fill('controlled-bank-receipt-001');author.locator('#cp-finance-evidence').fill('Controlled receipt evidence; no payment collected')
                author.locator('#cp-finance-form button').click()
                expect(author.locator('#cp-finance-kind')).to_have_value('INVOICE',timeout=15000)
                author.locator('#cp-finance-kind').select_option('COST')
                author.locator('#cp-finance-amount').fill('2.50');author.locator('#cp-finance-reference').fill('controlled-mistyped-cost')
                author.locator('#cp-finance-evidence').fill('Controlled mistaken cost entry')
                author.locator('#cp-finance-form button').click()
                expect(author.locator('#cp-finance-kind')).to_have_value('INVOICE',timeout=15000)
                history=author.request.get(base+'/api/team/customer/dashboard').json()['records']
                cost=next(r for r in history if r['core']['kind']=='COST')
                author.locator('#cp-finance-kind').select_option('REVERSE_ENTRY')
                author.locator('#cp-target-record').select_option(cost['id'])
                author.locator('#cp-finance-reference').fill('controlled-cost-correction')
                author.locator('#cp-finance-evidence').fill('Controlled correction of a typo; no money movement')
                author.locator('#cp-finance-form button').click()
                expect(author.locator('#cp-finance-kind')).to_have_value('INVOICE',timeout=15000)
                history=author.request.get(base+'/api/team/customer/dashboard').json()['records']
                assert any(r['core']['kind']=='REVERSE_ENTRY' and r['core']['target_record_id']==cost['id'] for r in history)
                passed('After the controlled interval, the administrator records effort, integration demand, exact-paise invoice and receipt evidence')
                with author.expect_download() as download:
                    author.locator('#cp-export').click()
                report_path=Path(download.value.path());report=json.loads(report_path.read_text())
                assert digest(report['core'])==report['report_hash']
                assert report['core']['controlled_exercises_excluded'] and report['core']['completed_journeys']==0
                assert not report['core']['paying_organization'] and report['core']['commercial']['net_receipts_paise']==0
                result=subprocess.run([sys.executable,'-B',str(ROOT/'tools/customer_cohort.py'),str(report_path)],capture_output=True,text=True,timeout=20)
                assert result.returncode!=0 and 'Future-dated cohort report' in result.stderr,result.stdout+result.stderr
                passed('Exact report download excludes controlled traction and receipts; the cohort tool rejects the advanced fixture clock as future evidence')
                author.screenshot(path=str(ROOT/'docs/screenshots/mvp5-customer-desktop-controlled.png'),full_page=True)
                author.set_viewport_size(dict(width=390,height=844))
                assert author.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                author.screenshot(path=str(ROOT/'docs/screenshots/mvp5-customer-mobile-controlled.png'),full_page=True)
                assert not errors,errors
                browser.close()
        finally:
            process.terminate();process.join(10)
            if process.is_alive():process.kill();process.join()
    record=dict(passed=True,checks=checks,javascript_errors=errors,identity=fingerprints(),controlled_clocks=True,
        controlled_customer_and_commercial_records=True,real_customers_onboarded=False,real_invoices_issued=False,real_payments_collected=False,real_orders_sent=False)
    (ROOT/'docs/customer-browser-validation.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(dict(passed=True,checks=checks,javascript_errors=errors)))


if __name__=='__main__':main()
