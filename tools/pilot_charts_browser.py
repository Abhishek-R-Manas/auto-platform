"""Isolated browser acceptance; all prices and broker side effects are controlled fixtures."""
import json,multiprocessing,sys,tempfile,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def serve(database):
    import uvicorn
    sys.path.insert(0,str(ROOT/'tests'))
    from test_pilot_charts import Kite,Forward,Broker,AT,mandate
    from app.api import create_app
    from app.charts import ChartRequest
    app=create_app(database,background=False)
    k=Kite();f=Forward();b=Broker();s=app.state.pilot
    s.kite=k;s.forward=f;s.broker=b;s.clock=lambda:AT;s.configure(mandate())
    app.state.charts.kite=k;app.state.charts.clock=lambda:AT
    app.state.charts.refresh(ChartRequest(symbol='NSE:TEST'))
    @app.post('/fixture/logout')
    def logout():k.token='';return {'ok':True}
    uvicorn.run(app,host='127.0.0.1',port=8794,access_log=False,log_level='error')

def main():
    from playwright.sync_api import sync_playwright,expect
    checks=[];errors=[]
    with tempfile.TemporaryDirectory(prefix='artha-pilot-browser-',dir=ROOT/'data') as directory:
        process=multiprocessing.get_context('spawn').Process(target=serve,args=(str(Path(directory)/'paper.sqlite3'),));process.start()
        try:
            for _ in range(100):
                try:urllib.request.urlopen('http://127.0.0.1:8794/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Controlled browser server did not start')
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto('http://127.0.0.1:8794/#marketdata',wait_until='networkidle');page.locator('#price-symbol').fill('NSE:TEST');page.locator('#price-load').click();expect(page.locator('#price-close')).to_contain_text('105');expect(page.locator('#price-chart svg')).to_be_visible();checks.append('saved completed-candle chart and close')
                page.locator('#price-refresh').click();expect(page.locator('#price-source')).to_have_text('SAVED KITE HISTORY');checks.append('controlled broker refresh')
                page.screenshot(path=str(ROOT/'docs/screenshots/charts-controlled-desktop.png'),full_page=True)
                page.get_by_role('button',name='Real-money pilot',exact=True).click();expect(page.locator('#pilot-mode')).to_have_text('PAUSED');expect(page.locator('#workspace-mode')).to_contain_text('REAL MONEY');expect(page.locator('#new-trade')).to_be_hidden();checks.append('explicit execution-mode separation')
                page.locator('#pilot-reconcile').click();expect(page.locator('#pilot-status')).to_contain_text('Reconciled');page.locator('#pilot-arm-text').fill('ARM REAL MONEY AB1234');page.locator('#pilot-arm button').click();expect(page.locator('#pilot-mode')).to_contain_text('ARMED');checks.append('reconcile and exact account arming')
                for key,value in {'symbol':'TEST','quantity':'5','price':'100','stop':'99','rationale':'Controlled browser acceptance'}.items():page.locator('#pilot-'+key).fill(value)
                page.locator('#pilot-preview-button').click();expect(page.locator('#pilot-confirm')).to_be_visible();page.locator('#pilot-confirm-text').fill('I AUTHORIZE THIS REAL MONEY ORDER');page.locator('#pilot-confirm button').click();expect(page.locator('#pilot-orders')).to_contain_text('ACKNOWLEDGED');checks.append('preview then explicit one-time controlled order')
                state=page.request.get('http://127.0.0.1:8794/api/state').json();assert state['mode']=='PAPER' and state['reconcile']['ok'] and not state['positions'];checks.append('paper account isolation')
                page.locator('#pilot-reconcile').click();expect(page.locator('#pilot-orders')).to_contain_text('OPEN');page.locator('[data-pilot-cancel]').click();expect(page.locator('#pilot-orders')).to_contain_text('CANCEL_PENDING');checks.append('broker reconciliation and cancellation acknowledgement')
                page.locator('#pilot-pause').click();expect(page.locator('#pilot-mode')).to_have_text('PAUSED');checks.append('explicit pause')
                page.screenshot(path=str(ROOT/'docs/screenshots/pilot-controlled-desktop.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+2');checks.append('pilot mobile layout');page.screenshot(path=str(ROOT/'docs/screenshots/pilot-controlled-mobile.png'),full_page=True)
                page.request.post('http://127.0.0.1:8794/fixture/logout',data='{}',headers={'Content-Type':'application/json','X-Artha-Client':'paper-workspace'})
                page.get_by_role('button',name='NSE market data',exact=True).click();page.locator('#price-load').click();expect(page.locator('#price-status')).to_contain_text('session signed out');expect(page.locator('#price-refresh')).to_be_disabled();expect(page.locator('#price-close')).to_contain_text('105');checks.append('saved chart survives logout with honest source label')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+2');checks.append('chart mobile layout');page.screenshot(path=str(ROOT/'docs/screenshots/charts-controlled-mobile.png'),full_page=True)
                browser.close()
        finally:process.terminate();process.join(15)
    result=dict(passed=len(checks),checks=checks,javascript_errors=errors,controlled_broker=True,real_orders_sent=False)
    (ROOT/'docs/pilot-charts-browser-validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));assert not errors

if __name__=='__main__':main()
