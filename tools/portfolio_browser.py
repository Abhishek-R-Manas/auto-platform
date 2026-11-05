"""Portfolio design acceptance with a fictional broker snapshot. No private portfolio artifacts."""
import json,multiprocessing,sys,tempfile,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.workbench_browser import serve

def main():
    from playwright.sync_api import sync_playwright,expect
    checks=[];errors=[];base='http://127.0.0.1:8796';headers={'Content-Type':'application/json','X-Artha-Client':'paper-workspace'}
    with tempfile.TemporaryDirectory(prefix='artha-portfolio-ui-',dir=ROOT/'data') as directory:
        process=multiprocessing.get_context('spawn').Process(target=serve,args=(str(Path(directory)/'paper.sqlite3'),));process.start()
        try:
            for _ in range(100):
                try:urllib.request.urlopen(base+'/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Controlled portfolio server did not start')
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1600,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base,wait_until='networkidle');before=page.request.get(base+'/api/state').json();page.locator('[data-view="positions"]').first.click();expect(page.locator('#broker-portfolio')).to_be_visible();expect(page.locator('.page-heading')).to_be_hidden();expect(page.locator('.mode-strip')).to_be_hidden();expect(page.locator('#portfolio-empty')).to_contain_text('Connect your Zerodha portfolio');expect(page.locator('#portfolio-sync')).to_be_disabled();checks.append('portfolio opens at broker holdings with an honest disconnected state')
                page.request.post(base+'/fixture/login',data='{}',headers=headers);response=page.request.post(base+'/fixture/portfolio',data='{}',headers=headers);assert response.ok
                page.reload(wait_until='networkidle');expect(page.locator('#portfolio-table tbody tr')).to_have_count(3,timeout=10000);expect(page.locator('#portfolio-summary')).to_contain_text('52,000.00');expect(page.locator('#portfolio-summary')).to_contain_text('52,200.00');expect(page.locator('#portfolio-summary')).to_contain_text('200.00');checks.append('holdings summary and table use hand-checked snapshot totals')
                page.evaluate("const b=document.createElement('div');b.textContent='CONTROLLED PORTFOLIO FIXTURE · NOT YOUR HOLDINGS';b.style='position:fixed;bottom:0;right:0;background:#253858;color:white;padding:5px 10px;font-size:10px;z-index:9999';document.body.append(b)")
                page.screenshot(path=str(ROOT/'docs/screenshots/portfolio-holdings-controlled.png'),full_page=True)
                page.locator('#portfolio-search').fill('TCS');expect(page.locator('#portfolio-table tbody tr')).to_have_count(1);expect(page.locator('#portfolio-table')).to_contain_text('T+1');expect(page.locator('#portfolio-table')).to_contain_text('MTF');checks.append('search preserves T+1 and MTF visibility')
                page.locator('#portfolio-table [data-portfolio-detail]').first.click();expect(page.locator('#portfolio-detail')).to_be_visible();expect(page.locator('#portfolio-detail-body')).to_contain_text('Settled');expect(page.locator('#portfolio-detail-body')).to_contain_text('3,125.00');page.locator('#portfolio-detail-close').click();checks.append('holding breakdown explains quantity and weighted cost')
                page.locator('#portfolio-search').fill('');page.locator('[data-portfolio-sort="pnl_paise"]').click();expect(page.locator('#portfolio-table tbody tr').first).to_contain_text('RELIANCE');checks.append('numeric sorting works from column headings')
                page.locator('#portfolio-product').select_option('MTF');expect(page.locator('#portfolio-table tbody tr')).to_have_count(1);checks.append('product filter includes the MTF component')
                with page.expect_download() as event:page.locator('#portfolio-download').click()
                download=event.value;file=Path(directory)/'fixture.csv';download.save_as(file);content=file.read_text();assert 'TCS' in content and '12500.00' in content and 'RELIANCE' not in content;checks.append('CSV export follows visible rows and decimal amounts')
                page.locator('[data-broker-tab="positions"]').click();expect(page.locator('#portfolio-table tbody tr')).to_have_count(2);expect(page.locator('#portfolio-summary')).to_contain_text('3,275.00');expect(page.locator('#portfolio-summary')).to_contain_text('-25.00');checks.append('positions separate signed exposure from broker P&L')
                page.locator('#portfolio-position-scope').select_option('ALL');expect(page.locator('#portfolio-table tbody tr')).to_have_count(3);checks.append('closed positions remain available without inflating open count');page.screenshot(path=str(ROOT/'docs/screenshots/portfolio-positions-controlled.png'),full_page=True)
                page.locator('.portfolio-navigation [data-portfolio-account="PAPER"]').click();expect(page.locator('#paper-portfolio-content')).to_be_visible();expect(page.locator('#broker-portfolio')).to_be_hidden();expect(page.locator('#ws-account')).to_have_value('PAPER');checks.append('paper view stays separate and viewing broker data preserves the execution account')
                page.locator('[data-broker-tab="holdings"]').click();page.locator('#portfolio-search').fill('not-a-stock');expect(page.locator('#portfolio-empty')).to_contain_text('No matching holdings');page.locator('#portfolio-clear-search').click();expect(page.locator('#portfolio-table tbody tr')).to_have_count(3);checks.append('filtered empty state offers a working reset')
                page.set_viewport_size({'width':390,'height':844});expect(page.locator('#portfolio-mobile')).to_be_visible();expect(page.locator('#portfolio-mobile .portfolio-holding-card')).to_have_count(3);assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2');page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(ROOT/'docs/screenshots/portfolio-holdings-mobile-controlled.png'),full_page=True);checks.append('mobile uses compact holding cards without horizontal overflow')
                page.locator('#portfolio-mobile [data-portfolio-detail]').first.click();expect(page.locator('#portfolio-detail')).to_be_visible();page.locator('#portfolio-detail-close').click();checks.append('mobile holding breakdown is keyboard-accessible')
                page.set_viewport_size({'width':1600,'height':1100});page.locator('#portfolio-sync').click();expect(page.locator('#portfolio-sync')).to_have_text('↻  Sync Zerodha',timeout=15000);expect(page.locator('#portfolio-table tbody tr')).to_have_count(3);checks.append('explicit Sync Zerodha refreshes the same read-only snapshot')
                page.locator('[data-view="home"]').first.click();page.get_by_role('button',name='View paper portfolio →',exact=True).click();expect(page.locator('#paper-portfolio-content')).to_be_visible();checks.append('paper-account shortcut opens the paper portfolio directly')
                after=page.request.get(base+'/api/state').json();pilot=page.request.get(base+'/api/pilot/status').json();assert before['account']==after['account'] and before['orders']==after['orders'] and not pilot['transport_enabled'] and not pilot['orders'];checks.append('portfolio actions preserve paper account and submit no broker orders')
                assert not errors,errors;browser.close()
        finally:process.terminate();process.join(15)
    record=dict(passed=len(checks),checks=checks,javascript_errors=errors,controlled_data=True,real_broker_reads=False,real_orders_sent=False,private_portfolio_artifacts_created=False)
    (ROOT/'docs/portfolio-browser-validation.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record))
if __name__=='__main__':main()
