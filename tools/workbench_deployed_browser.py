"""Read-only deployed workspace acceptance. No login, broker reads or orders."""
import json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def main():
    from playwright.sync_api import sync_playwright,expect
    checks=[];errors=[];base='http://127.0.0.1:8787'
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1600,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base,wait_until='networkidle');before=page.request.get(base+'/api/state').json()
        expect(page.locator('#view-home')).to_be_visible();expect(page.locator('#home-next-step')).to_be_visible();checks.append('guided home and next step')
        for name,view in [('Trade','trade'),('Strategy Builder','builder'),('My Strategies','saved'),('Connections','setup'),('Home','home')]:
            page.get_by_role('button',name=name,exact=True).click();expect(page.locator('#view-'+view)).to_be_visible();checks.append('primary navigation '+name)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            if view=='trade':expect(page.locator('#watchlist-select')).to_be_visible();expect(page.locator('#watchlist-menu')).to_be_visible();expect(page.locator('#chart-live-status')).to_be_visible();checks.append('deployed named watchlist controls and chart freshness label')
            if view=='builder':expect(page.locator('#builder-payoff')).to_contain_text('See the shape of your strategy')
            page.screenshot(path=str(ROOT/'docs/screenshots'/('workspace-'+view+'-deployed.png')),full_page=True)
        page.get_by_role('button',name='Strategy Builder',exact=True).click();expect(page.locator('#template-count')).to_contain_text('51');checks.append('full 51-template catalog visible')
        page.set_viewport_size({'width':390,'height':844})
        for name,view in [('Home','home'),('Trade','trade'),('Strategy Builder','builder'),('Connections','setup')]:
            page.get_by_role('button',name=name,exact=True).click();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2');checks.append('mobile '+name)
            if view=='builder':
                expect(page.locator('#builder-payoff')).to_contain_text('See the shape of your strategy');bounds=page.locator('#builder-payoff .desk-empty').bounding_box();container=page.locator('#builder-payoff').bounding_box();assert bounds['height']<=container['height']+2
            page.screenshot(path=str(ROOT/'docs/screenshots'/('workspace-'+view+'-deployed-mobile.png')),full_page=True)
        pilot=page.request.get(base+'/api/pilot/status').json();health=page.request.get(base+'/api/health').json();after=page.request.get(base+'/api/state').json()
        assert not pilot['transport_enabled'] and not pilot['armed'] and not pilot['orders'] and pilot['audit']['ok'];checks.append('live transport disabled and zero real orders')
        assert health['status']=='ok' and health['reconciliation']['ok'];checks.append('healthy financial reconciliation')
        assert before['account']==after['account'] and before['positions']==after['positions'] and before['orders']==after['orders'];checks.append('read-only journey preserves financial state')
        browser.close()
    record=dict(checked_at=datetime.now(timezone.utc).isoformat(),passed=len(checks),checks=checks,javascript_errors=errors,read_only=True,real_orders_sent=False,genuine_provider_access_tested=False)
    (ROOT/'docs/workspace-deployed-browser.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record));assert not errors
if __name__=='__main__':main()
