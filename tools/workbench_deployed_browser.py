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
        toggle=page.locator('#sidebar-toggle');summary=page.locator('#more-tools>summary');expect(toggle).to_have_attribute('aria-expanded','false');assert page.locator('#workspace-sidebar').bounding_box()['width']==64
        page.locator('#workspace-sidebar button[data-view="trade"]').hover();expect(toggle).to_have_attribute('aria-expanded','true');summary.hover();expect(page.locator('#more-tools-menu')).to_be_visible();page.locator('#more-tools-menu [data-view="research"]').hover();expect(summary).to_have_attribute('aria-expanded','true');page.mouse.move(600,80);expect(toggle).to_have_attribute('aria-expanded','false');expect(summary).to_have_attribute('aria-expanded','false');checks.append('hovering anywhere on the sidebar rail expands navigation and More tools without changing workspace width')
        for name,view in [('Trade','trade'),('Strategy Builder','builder'),('My Strategies','saved'),('Strategy Contracts','contracts'),('Portfolio','positions'),('Connections','setup'),('Trading setup','pilot'),('Home','home')]:
            (page.locator('[data-view="positions"]').first if view=='positions' else page.get_by_role('button',name=name,exact=True)).click();expect(page.locator('#view-'+view)).to_be_visible();expect(toggle).to_have_attribute('aria-expanded','false');assert page.locator('#workspace-sidebar').bounding_box()['width']==64;checks.append('primary navigation '+name)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            if view=='trade':expect(page.locator('#watchlist-select')).to_be_visible();expect(page.locator('#watchlist-menu')).to_be_visible();expect(page.locator('#chart-live-status')).to_be_visible();checks.append('deployed named watchlist controls and chart freshness label')
            if view=='builder':
                expect(page.locator('#builder-payoff')).to_contain_text('See the shape of your strategy');expect(page.locator('#studio-import-positions')).to_be_visible();expect(page.locator('#studio-leg-values')).to_be_visible();expect(page.locator('#studio-panel-margin')).to_be_visible();expect(page.locator('#builder-overview')).to_be_visible();checks.append('single-view strategy summary and margin controls available without broker calls')
            if view=='contracts':
                expect(page.locator('#ct-form')).to_be_visible();page.locator('#ct-tab-research').click();expect(page.locator('#cr-freeze')).to_be_visible();expect(page.locator('#cr-baseline')).to_be_disabled();expect(page.locator('#cr-verify')).to_be_visible();page.locator('#ct-tab-definition').click();expect(page.locator('#ct-form')).to_be_visible();checks.append('contract research protocol and packet verification UI loads without creating data or trials')
            if view=='positions':
                assert page.locator('#portfolio-summary').is_visible();saved=page.request.get(base+'/api/pilot/status').json().get('portfolio_view');expected=len(saved['holdings']) if saved else 0
                assert page.locator('#portfolio-table tbody tr').count()==expected;expect(page.locator('#position-count')).to_have_text(str(expected));checks.append('cached broker holdings rendered without a new broker read or private screenshot')
            elif view=='pilot':expect(page.locator('#live-onboarding-steps button')).to_have_count(4);expect(page.locator('[data-onboarding-panel="1"]')).to_be_visible();checks.append('four-stage onboarding opens at account verification without broker reads');page.locator('[data-onboarding-step="2"]').first.click();expect(page.locator('#pilot-universe option')).to_have_count(2);expect(page.locator('#pilot-universe')).to_contain_text('All supported NSE stocks and ETFs');expect(page.locator('#pilot-budget-mode')).to_contain_text('no app-defined caps');page.locator('[data-onboarding-step="1"]').first.click();checks.append('all-NSE-stock permission option is available without changing saved authority')
        page.get_by_role('button',name='Strategy Builder',exact=True).click();expect(page.locator('#builder-strategy-library')).to_be_visible();expect(page.locator('#strategy-templates [data-template]')).to_have_count(51);expect(page.locator('#builder-strategy-library select')).to_have_count(0);page.locator('[data-template-category="Bullish"]').click();expect(page.locator('#strategy-templates [data-template]')).to_have_count(10);page.locator('[data-template-category="All"]').click();checks.append('visible 51-template strategy library filters with market-view buttons without dropdowns or broker calls')
        page.set_viewport_size({'width':390,'height':844})
        for name,view in [('Home','home'),('Trade','trade'),('Strategy Builder','builder'),('Portfolio','positions'),('Connections','setup'),('Trading setup','pilot')]:
            (page.locator('[data-view="positions"]').first if view=='positions' else page.get_by_role('button',name=name,exact=True)).click();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2');checks.append('mobile '+name)
            if view=='builder':
                expect(page.locator('#builder-payoff')).to_contain_text('See the shape of your strategy');bounds=page.locator('#builder-payoff .desk-empty').bounding_box();container=page.locator('#builder-payoff').bounding_box();assert bounds['height']<=container['height']+2
            if view=='positions':assert page.locator('#portfolio-summary').is_visible()
            elif view=='pilot':expect(page.locator('#live-onboarding-steps')).to_be_visible()
        page.get_by_role('link',name='Team workspace',exact=True).scroll_into_view_if_needed()
        assert page.get_by_role('link',name='Team workspace',exact=True).get_attribute('href')=='/team'
        team_page=browser.new_page();team_page.on('pageerror',lambda e:errors.append(str(e)));team_page.goto(base+'/team',wait_until='networkidle')
        team_session=team_page.request.get(base+'/api/team/session').json()
        expect(team_page.locator('#auth-submit')).to_be_visible()
        assert team_page.request.get(base+'/api/team/dashboard').status==401
        assert team_page.locator('#auth-password').input_value()==''
        checks.append('team workspace opens at authenticated setup/sign-in; private team APIs reject anonymous access')
        team_page.close()
        pilot=page.request.get(base+'/api/pilot/status').json();health=page.request.get(base+'/api/health').json();after=page.request.get(base+'/api/state').json()
        assert pilot['transport_enabled']==('--expect-live-transport' in sys.argv) and not pilot['armed'] and not pilot['orders'] and pilot['audit']['ok'];checks.append('expected transport capability, inactive account and zero real orders')
        assert health['status']=='ok' and health['reconciliation']['ok'];checks.append('healthy financial reconciliation')
        assert before['account']==after['account'] and before['positions']==after['positions'] and before['orders']==after['orders'];checks.append('read-only journey preserves financial state')
        browser.close()
    record=dict(checked_at=datetime.now(timezone.utc).isoformat(),passed=len(checks),checks=checks,javascript_errors=errors,read_only=True,private_portfolio_artifacts_created=False,real_orders_sent=False,genuine_provider_access_tested=False,transport_enabled=pilot['transport_enabled'],account_armed=pilot['armed'],screenshots_created=False)
    (ROOT/'docs/workspace-deployed-browser.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record));assert not errors
if __name__=='__main__':main()
