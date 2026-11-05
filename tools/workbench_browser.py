"""Controlled customer journeys for the 0.8 workspace. No real quotes, provider or broker calls."""
import json,multiprocessing,os,sys,tempfile,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def serve(database):
    import uvicorn
    from datetime import datetime
    from app.engine import IST
    from app.api import create_app
    from app.market import KiteData
    sys.path.insert(0,str(ROOT/'tests'))
    from test_workbench import FakeKite
    fixture=FakeKite(lambda:datetime.now(IST))
    KiteData._request=lambda self,method,path,**kw:fixture._request(method,path,**kw)
    KiteData.master=lambda self:fixture.master()
    for key in ('KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN','ARTHA_AI_API_KEY','ARTHA_AI_MODEL','ARTHA_LIVE_TRANSPORT_ENABLED'):os.environ.pop(key,None)
    app=create_app(database,background=False)
    from test_portfolio_view import snapshot as portfolio_fixture
    app.state.pilot.broker.snapshot=portfolio_fixture
    @app.post('/fixture/portfolio')
    def portfolio_snapshot():return app.state.pilot.reconcile()
    app.state.kite.key='controlled-key';app.state.kite.secret='controlled-secret';app.state.kite.master_hash=fixture.master_hash
    @app.post('/fixture/login')
    def login():app.state.kite.token='controlled-session';return {'ok':True}
    @app.post('/fixture/offline')
    def offline():
        def missing():raise ValueError('Controlled unavailable broker master')
        app.state.kite.master=missing;app.state.kite.token='';return {'ok':True}
    @app.post('/fixture/stale')
    def stale():
        from datetime import timedelta
        app.state.workspace.clock=lambda:datetime.now(IST)+timedelta(seconds=40);return {'ok':True}
    @app.post('/fixture/fresh')
    def fresh():app.state.workspace.clock=lambda:datetime.now(IST);return {'ok':True}
    @app.post('/fixture/tick')
    def tick():
        at=datetime.now(IST);q=dict(instrument_token=257,last=1512.25,bid=1512.20,ask=1512.30,bid_quantity=6500,ask_quantity=6500,exchange_time=at.isoformat(),received_at=at.timestamp(),transport='KITE_WEBSOCKET',cumulative_volume=15000,close=1485,open=1490,high=1520,low=1480)
        hub=app.state.market_hub
        with hub.lock:hub.quotes[257]=q
        hub.display_bars.observe(q)
        return {'ok':True}
    @app.post('/fixture/logout')
    def logout():app.state.kite.token='';return {'ok':True}
    uvicorn.run(app,host='127.0.0.1',port=8796,access_log=False,log_level='error',timeout_graceful_shutdown=2)

def main():
    from playwright.sync_api import sync_playwright,expect
    class Checks(list):
        def append(self,value):super().append(value);print(json.dumps({'passed_stage':value}),flush=True)
    checks=Checks();errors=[];push_to_dom_ms=None
    with tempfile.TemporaryDirectory(prefix='artha-workbench-browser-',dir=ROOT/'data') as directory:
        process=multiprocessing.get_context('spawn').Process(target=serve,args=(str(Path(directory)/'paper.sqlite3'),));process.start()
        try:
            for _ in range(100):
                try:urllib.request.urlopen('http://127.0.0.1:8796/api/health',timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Controlled workspace server did not start')
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1600,'height':1100});page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto('http://127.0.0.1:8796/',wait_until='networkidle')
                page.evaluate("const b=document.createElement('div');b.textContent='CONTROLLED UI FIXTURE · NOT MARKET PRICES';b.style='position:fixed;bottom:0;right:0;background:#253858;color:white;padding:5px 10px;font-size:10px;z-index:9999';document.body.append(b)")
                expect(page.locator('#view-home')).to_be_visible();expect(page.locator('#home-next-step')).to_contain_text('Connect your market');checks.append('guided home has a clear connection next step')
                page.screenshot(path=str(ROOT/'docs/screenshots/workbench-home-controlled.png'),full_page=True)
                page.locator('#home-practice').click();expect(page.locator('#view-trade')).to_be_visible();expect(page.locator('#desk-market-label')).to_contain_text('PRACTICE');expect(page.locator('#watch-items')).to_contain_text('RELIANCE');checks.append('explicit practice path from home into trading desk')
                page.locator('#desk-buy').click();expect(page.locator('#trade-drawer')).to_be_visible();expect(page.locator('#ticket-heading')).to_contain_text('paper');expect(page.locator('#submit-trade')).to_be_enabled();page.locator('#submit-trade').click();expect(page.locator('#trade-drawer')).to_be_hidden();checks.append('watchlist-chart context opens a risk-checked paper order')
                page.get_by_role('button',name='Orders',exact=True).click();expect(page.locator('#orders-table')).to_contain_text('FILLED');checks.append('submitted paper order visible in Orders')
                headers={'Content-Type':'application/json','X-Artha-Client':'paper-workspace'};page.request.post('http://127.0.0.1:8796/fixture/login',data='{}',headers=headers)
                page.get_by_role('button',name='Trade',exact=True).click();page.locator('#watch-live').click();page.locator('#watch-refresh').click();page.locator('#price-refresh').click();expect(page.locator('#price-source')).to_contain_text('KITE');expect(page.locator('#price-chart canvas').first).to_be_visible();checks.append('genuine-contract fixture candles with separate market-data source')
                expect(page.locator('#watch-stream-status')).to_contain_text('Browser stream connected',timeout=15000)
                tick_started=time.monotonic();page.request.post('http://127.0.0.1:8796/fixture/tick',data='{}',headers=headers);expect(page.locator('#price-close')).to_contain_text('1,512.25',timeout=3000);push_to_dom_ms=round((time.monotonic()-tick_started)*1000);expect(page.locator('#chart-live-status')).to_contain_text('STREAMING');expect(page.locator('#desk-ohlc')).to_contain_text('DEVELOPING');checks.append('pushed quote updates headline LTP and developing candle without a manual refresh')
                page.request.post('http://127.0.0.1:8796/fixture/stale',data='{}',headers=headers);expect(page.locator('#chart-live-status')).to_have_text('SAVED / STALE');page.request.post('http://127.0.0.1:8796/fixture/fresh',data='{}',headers=headers);expect(page.locator('#chart-live-status')).to_contain_text('STREAMING');checks.append('stale quote loses live label and fresh observations restore it')
                page.locator('#watchlist-new').click();page.locator('#new-watchlist-form [name="name"]').fill('Controlled intraday');page.locator('#new-watchlist-form button[type="submit"]').click();expect(page.locator('#watch-items')).to_contain_text('Build your watchlist');page.locator('#watch-search').fill('TCS');page.locator('[data-add-watchlist="NSE:TCS"]').click();expect(page.locator('#watch-items')).to_contain_text('TCS');page.locator('#watch-search').fill('RELIANCE');page.locator('[data-add-watchlist="NSE:RELIANCE"]').click();expect(page.locator('#watch-items')).to_contain_text('RELIANCE');checks.append('create named watchlist and add current instruments through search')
                page.locator('#watchlist-menu').click();page.locator('#rename-watchlist-form [name="name"]').fill('Controlled watchlist');page.locator('#rename-watchlist-form button[type="submit"]').click();expect(page.locator('#watchlist-select')).to_contain_text('Controlled watchlist');page.locator('#watchlist-menu').click();page.locator('[data-watch-move="1"][data-watch-direction="-1"]').click();expect(page.locator('.watchlist-reorder>div').first).to_contain_text('RELIANCE');page.locator('#workspace-dialog-close').click();page.locator('#watch-sort').select_option('name');expect(page.locator('#watch-items>.watch-item').first).to_contain_text('RELIANCE');page.locator('#watch-change-mode').click();expect(page.locator('#watch-change-mode')).to_have_text('Change ₹');page.locator('#watchlist-select').select_option('main');expect(page.locator('#watch-items>.watch-item')).to_have_count(5);checks.append('rename reorder sort change display and switch watchlists')
                page.locator('#desk-sma').check();page.locator('#price-chart').hover();checks.append('candlestick chart crosshair and moving averages')
                page.screenshot(path=str(ROOT/'docs/screenshots/workbench-trade-controlled.png'),full_page=True)
                page.get_by_role('button',name='Strategy Builder',exact=True).click();expect(page.locator('#template-count')).to_contain_text('51');page.locator('#builder-refresh').click();page.locator('[data-builder-tab="chain"]').click();expect(page.locator('#option-chain')).to_contain_text('CALLS');expect(page.locator('#option-chain')).to_contain_text('PUTS');checks.append('option chain exposes current strikes premiums OI IV and buy/sell actions')
                page.screenshot(path=str(ROOT/'docs/screenshots/workbench-chain-controlled.png'),full_page=True)
                page.locator('[data-template="bull_call"]').click();expect(page.locator('#leg-count')).to_have_text('2');expect(page.locator('#builder-payoff svg')).to_be_visible();expect(page.locator('#builder-metrics')).to_contain_text('Maximum profit');checks.append('bull-call template produces exact-contract basket and payoff')
                page.locator('#scenario-move').fill('2');page.locator('#scenario-iv').fill('3');expect(page.locator('#scenario-move-value')).to_have_text('2%');expect(page.locator('#builder-greeks')).to_contain_text('Vega');checks.append('price/date/IV scenarios and position Greeks')
                page.locator('#scenario-table-toggle').click();expect(page.locator('#scenario-table')).to_be_visible();checks.append('target-date and expiry P&L table')
                page.locator('#builder-name').fill('Controlled bull-call research');page.locator('#builder-save').click();expect(page.locator('#builder-draft-state')).to_have_text('SAVED REVISION');checks.append('immutable strategy draft saved from visual controls')
                expect(page.locator('#payoff-subtitle')).to_contain_text('Expiry contract payoff');page.locator('#toast').evaluate("e=>e.classList.add('hidden')");page.screenshot(path=str(ROOT/'docs/screenshots/workbench-builder-controlled.png'),full_page=True)
                page.get_by_role('button',name='My Strategies',exact=True).click();expect(page.locator('#saved-drafts')).to_contain_text('Controlled bull-call research');assert page.evaluate('scrollY===0');page.locator('[data-open-draft]').first.click();expect(page.locator('#builder-name')).to_have_value('Controlled bull-call research');assert page.locator('#scenario-move').input_value()=='2' and page.locator('#scenario-iv').input_value()=='3';checks.append('saved strategy resumes with its saved scenario')
                page.locator('.leg-table [data-leg-field="premium"]').first.fill('200');page.locator('#builder-save').click();expect(page.locator('#builder-draft-state')).to_have_text('SAVED REVISION');drafts=page.request.get('http://127.0.0.1:8796/api/workspace/drafts').json();assert any(d['parent_id'] for d in drafts);checks.append('edited premium creates a linked immutable draft revision')
                page.get_by_role('button',name='My Strategies',exact=True).click();page.locator('[data-compare-draft]').nth(0).check();page.locator('[data-compare-draft]').nth(1).check();page.locator('#compare-drafts').click();expect(page.locator('#workspace-dialog')).to_contain_text('Compare saved strategies');expect(page.locator('#workspace-dialog')).to_contain_text('Different input bases');page.locator('#workspace-dialog-close').click();checks.append('comparison keeps immutable draft bases explicit')
                page.locator('[data-open-draft]').first.click();page.locator('#builder-wizard').click();page.locator('#workspace-screener [name="category"]').select_option('Bullish');page.locator('#workspace-screener [name="loss"]').fill('50000');page.locator('#workspace-screener button[type="submit"]').click();expect(page.locator('#screener-results [data-load-screened]').first).to_be_visible();page.locator('#screener-results [data-load-screened]').first.click();expect(page.locator('#workspace-dialog')).not_to_be_visible();expect(page.locator('#builder-payoff svg')).to_be_visible();checks.append('scenario screener filters defined loss and opens a buildable strategy')
                page.locator('[data-template="call_calendar"]').click();expect(page.locator('#leg-count')).to_have_text('2');expect(page.locator('#builder-metrics')).to_contain_text('Path dependent');checks.append('calendar spread retains later-expiry time value and does not invent loss bounds')
                page.locator('[data-builder-tab="insights"]').click();expect(page.locator('#oi-chart svg')).to_be_visible();expect(page.locator('#iv-chart svg')).to_be_visible();checks.append('observed OI and estimated-IV analysis')
                page.locator('[data-builder-tab="build"]').click();page.locator('[data-builder-kind="RULES"]').click();expect(page.locator('#rule-builder')).to_be_visible();page.locator('#rule-reference').fill('1500');page.locator('#builder-name').fill('Controlled trend rules');page.locator('#builder-save').click();expect(page.locator('#builder-draft-state')).to_have_text('SAVED REVISION');checks.append('stock-rule builder works without authoring JSON')
                page.locator('#builder-forward').click();page.locator('#flow-reviewer').fill('Controlled owner');page.locator('#flow-review-note').fill('Controlled calendar contract and execution assumptions reviewed for software acceptance');page.locator('#flow-rights').check();page.locator('#flow-assumptions').check();page.locator('#workspace-forward button[type="submit"]').click();expect(page.locator('#saved-forward')).to_contain_text('PAUSED',timeout=15000);expect(page.locator('#saved-forward')).to_contain_text('Controlled trend rules');checks.append('reviewed rules become an isolated paused forward portfolio')
                response=page.request.post('http://127.0.0.1:8796/api/research/datasets/synthetic',data='{}',headers=headers);assert response.ok;dataset=response.json()
                page.get_by_role('button',name='Strategy Builder',exact=True).click();page.locator('#builder-backtest').click();page.locator('#flow-dataset').select_option(dataset['id']);page.locator('#flow-mappings [name="underlying"]').select_option('NSE:ALPHA');page.locator('#flow-mappings [name="0"]').select_option('NSE:ALPHA');page.locator('#workspace-backtest button[type="submit"]').click();expect(page.locator('#saved-runs')).to_contain_text('completed',timeout=45000);checks.append('explicit historical instrument mapping produces a completed backtest')
                page.get_by_role('button',name='Trade',exact=True).click();page.locator('#ws-account').select_option('LIVE');page.locator('#desk-buy').click();expect(page.locator('#live-ticket-panel')).to_be_visible();expect(page.locator('#pilot-preview-button')).to_be_disabled();expect(page.locator('#live-ticket-status')).to_contain_text('disabled');page.locator('#close-ticket').click();page.locator('#ws-account').select_option('PAPER');checks.append('live order ticket remains gated and separate from paper')
                page.set_viewport_size({'width':390,'height':844});page.get_by_role('button',name='Home',exact=True).click();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2');checks.append('guided home mobile layout');page.screenshot(path=str(ROOT/'docs/screenshots/workbench-home-mobile.png'),full_page=True)
                page.get_by_role('button',name='Trade',exact=True).click();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2');checks.append('trading desk mobile layout');expect(page.locator('#price-interval')).to_be_visible();interval_bounds=page.locator('#price-interval').bounding_box();assert interval_bounds['x']+interval_bounds['width']<=390;checks.append('mobile chart interval remains inside the visible desk');page.locator('#toast').evaluate("e=>e.classList.add('hidden')");page.screenshot(path=str(ROOT/'docs/screenshots/workbench-trade-mobile.png'),full_page=True)
                page.get_by_role('button',name='Strategy Builder',exact=True).click();page.locator('[data-builder-kind="OPTIONS"]').click();page.locator('[data-template="iron_condor"]').click();expect(page.locator('#leg-count')).to_have_text('4');expect(page.locator('#payoff-subtitle')).to_contain_text('Expiry contract payoff');assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2');checks.append('four-leg options builder mobile layout');expect(page.locator('.mobile-leg-cards')).to_be_visible();mobile_price=page.locator('.mobile-leg-cards [data-leg-field="premium"]').first;original_price=mobile_price.input_value();mobile_price.fill('10');expect(page.locator('#payoff-subtitle')).to_contain_text('Expiry contract payoff');checks.append('mobile leg cards support direct premium editing');mobile_price.fill(original_price);expect(page.locator('#payoff-subtitle')).to_contain_text('Expiry contract payoff');page.locator('#scenario-table-toggle').click();page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(ROOT/'docs/screenshots/workbench-builder-mobile.png'),full_page=True)
                health=page.request.get('http://127.0.0.1:8796/api/health').json();assert health['reconciliation']['ok'] and not health['pilot']['transport_enabled'];checks.append('account reconciliation preserved and real transport remains off')
                page.request.post('http://127.0.0.1:8796/fixture/offline',data='{}',headers=headers);page.goto('http://127.0.0.1:8796/#builder',wait_until='networkidle');page.reload(wait_until='networkidle');assert page.request.get('http://127.0.0.1:8796/api/workspace/meta').json()['master_available'] is False;expect(page.locator('#template-count')).to_contain_text('51');expect(page.locator('#builder-payoff')).to_contain_text('See the shape of your strategy');page.locator('#builder-source').select_option('HYPOTHETICAL');expect(page.locator('#hypothetical-inputs')).to_be_visible();page.locator('#hypo-spot').fill('25000');page.locator('#hypo-lot-size').fill('65');page.locator('[data-template="call_calendar"]').click();expect(page.locator('#leg-count')).to_have_text('2');expect(page.locator('#builder-metrics')).to_contain_text('Path dependent');checks.append('broker-independent hypothetical templates with explicit dates spot lot size and strike spacing')
                page.get_by_role('button',name='Trade',exact=True).click();expect(page.locator('#watch-stream-status')).to_contain_text('Kite disconnected');process.terminate();process.join(10);assert not process.is_alive();checks.append('server shuts down within its bound with an active quote stream')
                browser.close()
        finally:process.terminate();process.join(15)
    record=dict(passed=len(checks),checks=checks,controlled_push_to_dom_ms=push_to_dom_ms,javascript_errors=errors,controlled_data=True,real_broker_orders_sent=False,real_provider_calls=False)
    (ROOT/'docs/workbench-browser-validation.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record));assert not errors

if __name__=='__main__':main()
