"""Browser acceptance checks against an isolated disposable paper database."""
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
    for key in ("KITE_API_KEY","KITE_API_SECRET","KITE_ACCESS_TOKEN"):
        os.environ.pop(key,None)
    uvicorn.run(create_app(database,background=False),host="127.0.0.1",port=8788,access_log=False,log_level="error")


def main():
    from playwright.sync_api import sync_playwright,expect
    artifacts=ROOT/"docs/screenshots"
    artifacts.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="artha-browser-",dir=ROOT/"data") as directory:
        process=multiprocessing.get_context("spawn").Process(target=serve,args=(str(Path(directory)/"paper.sqlite3"),))
        process.start()
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen("http://127.0.0.1:8788/api/health",timeout=1).close()
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("Browser test server did not start")
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,args=["--no-sandbox"])
                page=browser.new_page(viewport={"width":1440,"height":1100},device_scale_factor=1)
                errors=[]
                page.on("pageerror",lambda error:errors.append(str(error)))
                page.on("console",lambda message:errors.append(message.text) if message.type=="error" else None)
                page.goto("http://127.0.0.1:8788",wait_until="networkidle")
                expect(page.locator("#metric-equity")).to_contain_text("10,00,000")
                expect(page.locator("#market-body tr")).to_have_count(6)
                page.screenshot(path=str(artifacts/"overview-desktop.png"),full_page=True)
                page.get_by_role("button",name="New paper trade",exact=True).click()
                expect(page.locator("#trade-drawer")).to_be_visible()
                expect(page.locator("#order-preview")).to_contain_text("Order passes current entry checks")
                page.screenshot(path=str(artifacts/"order-ticket.png"),full_page=True)
                page.get_by_role("button",name="Place paper order",exact=True).click()
                expect(page.locator("#trade-drawer")).not_to_be_visible()
                expect(page.locator("#orders-table")).to_contain_text("FILLED")
                page.locator('[data-view="journal"]').click()
                expect(page.locator("#decision-list")).to_contain_text("ACCEPT")
                page.locator('#decision-list > details').last.locator('summary').first.click()
                expect(page.locator('#decision-list')).to_contain_text('trade risk')
                page.screenshot(path=str(artifacts/"decision-history.png"),full_page=True)
                page.locator('[data-view="positions"]').click()
                expect(page.locator("#positions-table")).to_contain_text("RELIANCE")
                page.locator('#positions-table [data-close-position]').click()
                expect(page.locator("#positions-table")).to_contain_text("No open positions")
                for symbol in ("NIFTY-CALL-DEMO","NIFTY-FUT-DEMO"):
                    page.get_by_role("button",name="New paper trade",exact=True).click()
                    page.locator("#ticket-symbol").select_option(symbol)
                    if symbol=="NIFTY-FUT-DEMO":
                        page.locator('[data-side="SELL"]').click()
                    expect(page.locator("#order-preview")).to_contain_text("Order passes current entry checks")
                    page.get_by_role("button",name="Place paper order",exact=True).click()
                    expect(page.locator("#trade-drawer")).not_to_be_visible()
                    page.locator('[data-view="positions"]').click()
                    expect(page.locator("#positions-table")).to_contain_text(symbol)
                    if symbol=="NIFTY-CALL-DEMO":
                        expect(page.locator("#options-monitor")).to_contain_text("NIFTY-CALL-DEMO")
                        expect(page.locator("#options-monitor")).to_contain_text("QUOTED")
                        page.screenshot(path=str(artifacts/"options-monitor.png"),full_page=True)
                    page.locator('#positions-table [data-close-position]').click()
                    expect(page.locator("#positions-table")).to_contain_text("No open positions")
                page.locator('[data-view="risk"]').click()
                page.locator('[data-capital="100000"]').click()
                page.get_by_role("button",name="Update virtual allocation").click()
                expect(page.locator("#reconcile-status")).to_contain_text("RECONCILED")
                page.locator('[data-view="journal"]').click()
                page.locator("#journal-note").fill("Browser-verified research observation")
                page.get_by_role("button",name="Add note",exact=True).click()
                expect(page.locator("#journal-list")).to_contain_text("Browser-verified research observation")
                with page.expect_download() as download:
                    page.get_by_role("button",name="Export CSV",exact=True).click()
                assert download.value.suggested_filename=="artha-paper-journal.csv"
                page.locator('[data-view="strategies"]').click()
                page.locator("#strategy-enabled").check()
                page.get_by_role("button",name="Save strategy",exact=True).click()
                expect(page.locator("#strategy-status-full")).to_contain_text("Warming up")
                page.get_by_role('button',name='Settings',exact=True).click()
                expect(page.locator("#kite-status")).to_contain_text("not configured")
                page.on("dialog",lambda dialog:dialog.accept())
                page.get_by_role("button",name="Archive & start fresh",exact=True).click()
                expect(page.locator("#session-archives")).to_contain_text("#1")
                page.locator('[data-view="overview"]').click()
                expect(page.locator("#metric-pnl")).to_contain_text("0.00")
                page.set_viewport_size({"width":390,"height":844})
                page.locator("#toast").evaluate("element => element.classList.add('hidden')")
                page.screenshot(path=str(artifacts/"overview-mobile.png"),full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.get_by_role("button",name="New paper trade",exact=True).click()
                expect(page.locator("#trade-drawer")).to_be_visible()
                page.keyboard.press("Escape")
                expect(page.locator("#trade-drawer")).not_to_be_visible()
                assert not errors,errors
                browser.close()
                result={"status":"passed","checks":["dashboard","order preview","paper equity entry/exit","paper option entry/exit","paper short future entry/exit","capital update","journal note","CSV export","strategy configuration","decision evidence","option leg monitoring","session archive","mobile layout","keyboard dismissal"],"javascript_errors":errors}
                (ROOT/"docs/browser-validation.json").write_text(json.dumps(result,indent=2))
                print(json.dumps(result,indent=2))
        finally:
            process.terminate()
            process.join(timeout=10)


if __name__=="__main__":
    main()
