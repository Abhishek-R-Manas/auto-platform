import asyncio
import csv
import hmac
import io
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictBool, StrictInt
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .engine import PaperEngine, IST
from .market import KiteData
from .configuration import PrivateConfig
from .onboarding import ConnectionSetup, router as onboarding_router
from .live import MarketHub
from .candidates.service import StrategyLab
from .candidates.api import router as candidates_router
from .research.jobs import Research
from .research.api import router as research_router
from .forward.service import ForwardService
from .forward.api import router as forward_router
from .intelligence.service import DataFeeds
from .intelligence.api import router as intelligence_router
from .charts import MarketCharts, router as charts_router
from .pilot.service import Pilot
from .pilot.api import router as pilot_router
from .workbench.service import Workspace
from .workbench.api import router as workspace_router

ROOT=Path(__file__).resolve().parent.parent


class StrictModel(BaseModel):
    model_config=ConfigDict(extra="forbid")


class Trade(StrictModel):
    symbol: Annotated[str,Field(min_length=1,max_length=80)]
    side: Literal["BUY","SELL"]="BUY"
    quantity: Annotated[StrictInt,Field(gt=0,le=10_000_000)]=1
    order_type: Literal["MARKET","LIMIT"]="MARKET"
    limit_price: Annotated[FiniteFloat,Field(gt=0,le=100_000_000)]|None=None
    stop_loss: Annotated[FiniteFloat,Field(gt=0,le=100_000_000)]|None=None
    take_profit: Annotated[FiniteFloat,Field(gt=0,le=100_000_000)]|None=None
    rationale: Annotated[str,Field(max_length=1000)]|None=None
    reduce_only: StrictBool=False
    client_id: Annotated[str,Field(min_length=1,max_length=100)]|None=None


class Capital(StrictModel):
    amount: Annotated[FiniteFloat,Field(ge=100,le=1_000_000_000)]


class Paused(StrictModel):
    paused: StrictBool


class Autoplay(StrictModel):
    enabled: StrictBool


class Steps(StrictModel):
    count: Annotated[StrictInt,Field(ge=1,le=100)]=1


class Source(StrictModel):
    source: Literal["DEMO","KITE"]
    instruments: Annotated[list[Annotated[str,Field(pattern=r"^(?:NSE|NFO):[A-Z0-9 &_.\-]{1,70}$")]],Field(min_length=1,max_length=50)]|None=None


class CaptureStart(StrictModel):
    name: Annotated[str,Field(min_length=1,max_length=100)]="Forward paper quote capture"
    rights_confirmed: StrictBool=False


class Note(StrictModel):
    text: Annotated[str,Field(min_length=1,max_length=1000)]


class Risk(StrictModel):
    trade_pct: Annotated[FiniteFloat,Field(gt=0,le=8)]
    open_pct: Annotated[FiniteFloat,Field(gt=0,le=25)]
    daily_pct: Annotated[FiniteFloat,Field(gt=0,le=25)]
    weekly_pct: Annotated[FiniteFloat,Field(gt=0,le=30)]
    drawdown_pct: Annotated[FiniteFloat,Field(gt=0,le=50)]
    margin_pct: Annotated[FiniteFloat,Field(gt=0,le=50)]
    slippage_bps: Annotated[FiniteFloat,Field(gt=0,le=50)]
    overnight_stress_pct: Annotated[FiniteFloat,Field(gt=0,le=25)]


class Strategy(StrictModel):
    enabled: StrictBool
    symbol: str="RELIANCE"
    fast: Annotated[StrictInt,Field(ge=2,le=99)]=5
    slow: Annotated[StrictInt,Field(ge=3,le=100)]=20
    stop_pct: Annotated[FiniteFloat,Field(ge=.1,le=20)]=2


def create_app(db_path=None,background=True,config_path=None):
    engine=PaperEngine(db_path or os.getenv("ARTHA_DB",str(ROOT/"data/paper.sqlite3")))
    config=PrivateConfig(config_path or (Path(db_path).parent/".env.local" if db_path else ROOT/".env.local"))
    saved=config.read()
    kite=KiteData()
    for name,attribute in (("KITE_API_KEY","key"),("KITE_API_SECRET","secret"),("KITE_ACCESS_TOKEN","token")):
        if name in saved:setattr(kite,attribute,saved[name])
    research_root=Path(db_path).parent/"research" if db_path else Path(os.getenv("ARTHA_RESEARCH_ROOT",str(ROOT/"data/research")))
    research=Research(research_root)
    hub=MarketHub(kite,engine,research_root.parent/"market-data")
    lab=StrategyLab(research_root.parent/"strategy-lab",research)
    for name,attribute in (("ARTHA_AI_API_KEY","key"),("ARTHA_AI_MODEL","model")):
        if name in saved:setattr(lab.provider,attribute,saved[name])
    forward=ForwardService(research_root.parent/"forward",kite,hub,lab,config)
    forward.provider.key=lab.provider.key;forward.provider.model=lab.provider.model
    intelligence=DataFeeds(research_root.parent/"intelligence",kite)
    forward.intelligence=intelligence
    charts=MarketCharts(research_root.parent/"charts",kite)
    pilot=Pilot(research_root.parent/"pilot",kite,forward)
    workspace=Workspace(research_root.parent/"workspace",kite,hub,research)
    intelligence.policy_guard=forward.lock
    from .team.service import Team
    from .team.api import install as install_team
    team=Team(research_root.parent/"teams/default")
    oauth_states={}
    setup=ConnectionSetup(kite,hub,engine,config)
    setup.ai_status=lambda:lab.provider.status()['configured']
    class RotationGuard:
        def __enter__(self):
            pilot.lock.acquire();forward.lock.acquire()
        def __exit__(self,*args):
            forward.lock.release();pilot.lock.release()
    setup.rotation_lock=RotationGuard();setup.forward_exposure=lambda:pilot.has_exposure() or forward.has_exposure();setup.pause_forward=forward.pause_for_credentials

    def apply_live_quotes(quotes,instruments):
        with engine.lock:
            if engine.state['source']!='KITE' or engine.state['instruments']!=instruments: return False
            engine.apply_quotes(quotes,1,datetime.now(IST),'KITE')
            return True

    async def market_loop(stop):
        while not stop.is_set():
            try:
                with engine.lock:
                    source=engine.state["source"]
                    automatic=engine.state["auto_feed"]
                    instruments=engine.state["instruments"].copy()
                if source=="DEMO" and automatic:
                    await asyncio.to_thread(engine.step)
                elif source=="KITE":
                    quotes=hub.drain(instruments)
                    connected=hub.phase=="STREAMING" and bool(hub.connections) and all(v=="CONNECTED" for v in hub.connections.values())
                    if quotes and connected:
                        applied=await asyncio.to_thread(apply_live_quotes,quotes,instruments)
                        if not applied: continue
                        try:
                            await asyncio.to_thread(hub.store.append,quotes,instruments)
                            hub.recording_error=None
                        except Exception:
                            hub.recording_error="Quote recording failed; stop the capture and check disk space"
                    elif hub.phase!="STREAMING" or not hub.connections or any(v!="CONNECTED" for v in hub.connections.values()):
                        await asyncio.to_thread(engine.market_error,hub.error or "Waiting for authenticated market stream")
            except Exception as exc:
                # Never persist exception text from HTTP clients: it can contain credential-bearing headers.
                message=str(exc) if isinstance(exc,ValueError) else "Market processing failed; entries require fresh data and operator review"
                await asyncio.to_thread(engine.market_error,message)
            try:
                await asyncio.wait_for(stop.wait(),timeout=2 if source=="DEMO" else 1)
            except TimeoutError:
                pass

    async def candidate_loop(stop):
        while not stop.is_set():
            await asyncio.to_thread(lab.tick)
            try: await asyncio.wait_for(stop.wait(),timeout=1)
            except TimeoutError: pass

    async def forward_loop(stop):
        while not stop.is_set():
            try:await asyncio.to_thread(forward.tick)
            except Exception:forward.last_error="Forward supervisor failed; entries require review"
            try:await asyncio.wait_for(stop.wait(),timeout=1)
            except TimeoutError:pass

    async def pilot_loop(stop):
        while not stop.is_set():
            try:await asyncio.to_thread(pilot.tick)
            except Exception:await asyncio.to_thread(pilot.pause)
            try:await asyncio.wait_for(stop.wait(),timeout=2)
            except TimeoutError:pass

    async def input_loop(stop):
        while not stop.is_set():
            try:await asyncio.to_thread(intelligence.tick)
            except Exception:intelligence.last_error='Input supervisor failed; inspect input storage and source jobs'
            try:await asyncio.wait_for(stop.wait(),timeout=1)
            except TimeoutError:pass

    async def service_watchdog(stop):
        from .operations import notify
        from datetime import timezone
        launched=datetime.now(timezone.utc)
        notify('READY=1\nSTATUS=Paper service ready; independent forward supervision active')
        while not stop.is_set():
            latest=forward.last_tick
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(latest)).total_seconds() if latest else (datetime.now(timezone.utc)-launched).total_seconds()
            pilot_age=(datetime.now(timezone.utc)-datetime.fromisoformat(pilot.last_tick)).total_seconds() if pilot.last_tick else (datetime.now(timezone.utc)-launched).total_seconds()
            if age<30 and forward.last_error is None and (not pilot.watch_required or pilot_age<30):notify('WATCHDOG=1')
            else:notify('STATUS=Financial supervisor stalled; service-manager watchdog will recover paused')
            try:await asyncio.wait_for(stop.wait(),timeout=5)
            except TimeoutError:pass
        notify('STOPPING=1')

    @asynccontextmanager
    async def lifespan(app):
        stop=asyncio.Event()
        task=asyncio.create_task(market_loop(stop)) if background else None
        feed_task=asyncio.create_task(hub.run(stop)) if background else None
        lab_task=asyncio.create_task(candidate_loop(stop)) if background else None
        forward_task=asyncio.create_task(forward_loop(stop)) if background else None
        watchdog_task=asyncio.create_task(service_watchdog(stop)) if background else None
        input_task=asyncio.create_task(input_loop(stop)) if background else None
        pilot_task=asyncio.create_task(pilot_loop(stop)) if background else None
        yield
        stop.set()
        await asyncio.to_thread(team.close)
        if pilot_task:await pilot_task
        pilot.close();charts.close();workspace.close()
        if input_task:await input_task
        if watchdog_task:await watchdog_task
        if forward_task:await forward_task
        await asyncio.to_thread(forward.close)
        await asyncio.to_thread(intelligence.close)
        if lab_task: await lab_task
        await asyncio.to_thread(lab.close)
        if task:
            await task
        if feed_task:
            feed_task.cancel()
            try: await feed_task
            except asyncio.CancelledError: pass
        hub.store.close()
        await asyncio.to_thread(research.close)
        engine.close()

    app=FastAPI(title="Artha Paper Trading",version="0.20.0",lifespan=lifespan,
                description="Single-user paper research, cached market charts and a separately gated supervised broker pilot.",docs_url="/api/docs",redoc_url=None)
    install_team(app,team)
    app.state.engine=engine
    app.state.kite=kite
    app.state.market_hub=hub
    app.state.research=research
    app.state.candidates=lab
    app.state.onboarding=setup
    app.state.forward=forward
    app.state.intelligence=intelligence
    app.state.workspace=workspace
    app.include_router(workspace_router(workspace,forward,pilot))
    from .contracts import router as contracts_router
    app.include_router(contracts_router(workspace))
    from .contract_research import router as contract_research_router
    app.include_router(contract_research_router(workspace))
    app.state.charts=charts
    app.state.pilot=pilot
    app.include_router(charts_router(charts))
    app.include_router(pilot_router(pilot))
    app.include_router(intelligence_router(intelligence,research.catalog))
    app.include_router(forward_router(forward))
    app.include_router(onboarding_router(setup,oauth_states))
    app.include_router(candidates_router(lab))
    app.include_router(research_router(research,kite))
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=["127.0.0.1","localhost"])

    @app.middleware("http")
    async def boundaries(request:Request,call_next):
        try:
            length=int(request.headers.get("content-length","0") or 0)
        except ValueError:
            return JSONResponse({"detail":"Invalid content length"},400)
        maximum=34000000 if request.url.path in {"/api/research/datasets/import","/api/research/datasets/kite"} else 1000000 if request.url.path.startswith(("/api/research/","/api/candidates/","/api/forward/","/api/intelligence/","/api/contract-research/verify")) else 16384
        if request.url.path in {'/api/contract-research/verify','/api/team/verify'}:maximum=16*1024*1024
        if request.url.path=='/api/team/datasets':maximum=34000000
        if length>maximum:
            return JSONResponse({"detail":"Request exceeds the size limit"},413)
        if request.method not in {"GET","HEAD","OPTIONS"}:
            if "content-length" not in request.headers:
                return JSONResponse({"detail":"Content length is required"},411)
            origin=request.headers.get("origin")
            if request.headers.get("x-artha-client")!="paper-workspace" or request.headers.get("sec-fetch-site")=="cross-site":
                return JSONResponse({"detail":"Same-origin workspace request required"},403)
            if origin and urlsplit(origin).netloc!=request.headers.get("host"):
                return JSONResponse({"detail":"Cross-origin mutation denied"},403)
            if not request.headers.get("content-type","").startswith("application/json"):
                return JSONResponse({"detail":"JSON content type required"},415)
            if len(await request.body())>maximum:
                return JSONResponse({"detail":"Request exceeds the size limit"},413)
        response=await call_next(request)
        response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="no-referrer"
        response.headers["Cache-Control"]="no-store" if request.url.path.startswith("/api") else "no-cache"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        if request.url.path in {'/api/forward/ai/configure','/api/forward/notifications/configure'}:
            return JSONResponse({'detail':'Check the private provider configuration fields; credential values are never returned'},422)
        if request.url.path.startswith('/api/intelligence/'):
            return JSONResponse({'detail':'Check the report type, official public source URL, timestamps and input limits; submitted content is not echoed'},422)
        if request.url.path.startswith('/api/team'):
            return JSONResponse({'detail':'Check the team request fields; submitted values are not returned'},422)
        if request.url.path.startswith(('/api/contracts','/api/contract-research')):
            fields=[dict(loc=list(e['loc']),msg=e['msg'],type=e['type']) for e in exc.errors()]
            return JSONResponse({'detail':fields},422)
        if request.url.path.startswith("/api/onboarding/"):
            return JSONResponse({"detail":"Check the connection fields. Enter the API key and secret exactly as provided by Kite."},422)
        return await request_validation_exception_handler(request,exc)

    @app.exception_handler(ValueError)
    async def bad_request(request,exc):
        return JSONResponse({"detail":str(exc)},400)

    @app.get("/")
    def index():
        return FileResponse(ROOT/"static/workspace-v020.html")

    @app.get("/api/health")
    def health():
        audit=lab.verify_audit()
        forward_health=forward.status()
        return dict(pilot=dict(transport_enabled=pilot.broker.enabled,armed=pilot.status()["armed"],audit=pilot.audit()),status="ok" if pilot.audit()["ok"] and audit["ok"] and forward_health['audit']['ok'] and forward_health['worker']['healthy'] else "degraded",mode="PAPER",live_order_access=False,reconciliation=engine.reconcile(),candidate_registry=audit,ai_configured=lab.provider.status()["configured"],forward={key:forward_health[key] for key in ('worker','audit','portfolio_count','open_incidents','live_orders_enabled')})

    @app.get("/api/state")
    def state():
        return dict(engine.snapshot(),kite=kite.status(),market_data=hub.status())

    @app.get("/api/decisions")
    def decisions(before: Annotated[int,Field(gt=0)]|None=None, limit: Annotated[int,Field(ge=1,le=100)]=50, all_sessions: bool=False):
        return engine.decisions(before,limit,all_sessions)

    @app.get("/api/decisions/{decision_id}")
    def decision(decision_id: str):
        import json
        with engine.lock:
            row=engine.db.execute("SELECT sequence,document FROM decisions WHERE id=?",(decision_id,)).fetchone()
        if not row:
            return JSONResponse({"detail":"Decision not found"},404)
        return dict(json.loads(row["document"]),sequence=row["sequence"])

    @app.post("/api/orders/preview")
    def preview(body:Trade):
        return engine.preview(body.model_dump())

    @app.post("/api/orders/size")
    def size(body:Trade):
        return engine.calculate_size(body.model_dump())

    @app.post("/api/orders")
    def submit(body:Trade):
        return engine.submit(body.model_dump())

    @app.post("/api/orders/{order_id}/cancel")
    def cancel(order_id:str):
        return engine.cancel(order_id)

    @app.post("/api/positions/{symbol}/close")
    def close_position(symbol:str):
        return engine.close_position(symbol)

    @app.post("/api/controls/pause")
    def pause(body:Paused):
        engine.set_paused(body.paused)
        return {"paused":body.paused}

    @app.post("/api/controls/flatten")
    def flatten():
        return {"orders":engine.flatten(),"message":"Entries paused. Review fills and remaining positions."}

    @app.post("/api/controls/autoplay")
    def autoplay(body:Autoplay):
        with engine.atomic():
            engine.state["auto_feed"]=body.enabled
            engine._event("INFO","FEED","Synthetic market playback "+("started" if body.enabled else "stopped"))
        return {"enabled":body.enabled}

    @app.post("/api/controls/step")
    def step(body:Steps):
        engine.step(body.count)
        return {"tick":engine.state["tick"]}

    @app.post("/api/account/capital")
    def capital(body:Capital):
        engine.change_capital(body.amount)
        return engine.snapshot()["account"]

    @app.post("/api/account/new-session")
    def new_session(body:Capital):
        engine.new_session(body.amount)
        return engine.snapshot()["account"]

    @app.get("/api/account/archives")
    def archives():
        import json
        with engine.lock:
            rows=engine.db.execute("SELECT * FROM archives ORDER BY id DESC LIMIT 100").fetchall()
        result=[]
        for row in rows:
            document=json.loads(row["document"])
            result.append(dict(id=row["id"],ended=row["ended"],capital=document["capital"]/100,
                               pnl=(document["cash"]-document["capital"])/100,orders=len(document["orders"]),source=document["source"]))
        return result

    @app.post("/api/risk")
    def risk(body:Risk):
        engine.configure_risk(body.model_dump())
        return engine.state["risk"]

    @app.post("/api/strategy")
    def strategy(body:Strategy):
        engine.configure_strategy(**body.model_dump())
        return engine.state["strategies"][0]

    @app.post("/api/journal/notes")
    def note(body:Note):
        with engine.atomic():
            engine._event("INFO","NOTE",body.text)
        return {"saved":True}

    @app.get("/api/journal/export")
    def export():
        with engine.lock:
            events=engine.db.execute("SELECT time,level,kind,message,details FROM journal ORDER BY id").fetchall()
        buffer=io.StringIO()
        writer=csv.writer(buffer)
        writer.writerow(["time","level","kind","message","details"])
        for event in events:
            writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(("=","+","-","@")) else v for v in event])
        return Response(buffer.getvalue(),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=artha-paper-journal.csv"})

    @app.get("/api/market/instruments/history")
    def instrument_history(limit: Annotated[int,Field(ge=1,le=100)]=20):
        import json
        with engine.lock:
            rows=engine.db.execute("SELECT * FROM instrument_history ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
        return [dict(id=row["id"],observed_at=row["observed_at"],source=row["source"],**json.loads(row["document"])) for row in rows]

    @app.post("/api/market/source")
    def source(body:Source):
        if body.source=="KITE":
            instruments,quotes=kite.load(body.instruments) if body.instruments else kite.load()
            with engine.lock:
                engine.switch_source("KITE",instruments,quotes)
                engine.market_error("Waiting for live stream quotes; paper entries require successful data recovery")
            hub.enable(True)
        else:
            engine.switch_source("DEMO")
        for capture in hub.store.list():
            if capture['status']=='RECORDING': hub.store.stop(capture['id'],'SOURCE_CHANGED')
        return {"source":engine.state["source"],"execution":"PAPER"}

    @app.get("/api/market/status")
    def market_status(): return hub.status()

    @app.post("/api/market/connect")
    def connect_market(body:Autoplay):
        if body.enabled and not kite.status()['authenticated']: raise ValueError("Configure server credentials and authenticate a subscribed Kite data session first")
        if not body.enabled and engine.state['source']=='KITE': raise ValueError("Switch the paper desk to demo before disconnecting its data feed")
        hub.enable(body.enabled)
        return hub.status()

    @app.get("/api/market/universe")
    def market_universe(q:Annotated[str,Field(max_length=80)]="",offset:Annotated[int,Field(ge=0)]=0,limit:Annotated[int,Field(ge=1,le=200)]=50):
        return hub.scan(q,offset,limit)

    @app.get("/api/market/search")
    def market_search(q:Annotated[str,Field(min_length=2,max_length=80)]): return kite.search(q)

    @app.get("/api/market/captures")
    def captures(): return hub.store.list()

    @app.post("/api/market/captures")
    def start_capture(body:CaptureStart):
        source,instruments=hub.desired()
        if source!='KITE' or not kite.status()['authenticated']: raise ValueError("Connect an authenticated Kite paper watchlist before recording")
        return hub.store.start(body.name,instruments,body.rights_confirmed)

    @app.post("/api/market/captures/{identifier}/stop")
    def stop_capture(identifier:str): return hub.store.stop(identifier)

    @app.post("/api/market/captures/{identifier}/dataset")
    def capture_dataset(identifier:str): return hub.store.import_into(identifier,research.catalog)

    @app.get("/api/kite/login")
    def kite_login(return_to:Literal["settings","setup"]="settings"):
        state=secrets.token_urlsafe(32)
        oauth_states.clear()
        oauth_states[state]=dict(expires=__import__("time").time()+600,destination="/#setup" if return_to=="setup" else "/?kite=connected")
        response=RedirectResponse(kite.login_url(state))
        response.set_cookie("artha_oauth",state,httponly=True,samesite="lax",max_age=600)
        return response

    @app.get("/api/kite/callback")
    def kite_callback(request:Request):
        import time
        state=request.query_params.get("state","")
        expected=request.cookies.get("artha_oauth","")
        login=oauth_states.pop(state,None)
        if not state or not expected or not hmac.compare_digest(state,expected) or not login or login["expires"]<time.time():
            raise ValueError("Invalid or expired authentication state; begin login again")
        token=request.query_params.get("request_token","")
        try:
            if not 1<=len(token)<=512:raise ValueError("Authentication did not return a valid request token")
            kite.authenticate(token)
        except Exception:
            if login["destination"]!="/#setup":raise ValueError("Broker sign-in failed; begin login again") from None
            setup.authentication_error="Zerodha sign-in did not complete. Check your app key, secret and registered redirect URL, then try again."
            response=RedirectResponse("/#setup",status_code=303)
            response.delete_cookie("artha_oauth")
            return response
        setup.reset()
        response=RedirectResponse(login["destination"],status_code=303)
        response.delete_cookie("artha_oauth")
        return response

    app.mount("/static",StaticFiles(directory=ROOT/"static"),name="static")
    return app
