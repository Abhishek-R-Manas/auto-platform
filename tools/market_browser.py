"""Browser acceptance with a controlled broker protocol fixture, never actual market data."""
import asyncio
from datetime import datetime
import json
import multiprocessing
from pathlib import Path
import os
import struct
import sys
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def serve(database):
    import uvicorn
    from app.api import create_app
    from app.engine import IST
    from app.market import KiteData
    import app.live
    def request(self,method,path,**kwargs):
        if path=='/instruments':
            header='exchange,tradingsymbol,instrument_type,name,expiry,strike,lot_size,tick_size,instrument_token'
            return header+'\n'+'\n'.join(f'NSE,STOCK{i:02},EQ,CONTROLLED TEST FIXTURE {i},,0,1,0.05,{(i+1)*256+1}' for i in range(62))
        if path=='/quote':
            return {key:dict(instrument_token=257,last_price=100,timestamp=datetime.now(IST).isoformat(),
                depth=dict(buy=[dict(price=99.95,quantity=100)],sell=[dict(price=100.05,quantity=100)])) for _,key in kwargs['params']}
        raise AssertionError('Unexpected network route in controlled test')
    KiteData._request=request
    class Socket:
        def __init__(self): self.tokens=[]
        async def send(self,text):
            value=json.loads(text)
            if value['a']=='subscribe': self.tokens=value['v']
        async def recv(self):
            await asyncio.sleep(.2);packets=[]
            for token in self.tokens:
                packet=bytearray(184)
                for at,value in ((0,token),(4,10000),(60,int(time.time())),(64,100),(68,9995),(124,100),(128,10005)):
                    struct.pack_into('>I',packet,at,value)
                packets.append(struct.pack('>H',len(packet))+packet)
            return struct.pack('>H',len(packets))+b''.join(packets)
    class Connection:
        def __init__(self,*a,**kw): self.socket=Socket()
        async def __aenter__(self): return self.socket
        async def __aexit__(self,*a): return False
    app.live.FixedOriginConnect=Connection
    for key in ('KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN'): os.environ.pop(key,None)
    app=create_app(database)
    app.state.kite.key='controlled-test-key';app.state.kite.token='controlled-test-session'
    uvicorn.run(app,host='127.0.0.1',port=8792,access_log=False,log_level='error')


def main():
    from playwright.sync_api import sync_playwright,expect
    import re
    checks=[]
    with tempfile.TemporaryDirectory(prefix='artha-market-browser-',dir=ROOT/'data') as directory:
        process=multiprocessing.get_context('spawn').Process(target=serve,args=(str(Path(directory)/'paper.sqlite3'),));process.start()
        try:
            for _ in range(100):
                try: urllib.request.urlopen('http://127.0.0.1:8792/api/health',timeout=1).close();break
                except OSError: time.sleep(.1)
            else: raise RuntimeError('Browser fixture server did not start')
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport=dict(width=1440,height=1100));errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto('http://127.0.0.1:8792/#marketdata',wait_until='networkidle')
                expect(page.locator('#view-marketdata')).to_be_visible();checks.append('market-data navigation')
                page.locator('#breadth-connect').click()
                expect(page.locator('#breadth-health')).to_contain_text('62 NSE instruments',timeout=15000)
                expect(page.locator('#breadth-health')).to_contain_text('62 fresh',timeout=15000);checks.append('full controlled universe coverage and freshness')
                expect(page.locator('#breadth-page')).to_contain_text('1–50 of 62')
                page.locator('#breadth-next').click();expect(page.locator('#breadth-page')).to_contain_text('51–62 of 62');checks.append('universe pagination')
                page.locator('#breadth-search').fill('STOCK61');expect(page.locator('#breadth-page')).to_contain_text('1–1 of 1');checks.append('full-universe symbol search')
                page.locator('#breadth-quotes [data-add-watch]').click()
                expect(page.locator('#view-settings')).to_be_visible();expect(page.locator('#kite-instruments')).to_have_value('NSE:STOCK61');checks.append('stock selection into paper watchlist')
                page.locator('#contract-search').fill('STOCK60');expect(page.locator('#contract-results')).to_contain_text('NSE:STOCK60');checks.append('master contract search')
                page.locator('#use-kite').click();expect(page.locator('#source-label')).to_contain_text('Zerodha',timeout=15000)
                page.get_by_role('button',name='NSE market data',exact=True).click()
                expect(page.locator('#capture-start')).to_be_enabled(timeout=15000)
                page.locator('#capture-name').fill('Controlled browser protocol capture');page.locator('#capture-start').click()
                expect(page.locator('#capture-list')).to_contain_text(re.compile(r'[1-9][0-9]* quotes'),timeout=15000);checks.append('stream delivery and durable capture')
                page.get_by_role('button',name='Stop recording',exact=True).click()
                expect(page.locator('#capture-list')).to_contain_text('STOPPED',timeout=10000)
                page.get_by_role('button',name='Create research dataset',exact=True).click()
                expect(page.locator('#view-research')).to_be_visible(timeout=15000)
                expect(page.locator('#research-datasets')).to_contain_text('Controlled browser protocol capture',timeout=15000);checks.append('capture to immutable research dataset')
                state=page.request.get('http://127.0.0.1:8792/api/state').json()
                assert state['mode']=='PAPER' and state['reconcile']['ok'] and not state['live_trading_available'] and not state['positions'];checks.append('paper-only account and reconciliation')
                page.get_by_role('button',name='NSE market data',exact=True).click()
                page.locator('#breadth-search').fill('');expect(page.locator('#breadth-page')).to_contain_text('1–50 of 62')
                page.screenshot(path=str(ROOT/'docs/screenshots/market-data-controlled-desktop.png'),full_page=True)
                page.set_viewport_size(dict(width=390,height=844));page.screenshot(path=str(ROOT/'docs/screenshots/market-data-controlled-mobile.png'),full_page=True)
                assert page.locator('body').bounding_box()['width']<=390;checks.append('mobile layout')
                assert not errors,errors
                browser.close()
            result=dict(controlled_protocol_fixture=True,actual_provider_connection_validated=False,checks=checks,passed=len(checks),javascript_errors=errors)
            (ROOT/'docs/market-browser-validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
        finally:
            process.terminate();process.join(15)
            if process.is_alive(): process.kill();process.join()

if __name__=='__main__': main()
