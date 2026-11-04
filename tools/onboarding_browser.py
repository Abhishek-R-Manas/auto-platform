"""Guided setup acceptance using a controlled broker, real OAuth state and real private-file writes."""
import asyncio
from datetime import datetime,timedelta
import json,multiprocessing,os
from pathlib import Path
import struct,sys,tempfile,time,urllib.request
from urllib.parse import parse_qs,urlsplit,urlencode
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
BASE='http://127.0.0.1:8794'

def serve(database):
    import uvicorn
    from app.api import create_app
    from app.engine import IST
    from app.market import KiteData
    import app.live
    for name in ('KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN','ARTHA_AI_API_KEY','ARTHA_AI_MODEL'):os.environ.pop(name,None)
    stocks={'RELIANCE':257,'HDFCBANK':513,'INFY':769}
    def request(self,method,path,**kwargs):
        if path=='/instruments':return 'exchange,tradingsymbol,instrument_type,name,expiry,strike,lot_size,tick_size,instrument_token\n'+'\n'.join(f'NSE,{symbol},EQ,CONTROLLED TEST FIXTURE,,0,1,0.05,{token}' for symbol,token in stocks.items())
        if path=='/session/token':return dict(access_token='controlled_onboarding_session')
        if path=='/quote':return {key:dict(instrument_token=stocks[key.split(':')[1]],last_price=100,timestamp=datetime.now(IST).isoformat(),depth=dict(buy=[dict(price=99.95,quantity=100)],sell=[dict(price=100.05,quantity=100)])) for _,key in kwargs['params']}
        if path.startswith('/instruments/historical/'):return dict(candles=[[(datetime.now(IST)-timedelta(days=1)).isoformat(),100,101,99,100,1000]])
        raise AssertionError('Unexpected broker endpoint in controlled setup')
    KiteData._request=request
    class Socket:
        def __init__(self):self.tokens=[]
        async def send(self,value):
            p=json.loads(value)
            if p['a']=='subscribe':self.tokens=p['v']
        async def recv(self):
            await asyncio.sleep(.2);packets=[]
            for token in self.tokens:
                data=bytearray(184)
                for at,value in ((0,token),(4,10000),(60,int(time.time())),(64,100),(68,9995),(124,100),(128,10005)):struct.pack_into('>I',data,at,value)
                packets.append(struct.pack('>H',184)+data)
            return struct.pack('>H',len(packets))+b''.join(packets)
    class Connect:
        def __init__(self,*a,**kw):self.socket=Socket()
        async def __aenter__(self):return self.socket
        async def __aexit__(self,*args):return False
    app.live.FixedOriginConnect=Connect
    uvicorn.run(create_app(database),host='127.0.0.1',port=8794,access_log=False,log_level='error')

def main():
    from playwright.sync_api import sync_playwright,expect
    checks=[]
    with tempfile.TemporaryDirectory(prefix='artha-onboarding-browser-',dir=ROOT/'data') as directory:
        process=multiprocessing.get_context('spawn').Process(target=serve,args=(str(Path(directory)/'paper.sqlite3'),));process.start()
        try:
            for _ in range(150):
                try:urllib.request.urlopen(BASE+'/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Controlled onboarding server did not start')
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport=dict(width=1440,height=1100));errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(BASE+'/#setup',wait_until='networkidle')
                expect(page.locator('#view-setup')).to_be_visible();expect(page.locator('#view-setup')).to_contain_text('₹500');checks.append('guided paid Connect setup page')
                expect(page.locator('#setup-callback')).to_have_value(BASE+'/api/kite/callback');checks.append('exact callback URL guidance')
                expect(page.locator('#setup-check-quotes')).to_be_disabled();expect(page.locator('#setup-connect')).to_be_disabled();checks.append('honest unconfigured step gates')
                page.locator('#setup-api-key').fill('controlled_key_123');page.locator('#setup-api-secret').fill('controlled_secret_123');page.locator('#setup-save-keys').click()
                expect(page.locator('#setup-keys-status')).to_contain_text('configured',timeout=10000)
                expect(page.locator('#setup-api-key')).to_have_value('');expect(page.locator('#setup-api-secret')).to_have_value('')
                assert os.stat(Path(directory)/'.env.local').st_mode&0o077==0;checks.append('private credential save and cleared fields without restart')
                status=page.request.get(BASE+'/api/onboarding/status').text()
                assert 'controlled_key_123' not in status and 'controlled_secret_123' not in status;checks.append('no credential values in status responses')
                def broker_redirect(route):
                    # Route interception applies to the first request in a redirect chain.
                    # Fetch the genuine local login response without following it, then simulate
                    # the external broker return while forwarding the real CSRF cookie.
                    response=route.fetch(max_redirects=0)
                    destination=response.headers['location'];parsed=urlsplit(destination)
                    assert parsed.hostname=='kite.zerodha.com' and parsed.path=='/connect/login'
                    q=parse_qs(parsed.query);state=parse_qs(q['redirect_params'][0])['state'][0]
                    headers=dict(response.headers);headers['location']=BASE+'/api/kite/callback?'+urlencode(dict(state=state,request_token='controlled_request'))
                    route.fulfill(status=303,headers=headers,body='')
                page.route('**/api/kite/login?return_to=setup',broker_redirect)
                page.locator('#setup-login').click()
                try:page.wait_for_url(BASE+'/#setup',timeout=15000)
                except Exception:
                    parsed=urlsplit(page.url)
                    print(json.dumps(dict(stage='controlled_login_debug',host=parsed.netloc,path=parsed.path,query_names=list(parse_qs(parsed.query)),
                        local_error_flags=[text for text in ('Invalid or expired authentication state','Broker sign-in failed') if text in page.locator('body').inner_text()[:1000]])),flush=True)
                    raise
                expect(page.locator('#setup-login-status')).to_contain_text('Session available',timeout=15000)
                assert 'request_token' not in page.url;checks.append('broker sign-in callback returns safely to setup')
                page.locator('#setup-check-quotes').click();expect(page.locator('#setup-quotes-result')).to_contain_text('Verified:',timeout=10000)
                page.locator('#setup-check-history').click();expect(page.locator('#setup-history-result')).to_contain_text('Verified:',timeout=10000);checks.append('independent quote and candle access checks')
                page.locator('#setup-connect').click();expect(page.locator('#setup-overall')).to_have_text('QUOTES ARRIVING',timeout=15000)
                expect(page.locator('#setup-feed-counts')).to_contain_text('3 fresh');checks.append('observed stream freshness rather than login-only readiness')
                page.locator('#setup-paper-connect').click();expect(page.locator('#setup-paper-status')).to_contain_text('uses fresh Kite market quotes',timeout=15000);checks.append('explicit stock-quote connection to simulated paper desk')
                expect(page.locator('#system-error')).to_be_hidden(timeout=15000)
                state=page.request.get(BASE+'/api/state').json();assert state['mode']=='PAPER' and state['source']=='KITE' and not state['live_trading_available'] and not state['orders'] and state['reconcile']['ok'];checks.append('paper-only account and reconciliation')
                page.screenshot(path=str(ROOT/'docs/screenshots/onboarding-controlled-desktop.png'),full_page=True)
                page.set_viewport_size(dict(width=390,height=844));page.screenshot(path=str(ROOT/'docs/screenshots/onboarding-controlled-mobile.png'),full_page=True)
                assert page.locator('body').bounding_box()['width']<=390;checks.append('mobile onboarding layout')
                page.reload(wait_until='networkidle');expect(page.locator('#setup-login-status')).to_contain_text('Session available',timeout=10000);checks.append('progress survives browser refresh')
                assert not errors,errors;browser.close()
            result=dict(controlled_broker=True,real_provider_login_validated=False,passed=len(checks),checks=checks,javascript_errors=errors)
            (ROOT/'docs/onboarding-browser-validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
        finally:
            process.terminate();process.join(15)
            if process.is_alive():process.kill();process.join()

if __name__=='__main__':main()
