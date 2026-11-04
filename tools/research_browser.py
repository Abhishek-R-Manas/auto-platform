"""End-to-end Research lab acceptance using a disposable account and real Chromium."""
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
    for key in ('KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN'): os.environ.pop(key,None)
    uvicorn.run(create_app(database,background=False),host='127.0.0.1',port=8791,access_log=False,log_level='error')


def main():
    from playwright.sync_api import sync_playwright,expect
    artifacts=ROOT/'docs/screenshots';artifacts.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='artha-research-browser-',dir=ROOT/'data') as directory:
        process=multiprocessing.get_context('spawn').Process(target=serve,args=(str(Path(directory)/'paper.sqlite3'),))
        process.start()
        try:
            for _ in range(150):
                try:
                    urllib.request.urlopen('http://127.0.0.1:8791/api/health',timeout=1).close();break
                except OSError: time.sleep(.1)
            else: raise RuntimeError('Research browser server did not start')
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport=dict(width=1440,height=1100),device_scale_factor=1)
                errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('console',lambda m:errors.append(m.text) if m.type=='error' else None)
                page.goto('http://127.0.0.1:8791/#research',wait_until='networkidle')
                expect(page.locator('#view-research')).to_be_visible()
                expect(page.locator('#research-datasets')).to_contain_text('No historical datasets')
                page.get_by_role('button',name='Create synthetic test dataset',exact=True).click()
                expect(page.locator('#research-datasets')).to_contain_text('SYNTHETIC',timeout=15000)
                page.locator('[data-research-dataset]').first.click()
                expect(page.locator('#research-config')).not_to_have_value('')
                page.get_by_role('button',name='Check capabilities',exact=True).click()
                expect(page.locator('#research-preflight-result')).to_contain_text('Runnable with stated assumptions')
                expect(page.locator('#research-preflight-result')).to_contain_text('SYNTHETIC TEST DATA')
                page.screenshot(path=str(artifacts/'research-builder.png'),full_page=True)
                page.get_by_role('button',name='Queue research run',exact=True).click()
                expect(page.locator('#research-runs')).to_contain_text('COMPLETED',timeout=45000)
                page.locator('[data-research-run]').first.click()
                expect(page.locator('#research-report')).to_contain_text('Cash ledger reconciled',timeout=15000)
                expect(page.locator('#research-report')).to_contain_text('Synthetic fixture')
                page.screenshot(path=str(artifacts/'research-report.png'),full_page=True)
                page.locator('#research-event-kind').select_option('FILL')
                expect(page.locator('#research-events')).to_contain_text('Simulated fill')
                page.locator('[data-research-event]').first.click()
                expect(page.locator('#research-event-detail')).to_contain_text('reference')
                with page.expect_download() as download:
                    page.get_by_role('button',name='Export report',exact=True).click()
                assert download.value.suggested_filename=='artha-research-report.json'
                page.get_by_role('button',name='Prepare paper review',exact=True).click()
                expect(page.locator('#research-handoff-result')).to_contain_text('Paper review packet saved')
                page.locator('[data-research-tab="build"]').click()
                page.get_by_role('button',name='Queue cost sensitivity · 3 runs',exact=True).click()
                expect(page.locator('#research-runs tbody tr')).to_have_count(4,timeout=15000)
                expect(page.locator('#research-runs tbody tr').filter(has_text='COMPLETED')).to_have_count(4,timeout=60000)
                page.locator('[data-research-tab="compare"]').click()
                page.get_by_role('button',name='Compare selected',exact=True).click()
                expect(page.locator('#research-comparison')).to_contain_text('variant 1',timeout=15000)
                page.screenshot(path=str(artifacts/'research-compare.png'),full_page=True)
                page.locator('[data-research-tab="data"]').click()
                page.locator('#research-manifest-example').click()
                manifest=json.loads(page.locator('#research-manifest').input_value())
                manifest.update(name='Browser imported fixture',source='EXPLICIT_BROWSER_TEST',source_kind='SYNTHETIC')
                page.locator('#research-manifest').fill(json.dumps(manifest))
                page.locator('#research-file').set_input_files(dict(name='fixture.csv',mimeType='text/csv',buffer=b'instrument_id,timestamp,open,high,low,close,volume\nNSE:ASSET,2023-01-02,100,101,99,100,1000\nNSE:ASSET,2023-01-03,101,102,100,101,1000\n'))
                page.get_by_role('button',name='Import & validate dataset',exact=True).click()
                expect(page.locator('#research-datasets')).to_contain_text('Browser imported fixture')
                page.set_viewport_size(dict(width=390,height=844))
                page.locator('#toast').evaluate("e=>e.classList.add('hidden')")
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(artifacts/'research-mobile.png'),full_page=True)
                assert not errors,errors
                browser.close()
                result=dict(status='passed',checks=['research navigation','synthetic provenance','capability preflight','experiment builder','process worker completion','portfolio report','decision/fill inspection','report download','paper review packet','cost sensitivity suite','experiment comparison','CSV file import','mobile layout'],javascript_errors=errors)
                (ROOT/'docs/research-browser-validation.json').write_text(json.dumps(result,indent=2)+'\n')
                print(json.dumps(result,indent=2))
        finally:
            process.terminate();process.join(timeout=15)
            if process.is_alive(): process.kill();process.join()


if __name__=='__main__': main()
