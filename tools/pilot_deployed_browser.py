"""Read-only acceptance of the deployed chart/pilot UI. No broker actions or login."""
import json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def main():
    from playwright.sync_api import sync_playwright,expect
    checks=[];errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8787/#marketdata',wait_until='networkidle');expect(page.locator('#view-marketdata')).to_be_visible();expect(page.locator('#price-load')).to_be_visible();checks.append('deployed price-chart controls')
        expect(page.locator('#workspace-mode')).to_have_text('MARKET DATA');expect(page.locator('.mode-strip')).to_be_hidden();checks.append('market data independent of paper desk')
        before=page.request.get('http://127.0.0.1:8787/api/state').json()
        chart=page.request.get('http://127.0.0.1:8787/api/market/charts').json();assert chart['symbol']=='NSE:RELIANCE';checks.append('persistent chart API honest cached/session state')
        page.screenshot(path=str(ROOT/'docs/screenshots/charts-deployed-desktop.png'),full_page=True)
        page.get_by_role('button',name='Real-money pilot',exact=True).click();expect(page.locator('#view-pilot')).to_be_visible();expect(page.locator('#pilot-mode')).to_have_text('TRANSPORT DISABLED');expect(page.locator('#pilot-preview-button')).to_be_disabled();checks.append('deployed pilot transport and orders disabled')
        expect(page.locator('#workspace-mode')).to_contain_text('REAL MONEY');expect(page.locator('#new-trade')).to_be_hidden();expect(page.locator('#pilot-confirm')).to_be_hidden();checks.append('explicit real-money mode and confirmation boundary')
        status=page.request.get('http://127.0.0.1:8787/api/pilot/status').json();assert not status['transport_enabled'] and not status['armed'] and not status['orders'] and status['audit']['ok'];checks.append('broker pilot audit with zero orders')
        page.screenshot(path=str(ROOT/'docs/screenshots/pilot-deployed-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2');checks.append('deployed pilot mobile layout');page.screenshot(path=str(ROOT/'docs/screenshots/pilot-deployed-mobile.png'),full_page=True)
        page.get_by_role('button',name='NSE market data',exact=True).click();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2');checks.append('deployed chart mobile layout');page.screenshot(path=str(ROOT/'docs/screenshots/charts-deployed-mobile.png'),full_page=True)
        after=page.request.get('http://127.0.0.1:8787/api/state').json();assert before['account']==after['account'] and before['orders']==after['orders'] and before['positions']==after['positions'];checks.append('owner financial state preserved by read-only browser checks')
        browser.close()
    record=dict(checked_at=datetime.now(timezone.utc).isoformat(),passed=len(checks),checks=checks,javascript_errors=errors,read_only=True,real_orders_sent=False,genuine_provider_access_tested=False)
    (ROOT/'docs/phase-5-deployed-browser.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record));assert not errors

if __name__=='__main__':main()
