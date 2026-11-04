"""End-to-end forward browser acceptance with isolated, explicitly controlled providers."""
import copy
from datetime import timedelta
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
BASE='http://127.0.0.1:8795'

def serve(database):
    import uvicorn
    from app.api import create_app
    from app.market import KiteData
    from app.forward.drills import DrillKite,DrillHub,AT,quote
    from app.forward.provider import MonitorProvider
    from app.research.catalog import aware,iso,bounds
    for name in ('KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN','ARTHA_AI_API_KEY','ARTHA_AI_MODEL','ARTHA_TELEGRAM_BOT_TOKEN','ARTHA_TELEGRAM_CHAT_ID'):os.environ.pop(name,None)
    def broker(self,method,path,**kwargs):
        assert (method,path)==('GET','/instruments'),'Controlled browser cannot call a real broker endpoint'
        return 'exchange,tradingsymbol,instrument_type,segment,name,expiry,strike,lot_size,tick_size,instrument_token\nNSE,DRILL,EQ,NSE,CONTROLLED BROWSER,,0,1,0.05,257\n'
    KiteData._request=broker
    app=create_app(database);service=app.state.forward;start=time.monotonic()
    def clock():return AT+timedelta(seconds=int((time.monotonic()-start)*30))
    service.clock=clock
    class Hub(DrillHub):
        fault_started=None
        def status(self):
            if service.alert_policy()['enabled'] and self.fault_started is None:self.fault_started=time.monotonic()
            self.ready=not(self.fault_started and time.monotonic()-self.fault_started<6)
            return super().status()
        def forward_quotes(self,items):return {key:quote(clock()-timedelta(seconds=1),100,100) for key in items}
    service.kite=DrillKite();service.hub=Hub()
    class Model(MonitorProvider):
        def request(self,context,tokens):
            return dict(summary='Controlled AI explanation of paper portfolio health',findings=[dict(severity='INFO',statement='Paper observations require operator review',evidence_ids=[context['facts'][0]['id']],proposed_action='OBSERVE')],limitations=['Controlled browser provider; no genuine model call']),dict(provider='CONTROLLED_TEST',model=self.model,live_provider_response=False,input_tokens=20,output_tokens=30,raw_output='Controlled structured explanation')
    service.provider=Model();service.alert_provider.send=lambda text:dict(confirmed=True,message_id=12345,controlled_provider=True)
    def history(doc,state,warmup,cutoff):
        from zoneinfo import ZoneInfo
        cutoff=aware(cutoff);span=bounds(doc['manifest']['calendar'],cutoff.astimezone(ZoneInfo('Asia/Kolkata')).date());step=doc['config']['interval_minutes']
        end=span[0]+timedelta(minutes=int((cutoff-span[0]).total_seconds())//(step*60)*step)
        ends=[end-timedelta(minutes=step*i) for i in range(30,-1,-1)] if warmup else [end]
        return [dict(instrument_id=key,timestamp=iso(at),open_at=iso(at-timedelta(minutes=step)),open=100.,high=101.,low=99.,close=100.,volume=1000,source='CONTROLLED_COMPLETED_CANDLE') for at in ends if at>span[0] for key in doc['bindings']]
    service._history=history
    uvicorn.run(app,host='127.0.0.1',port=8795,access_log=False,log_level='error')

def main():
    from playwright.sync_api import sync_playwright,expect
    class Checks(list):
        def append(self,value):super().append(value);print(json.dumps({'passed_stage':value}),flush=True)
    checks=Checks();errors=[]
    with tempfile.TemporaryDirectory(prefix='artha-forward-browser-',dir=ROOT/'data') as directory:
        process=multiprocessing.get_context('spawn').Process(target=serve,args=(str(Path(directory)/'paper.sqlite3'),));process.start()
        try:
            for _ in range(150):
                try:urllib.request.urlopen(BASE+'/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Controlled forward browser server failed to start')
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport=dict(width=1440,height=1100));page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(BASE+'/#forward',wait_until='networkidle');expect(page.locator('#view-forward')).to_be_visible()
                expect(page.locator('#forward-status')).to_contain_text('AI not configured',timeout=10000);checks.append('honest unconfigured AI and paper-only workspace')
                expect(page.locator('#forward-forge')).to_contain_text('NOT_CONNECTED');expect(page.locator('#forward-forge')).to_contain_text('1,000-user target');checks.append('Centaur positioning exposes missing research feeds and commercial targets honestly')
                before=page.request.get(BASE+'/api/state').json()
                page.locator('[data-forward-tab="create"]').click();expect(page.locator('#forward-protocol')).not_to_have_value('',timeout=10000)
                page.locator('#forward-create details summary').click()
                status=page.request.get(BASE+'/api/forward/status').json();from app.forward.drills import AT
                from app.research.catalog import aware,iso
                at=aware(status['worker']['last_tick'])+timedelta(minutes=2)
                body=json.loads(page.locator('#forward-protocol').input_value());body['strategies']=[dict(id='browser',name='Controlled prospective scheduled reference',family='SCHEDULED',signal_instruments=['DRILL'],legs=[dict(instrument_id='DRILL')],size_mode='LOTS',lots=3,parameters=dict(hold_bars=1000),schedule=[dict(at=iso(at),action='ENTER')])]
                body['benchmark_id']='DRILL';page.locator('#forward-protocol').fill(json.dumps(body))
                page.locator('#forward-new-name').fill('Controlled forward browser experiment');page.locator('#forward-new-capital').fill('250000');page.locator('#forward-bindings').fill('DRILL=NSE:DRILL');page.locator('#forward-interval').select_option('1');page.locator('#forward-reviewer').fill('Controlled browser reviewer');page.locator('#forward-review-note').fill('Controlled software acceptance; no trading edge or supplier qualification is claimed.');page.locator('#forward-rights').check();page.locator('#forward-assumptions').check();page.locator('#forward-create-button').click()
                expect(page.locator('#forward-name')).to_have_text('Controlled forward browser experiment',timeout=15000);expect(page.locator('#forward-caption')).to_contain_text('PAUSED');checks.append('immutable reference portfolio created paused through reviewed form')
                expect(page.locator('#forward-metrics')).to_contain_text('2,50,000');checks.append('configurable virtual capital visible in portfolio account')
                page.locator('#forward-warmup').click();expect(page.locator('#forward-jobs')).to_contain_text('SUCCEEDED',timeout=15000);checks.append('completed-candle warm-up queued and applied without fills')
                page.locator('[data-forward-control="RESUME"]').click();expect(page.locator('#forward-caption')).to_contain_text('RUNNING',timeout=15000);checks.append('explicit reviewed forward activation')
                expect(page.locator('#forward-positions')).to_contain_text('DRILL',timeout=25000);checks.append('completed-bar decision produced a subsequent-quote simulated fill')
                page.locator('#forward-event-kind').select_option('FILL');expect(page.locator('#forward-events tbody tr').first).to_contain_text('FILL',timeout=10000);page.locator('[data-forward-event]').first.click();expect(page.locator('#forward-event-detail')).to_contain_text('Simulated fill');checks.append('traceable fill evidence and bounded event filtering')
                expect(page.locator('#forward-report')).to_contain_text('CONTINUE_PAPER_OR_REVISE');checks.append('reference and insufficient prospective evidence cannot qualify for Phase 5')
                response=page.request.get(BASE+page.locator('#forward-export').get_attribute('href'));export=response.json();assert export['live_orders_enabled'] is False and export['report']['ready_for_phase5_review'] is False;checks.append('review evidence export preserves simulation and qualification limits')
                page.locator('[data-forward-control="PAUSE"]').click();expect(page.locator('#forward-caption')).to_contain_text('PAUSED',timeout=10000);page.locator('[data-forward-control="LIQUIDATE"]').click();expect(page.locator('#forward-positions')).to_contain_text('No records yet',timeout=15000);checks.append('pause and actual reduce-only liquidation lifecycle')
                page.locator('[data-forward-control="RETIRE"]').click();expect(page.locator('#forward-caption')).to_contain_text('RETIRED',timeout=10000);page.locator('#forward-guardian-capture').click();page.locator('#forward-guardian-reviewer').fill('Controlled guardian');page.locator('#forward-guardian-note').fill('Reviewed the displayed prospective paper evidence and its sample limitations.');page.locator('#forward-guardian-conflicts').fill('Controlled local owner; professional licensing not verified');page.locator('#forward-guardian button[type=submit]').click();expect(page.locator('#forward-guardian-status')).to_contain_text('CONTINUE_PAPER',timeout=10000);checks.append('human guardian review is recorded from the displayed evidence without execution authority')
                page.locator('#forward-drills').click();expect(page.locator('#toast')).to_contain_text('drills passed',timeout=15000);checks.append('isolated fault and restore drills through the operator dashboard')
                # Retired books retain review evidence; a separate nonretired book
                # receives the following controlled connectivity/alert exercise.
                alert_body=page.request.get(BASE+'/api/forward/portfolios').json()[0]['document']['config']
                alert_body.update(request_id='browser-alert-recipient',name='Controlled alert recipient')
                now=aware(page.request.get(BASE+'/api/forward/status').json()['worker']['last_tick'])
                alert_body['strategies'][0]['schedule']=[dict(at=iso(now+timedelta(hours=1)),action='ENTER')]
                created=page.request.post(BASE+'/api/forward/portfolios',data=alert_body,headers={'X-Artha-Client':'paper-workspace'});assert created.ok,created.text()
                page.locator('[data-forward-select="'+created.json()['id']+'"]').click(timeout=10000)
                expect(page.locator('#forward-name')).to_have_text('Controlled alert recipient')
                page.locator('[data-forward-tab="monitoring"]').click();page.locator('#forward-ai-key').fill('CONTROLLED_BROWSER_AI_KEY');page.locator('#forward-ai-model').fill('controlled-browser-model');page.locator('#forward-ai-config button[type="submit"]').click();expect(page.locator('#forward-ai-key')).to_have_value('');expect(page.locator('#forward-status')).to_contain_text('AI configured',timeout=10000);checks.append('private dedicated AI configuration without restart or secret echo')
                page.locator('[data-forward-tab="portfolios"]').click();page.locator('#forward-monitor').click();expect(page.locator('#forward-jobs')).to_contain_text('Controlled AI explanation',timeout=15000);checks.append('bounded advisory-only model job and visible output provenance')
                page.locator('[data-forward-tab="monitoring"]').click();page.locator('#forward-alert-token').fill('123456:CONTROLLED_BROWSER_ALERT_TOKEN');page.locator('#forward-alert-chat').fill('12345');page.locator('#forward-alert-enabled').check();page.locator('#forward-alert-consent').check();page.locator('#forward-alert-config button[type="submit"]').click();expect(page.locator('#forward-alert-token')).to_have_value('');expect(page.locator('#forward-alert-chat')).to_have_value('');expect(page.locator('#forward-alert-outbox')).to_contain_text('SENT',timeout=15000);checks.append('explicitly authorized controlled notification delivery with cleared private fields')
                page.locator('[data-forward-tab="portfolios"]').click();expect(page.locator('#forward-incidents')).to_contain_text('MARKET_TRANSPORT_NOT_READY',timeout=15000);page.locator('[data-review-action="ACKNOWLEDGE"]').first.click();expect(page.locator('#forward-incidents')).to_contain_text('ACKNOWLEDGED',timeout=10000);checks.append('durable incident acknowledgement without risk bypass')
                page.screenshot(path=str(ROOT/'docs/screenshots/forward-controlled-desktop.png'),full_page=True);page.set_viewport_size(dict(width=390,height=844));page.screenshot(path=str(ROOT/'docs/screenshots/forward-controlled-mobile.png'),full_page=True);assert page.locator('body').bounding_box()['width']<=390;checks.append('usable narrow-screen forward workspace')
                after=page.request.get(BASE+'/api/state').json();assert after['account']==before['account'] and after['orders']==before['orders'] and after['mode']=='PAPER' and not after['live_trading_available'];checks.append('main paper account isolated and live execution unavailable')
                assert not errors,errors;browser.close()
            result=dict(passed=len(checks),checks=checks,javascript_errors=errors,controlled_broker=True,controlled_model=True,controlled_notification=True,accelerated_clock_for_software_tests_only=True,real_provider_validation=False)
            (ROOT/'docs/phase-4-browser-validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
        finally:
            process.terminate();process.join(15)
            if process.is_alive():process.kill();process.join()

if __name__=='__main__':main()
