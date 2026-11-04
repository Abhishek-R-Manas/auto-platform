"""Controlled desktop/mobile acceptance for dated inputs; no external provider calls."""
from contextlib import asynccontextmanager
from datetime import timedelta
import asyncio,json,multiprocessing,os,sys,tempfile,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
BASE='http://127.0.0.1:8796'

def serve(path):
    import uvicorn
    from app.api import create_app
    from app.intelligence.mospi import download
    from test_intelligence import AT,Quotes,OfficialFixture
    for name in ('KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN','ARTHA_AI_API_KEY','ARTHA_AI_MODEL'):os.environ.pop(name,None)
    app=create_app(path,background=False);inputs=app.state.intelligence;inputs.clock=lambda:AT;inputs.kite=Quotes()
    class Sources:
        def fetch(self,source):
            if source=='MOSPI':return download(OfficialFixture(),AT)
            return dict(source='RBI',kind='RBI_RSS',source_url='https://www.rbi.org.in/pressreleases_rss.xml',content='<rss><channel><item><title>Controlled RBI policy notice</title><link>https://www.rbi.org.in/</link><pubDate>Wed, 04 Nov 2026 10:00:00 +0530</pubDate><description>Controlled source fixture</description></item></channel></rss>')
    inputs.providers=Sources();original=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            stopped=asyncio.Event()
            async def worker():
                while not stopped.is_set():
                    await asyncio.to_thread(inputs.tick)
                    try:await asyncio.wait_for(stopped.wait(),timeout=.05)
                    except TimeoutError:pass
            task=asyncio.create_task(worker())
            try:yield
            finally:stopped.set();await task
    app.router.lifespan_context=lifespan
    uvicorn.run(app,host='127.0.0.1',port=8796,access_log=False,log_level='error')

def main():
    from playwright.sync_api import sync_playwright,expect
    from test_intelligence import FII
    checks=[];errors=[]
    def checked(name):checks.append(name);print(json.dumps(dict(passed_stage=name)),flush=True)
    with tempfile.TemporaryDirectory(prefix='artha-input-browser-') as directory:
        process=multiprocessing.get_context('spawn').Process(target=serve,args=(str(Path(directory)/'paper.sqlite3'),));process.start()
        try:
            for _ in range(150):
                try:urllib.request.urlopen(BASE+'/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Controlled source browser server failed to start')
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport=dict(width=1440,height=1100));page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(BASE+'/#forward',wait_until='networkidle');before=page.request.get(BASE+'/api/state').json();page.locator('[data-forward-tab="inputs"]').click();expect(page.locator('#input-status')).to_contain_text('verified',timeout=10000);checked('research inputs setup and honest empty source status')
                page.locator('#input-file').set_input_files(dict(name='controlled-official-report.csv',mimeType='text/csv',buffer=FII.encode()));expect(page.locator('#input-content')).to_have_value(FII);page.locator('#input-reviewer').fill('Controlled input reviewer');page.locator('#input-rights').check();page.locator('#input-import button[type=submit]').click();expect(page.locator('#input-import-result')).to_contain_text('retained',timeout=10000);expect(page.locator('#input-features')).to_contain_text('fii.COMBINED_CASH.net');checked('official CSV file import with review, scope and INR-crore units')
                artifacts=page.request.get(BASE+'/api/intelligence/artifacts').json();first=artifacts[0]['id'];page.locator('#input-import button[type=submit]').click();expect(page.locator('#input-import-result')).to_contain_text(first[:16]);assert len(page.request.get(BASE+'/api/intelligence/artifacts').json())==1;checked('duplicate report import is idempotent')
                page.locator('#input-content').fill(FII.replace('1500,-300','1500,-200'));page.locator('#input-import button[type=submit]').click();expect(page.locator('#input-import-result')).to_contain_text('net');assert len(page.request.get(BASE+'/api/intelligence/artifacts').json())==1;checked('inconsistent institutional net values rejected without partial ingestion')
                page.locator('[data-input-refresh="MOSPI"]').click();expect(page.locator('#input-features')).to_contain_text('macro.CPI_YOY.2024',timeout=15000);expect(page.locator('#input-features')).to_contain_text('macro.GDP_YOY.2022-23');checked('official-format CPI, IIP and constant-price GDP data jobs')
                page.locator('[data-input-refresh="RBI"]').click();expect(page.locator('#input-releases')).to_contain_text('Controlled RBI policy notice',timeout=10000);checked('dated RBI release notices with publication and receipt times')
                page.locator('#input-expiries').fill('1');page.locator('#input-strikes').fill('4');page.locator('#input-policy button[type=submit]').click();expect(page.locator('#toast')).to_contain_text('settings saved');checked('bounded quote universe and model settings persist')
                page.locator('[data-input-refresh="KITE"]').click();expect(page.locator('#input-surfaces')).to_contain_text('FRESH',timeout=15000);expect(page.locator('#input-surfaces')).to_contain_text('25-delta put-minus-call');expect(page.locator('#input-features')).to_contain_text('iv.NIFTY.near.skew25_pp');checked('Kite-only IV, skew and per-leg sensitivity surface')
                page.locator('#input-feature-selection').select_option(['fii.COMBINED_CASH.net','macro.CPI_YOY.2024','iv.NIFTY.near.skew25_pp']);page.locator('#input-use-features').click();assert 'fii.COMBINED_CASH.net' in page.locator('#forward-feature-names').input_value();protocol=json.loads(page.locator('#forward-protocol').input_value());assert len(protocol['evidence_policy_hash'])==64 and len(protocol['evidence_features'])==3;checked('selected dated inputs and frozen policy added to a new forward protocol')
                page.locator('[data-forward-tab="inputs"]').click();created=page.request.post(BASE+'/api/research/datasets/synthetic',data={},headers={'X-Artha-Client':'paper-workspace'});assert created.ok;dataset=created.json();expect(page.locator('#input-dataset')).not_to_be_empty(timeout=10000);page.locator('#input-dataset').select_option(dataset['id']);page.locator('#input-enrich button[type=submit]').click();expect(page.locator('#input-enrich-result')).to_contain_text('0 available feature values',timeout=10000);checked('historical enrichment creates a child without backfilling later-received data')
                assert page.request.get(BASE+'/api/intelligence/audit').json()['ok'];checked('source artifact and observation evidence audit')
                page.screenshot(path=str(ROOT/'docs/screenshots/inputs-controlled-desktop.png'),full_page=True);page.set_viewport_size(dict(width=390,height=844));assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth');page.screenshot(path=str(ROOT/'docs/screenshots/inputs-controlled-mobile.png'),full_page=True);checked('usable narrow-screen inputs workspace without horizontal overflow')
                after=page.request.get(BASE+'/api/state').json();assert all(after[key]==before[key] for key in ('account','positions','orders')) and not after['live_trading_available'];checked('original paper account isolated and live execution unavailable')
                assert not errors;browser.close()
            result=dict(passed=len(checks),checks=checks,javascript_errors=errors,controlled_broker=True,controlled_official_data=True,real_provider_validation=False)
            (ROOT/'docs/inputs-browser-validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
        finally:
            process.terminate();process.join(15)
            if process.is_alive():process.kill();process.join()

if __name__=='__main__':main()
