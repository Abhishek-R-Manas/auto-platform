"""Full manual trading journey with an isolated fake broker. No real provider or order calls."""
import json,multiprocessing,sys,tempfile,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.workbench_browser import serve

def main():
    from playwright.sync_api import sync_playwright,expect
    checks=[];errors=[];base='http://127.0.0.1:8796';headers={'Content-Type':'application/json','X-Artha-Client':'paper-workspace'}
    with tempfile.TemporaryDirectory(prefix='artha-manual-ui-',dir=ROOT/'data') as directory:
        process=multiprocessing.get_context('spawn').Process(target=serve,args=(str(Path(directory)/'paper.sqlite3'),True));process.start()
        try:
            for _ in range(100):
                try:urllib.request.urlopen(base+'/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Controlled manual server did not start')
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1600,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base,wait_until='networkidle');before=page.request.get(base+'/api/state').json();page.locator('[data-view="pilot"]').first.click();expect(page.locator('#pilot-save-limits')).to_be_disabled();expect(page.locator('#pilot-gates')).to_contain_text('Sign in with Zerodha');checks.append('guided setup shows missing authentication and blocks saving')
                page.request.post(base+'/fixture/login',data='{}',headers=headers);page.reload(wait_until='networkidle');expect(page.locator('#pilot-reconcile')).to_be_enabled();page.locator('#pilot-reconcile').click();expect(page.locator('#pilot-account')).to_have_value('AB1234');expect(page.locator('#pilot-forward-field')).to_be_hidden();checks.append('explicit sync binds the actual fixture account without a forward strategy');page.locator('#live-next-account').click();expect(page.locator('[data-onboarding-panel="2"]')).to_be_visible();page.locator('#pilot-budget-mode').select_option('CUSTOM');page.locator('#pilot-order-count').fill('3')
                for key,value in [('symbols','RELIANCE'),('capital','2000'),('loss','100'),('trade-loss','50'),('order-cap','1000'),('expiry','2026-11-06T10:00'),('reviewer','Controlled owner')]:page.locator('#pilot-'+key).fill(value)
                page.locator('#live-next-limits').click();expect(page.locator('[data-onboarding-panel="3"]')).to_be_visible();expect(page.locator('[data-onboarding-panel="3"]')).to_contain_text('read APIs do not expose the whitelist')
                for key in ['native-stops','static-ip','delivery']:page.locator('#pilot-'+key).check()
                page.locator('#live-next-permissions').click();expect(page.locator('#live-setup-review')).to_contain_text('Setup validation passed');page.locator('#pilot-manual-confirm').fill('ENABLE MANUAL NSE DELIVERY TRADING')
                page.locator('#pilot-save-limits').click();expect(page.locator('#pilot-setup-message')).to_contain_text('Trading permissions saved');expect(page.locator('#pilot-limits-summary')).to_contain_text('2,000');checks.append('explicit limits saved with independent manual authority')
                page.locator('#pilot-arm-text').fill('ARM REAL MONEY AB1234');page.locator('#pilot-arm button').click();expect(page.locator('#pilot-mode')).to_have_text('ACTIVE · REAL MONEY');checks.append('fresh reconciliation and exact confirmation activate the simulated session')
                page.evaluate("const b=document.createElement('div');b.textContent='CONTROLLED BROKER SIMULATION · NO REAL MONEY';b.style='position:fixed;bottom:0;right:0;background:#253858;color:white;padding:5px 10px;font-size:10px;z-index:9999';document.body.append(b)")
                page.screenshot(path=str(ROOT/'docs/screenshots/manual-trading-setup-controlled.png'),full_page=True)
                page.locator('[data-view="trade"]').first.click();page.locator('#ws-account').select_option('LIVE');page.locator('#desk-buy').click();expect(page.locator('#live-ticket-panel')).to_be_visible()
                page.locator('#pilot-price').fill('100');page.locator('#pilot-quantity').fill('5');page.locator('#pilot-stop').fill('99');page.locator('#pilot-rationale').fill('Controlled manual entry');page.locator('#pilot-preview-button').click();expect(page.locator('#pilot-preview')).to_contain_text('Review real order');expect(page.locator('#pilot-preview')).to_contain_text('₹500.00');expect(page.locator('#pilot-preview')).to_contain_text('₹21.46');checks.append('human-readable review shows exact order value, charges and planned risk')
                page.screenshot(path=str(ROOT/'docs/screenshots/manual-trading-ticket-controlled.png'),full_page=True)
                page.locator('#pilot-confirm-text').fill('I AUTHORIZE THIS REAL MONEY ORDER');page.locator('#pilot-confirm button').click();expect(page.locator('#pilot-preview')).to_contain_text('ACKNOWLEDGED');page.locator('#live-show-orders').click();expect(page.locator('#broker-orders')).to_contain_text('ACKNOWLEDGED');checks.append('confirmed order is recorded once and acknowledgement is shown separately from fills')
                page.locator('[data-order-account="PAPER"]').click();page.evaluate("async()=>{const s=await (await fetch('/api/pilot/status')).json();window.dispatchEvent(new CustomEvent('artha:pilot-status',{detail:s}));}");expect(page.locator('#paper-orders-content')).to_be_visible();page.locator('[data-order-account="LIVE"]').click();checks.append('broker status refresh preserves the selected paper-orders tab')
                page.request.post(base+'/fixture/fill-manual',data='{}',headers=headers);page.locator('[data-live-sync]').click();expect(page.locator('#broker-orders')).to_contain_text('COMPLETE');expect(page.locator('#broker-orders')).to_contain_text('Protective exit');checks.append('broker-confirmed fills create reserved native protection')
                page.on('dialog',lambda d:d.accept());page.locator('#broker-orders [data-protective="true"]').click();expect(page.locator('#broker-orders')).to_contain_text('CANCEL_PENDING');checks.append('explicit stop cancellation pauses entries and remains pending until broker confirmation')
                page.request.post(base+'/fixture/resolve-cancellation',data='{}',headers=headers);page.locator('[data-live-sync]').click();expect(page.locator('#broker-orders')).to_contain_text('CANCELLED')
                page.locator('[data-view="trade"]').first.click();page.locator('#desk-sell').click();page.locator('#pilot-price').fill('100');page.locator('#pilot-quantity').fill('5');page.locator('#pilot-rationale').fill('Controlled reduction');expect(page.locator('#pilot-preview-button')).to_be_enabled();page.locator('#pilot-preview-button').click();expect(page.locator('#pilot-preview')).to_contain_text('Reduction only');checks.append('paused entries still allow a verified reduction after stop cancellation')
                page.locator('#pilot-price').fill('100.05');expect(page.locator('#pilot-confirm')).to_be_hidden();checks.append('changing any order field invalidates the previous review')
                page.locator('#close-ticket').click();page.locator('[data-view="pilot"]').first.click();page.set_viewport_size({'width':390,'height':844});expect(page.locator('#more-tools')).not_to_have_attribute('open','');assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2');page.screenshot(path=str(ROOT/'docs/screenshots/manual-trading-mobile-controlled.png'),full_page=True);checks.append('mobile trading setup fits the viewport')
                after=page.request.get(base+'/api/state').json();pilot=page.request.get(base+'/api/pilot/status').json();assert before['account']==after['account'] and before['orders']==after['orders'] and pilot['audit']['ok'];checks.append('simulated broker actions preserve the paper account and order audit')
                assert not errors,errors;browser.close()
        finally:process.terminate();process.join(15)
    record=dict(passed=len(checks),checks=checks,javascript_errors=errors,controlled_broker=True,real_broker_reads=False,real_orders_sent=False,private_portfolio_artifacts_created=False)
    (ROOT/'docs/manual-trading-browser-validation.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record))
if __name__=='__main__':main()
