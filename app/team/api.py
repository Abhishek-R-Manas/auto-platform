"""Authenticated organization API; standalone deployments expose no owner/broker routes."""
from contextlib import asynccontextmanager
from datetime import datetime
import json
from pathlib import Path
from typing import Annotated,Literal
from urllib.parse import urlsplit
from fastapi import APIRouter,Depends,FastAPI,HTTPException,Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field,SecretStr,StrictBool,StrictInt,field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from ..research.schemas import Strict,ImportData,aware
from ..contracts import Save,Hash
from ..contract_research import Freeze,Start,Capture,PacketInput,TrialControl,verify_packet
from ..research.catalog import canonical
from .service import Team
from .auth import SESSION_SECONDS,check_auth_database,secret_hash

ROOT=Path(__file__).resolve().parents[2]
Email=Annotated[str,Field(pattern=r"^[^@\s]{1,100}@[^@\s.]{1,100}(?:\.[^@\s.]{1,60})+$",max_length=254)]
Roles=Annotated[list[Literal["ADMIN","RESEARCHER","REVIEWER"]],Field(min_length=1,max_length=3)]
Password=Annotated[SecretStr,Field(min_length=15,max_length=256)]
Key=Annotated[SecretStr,Field(min_length=24,max_length=256)]
RequestID=Annotated[str,Field(min_length=8,max_length=100)]
Text=Annotated[str,Field(min_length=1,max_length=4000)]

class Setup(Strict):
    bootstrap_key:Key
    organization_name:Annotated[str,Field(min_length=1,max_length=120)]
    email:Email
    password:Password

class Login(Strict):
    email:Email
    password:Annotated[SecretStr,Field(min_length=1,max_length=256)]

class Invite(Strict):
    email:Email
    name:Annotated[str,Field(min_length=1,max_length=120)]
    roles:Roles

class Accept(Strict):
    token:Key
    password:Password

class Membership(Strict):
    roles:Roles
    active:StrictBool

class ChangePassword(Strict):
    current_password:Annotated[SecretStr,Field(min_length=1,max_length=256)]
    new_password:Password

class Example(Strict):
    request_id:RequestID

class TeamStart(Start):
    grant_id:Hash|None=None

class Review(Strict):
    request_id:RequestID
    packet_id:Hash
    decision:Literal["ACCEPT","REQUEST_CHANGES","REJECT"]
    comment:Text

class Comment(Strict):
    request_id:RequestID
    packet_id:Hash
    comment:Text

class Grant(Strict):
    request_id:RequestID
    packet_id:Hash
    review_id:Hash
    expires_at:datetime
    max_new_trials:Annotated[StrictInt,Field(ge=1,le=20)]
    scenarios:Annotated[list[Literal["BASELINE","REPRODUCE","COST_STRESS"]],Field(min_length=1,max_length=3)]
    acknowledge_unqualified_research:StrictBool=False
    _aware=field_validator("expires_at")(aware)

class Revoke(Strict):
    request_id:RequestID
    grant_id:Hash
    comment:Text

def router(team):
    api=APIRouter(prefix="/api/team",tags=["Authenticated organization research"])
    identity=team.identity
    def actor(request:Request):
        return identity.authenticate(request.cookies.get(identity.cookie),request.headers.get("x-artha-csrf"),request.method not in {"GET","HEAD"})["user"]
    def public_attempt(request):
        with identity.lock,identity.db:identity._throttle(secret_hash("public:"+str(request.client.host if request.client else "unknown")),30)

    @api.get("/session")
    def session(request:Request):
        result=dict(configured=bool(identity.organization()),authenticated=False,live_orders_enabled=False)
        if request.cookies.get(identity.cookie):
            try:result.update(identity.authenticate(request.cookies.get(identity.cookie)),authenticated=True)
            except HTTPException:pass
        return result

    @api.post("/bootstrap")
    def bootstrap(body:Setup,request:Request):
        public_attempt(request)
        return identity.bootstrap(body.bootstrap_key.get_secret_value(),body.organization_name,body.email,body.password.get_secret_value())

    @api.post("/login")
    def login(body:Login,request:Request):
        token,csrf,user=identity.login(body.email,body.password.get_secret_value(),request.client.host if request.client else "unknown")
        secure=request.url.scheme=="https"
        response=JSONResponse(dict(user=user,csrf=csrf,organization=identity.organization()))
        response.set_cookie(identity.cookie,token,httponly=True,secure=secure,samesite="strict",max_age=SESSION_SECONDS,path="/api/team")
        return response

    @api.post("/logout")
    def logout(request:Request,user=Depends(actor)):
        identity.logout(request.cookies.get(identity.cookie),user)
        response=JSONResponse(dict(signed_out=True));response.delete_cookie(identity.cookie,path="/api/team")
        return response

    @api.post("/invitations/accept")
    def accept(body:Accept,request:Request):
        public_attempt(request)
        return identity.accept(body.token.get_secret_value(),body.password.get_secret_value())

    @api.get("/members")
    def members(user=Depends(actor)):
        return identity.members(user)

    @api.post("/members")
    def invite(body:Invite,user=Depends(actor)):
        return identity.invite(user,body.email,body.name,body.roles)

    @api.post("/members/{identifier}/invitation")
    def renew_invite(identifier:str,user=Depends(actor)):
        return identity.renew_invite(user,identifier)

    @api.post("/members/{identifier}")
    def change_member(identifier:str,body:Membership,user=Depends(actor)):
        return identity.change_member(user,identifier,body.roles,body.active)

    @api.post("/password")
    def password(body:ChangePassword,user=Depends(actor)):
        identity.change_password(user,body.current_password.get_secret_value(),body.new_password.get_secret_value())
        return dict(signed_out=True)

    @api.get("/backups")
    def backups(user=Depends(actor)):
        from .recovery import list_snapshots
        with team.lock:
            identity.require(user,"ADMIN")
            receipts=sorted((team.root/"backups/receipts").glob("team-*.json"),reverse=True)
            latest=json.loads(receipts[0].read_text()) if receipts else None
            return dict(snapshots=list_snapshots(team.root),last_offhost_delivery=latest,
                note="Encrypted SSH publication is configured by the local operator; this API never chooses an external destination")

    @api.post("/backups")
    def backup(user=Depends(actor)):
        from .recovery import snapshot
        with team.lock:
            identity.require(user,"ADMIN")
            return snapshot(team)

    @api.get("/dashboard")
    def dashboard(user=Depends(actor)):
        return team.dashboard(user)

    @api.post("/example")
    def example(body:Example,user=Depends(actor)):
        return team.example(user,body.request_id)

    @api.post("/datasets")
    def dataset(body:ImportData,user=Depends(actor)):
        return team.import_data(user,body)

    @api.post("/contracts")
    def save(body:Save,user=Depends(actor)):
        return team.save(user,body.model_dump(mode="json"))

    @api.post("/protocols")
    def freeze(body:Freeze,user=Depends(actor)):
        return team.freeze(user,body.model_dump(mode="json"))

    @api.get("/protocols/{identifier}")
    def protocol(identifier:Hash,user=Depends(actor)):
        return team.detail(user,identifier)

    @api.post("/trials")
    def start(body:TeamStart,user=Depends(actor)):
        return team.start(user,body.model_dump(mode="json"))

    @api.post("/trials/{identifier}/control")
    def control(identifier:Hash,body:TrialControl,user=Depends(actor)):
        return team.control(user,identifier,body.action)

    @api.post("/trials/{identifier}/recover")
    def recover(identifier:Hash,user=Depends(actor)):
        return team.recover(user,identifier)

    @api.post("/packets")
    def capture(body:Capture,user=Depends(actor)):
        return team.capture(user,body.model_dump(mode="json"))

    @api.get("/packets/{identifier}")
    def packet(identifier:Hash,user=Depends(actor)):
        return team.export(user,identifier)

    @api.get("/packets/{identifier}/export")
    def export(identifier:Hash,include_trace:bool=False,rights_acknowledged:bool=False,user=Depends(actor)):
        packet=team.export(user,identifier,include_trace,rights_acknowledged)
        return Response(canonical(packet),media_type="application/json",headers={"Content-Disposition":"attachment; filename=team-evidence-"+identifier+".json"})

    @api.post("/verify")
    def verify(body:PacketInput,user=Depends(actor)):
        return verify_packet(body.packet,body.expected_protocol,body.expected_build,body.expected_dataset)

    @api.post("/reviews")
    def review(body:Review,user=Depends(actor)):
        return team.review(user,body.model_dump(mode="json"))

    @api.post("/comments")
    def comment(body:Comment,user=Depends(actor)):
        return team.comment(user,body.model_dump(mode="json"))

    @api.post("/scopes")
    def grant(body:Grant,user=Depends(actor)):
        return team.grant(user,body.model_dump(mode="json"))

    @api.post("/scopes/revoke")
    def revoke(body:Revoke,user=Depends(actor)):
        return team.revoke(user,body.model_dump(mode="json"))

    return api

def install(app,team):
    app.state.team=team
    app.include_router(router(team))
    app.mount("/team-assets",StaticFiles(directory=ROOT/"static/team"),name="team-assets")
    @app.get("/team",include_in_schema=False)
    def team_page():
        return FileResponse(ROOT/"static/team/index.html")
    @app.middleware("http")
    async def team_boundaries(request,call_next):
        if request.url.path.startswith("/api/team"):
            try:length=int(request.headers.get("content-length","0"))
            except ValueError:return JSONResponse(dict(detail="Invalid content length"),400)
            limit=34000000 if request.url.path=="/api/team/datasets" else 16*1024*1024 if request.url.path=="/api/team/verify" else 16384
            if length<0 or length>limit:return JSONResponse(dict(detail="Team request exceeds the size limit"),413)
            if request.url.hostname not in {"127.0.0.1","localhost"} and request.url.scheme!="https":
                return JSONResponse(dict(detail="HTTPS is required for team access"),403)
            if request.method not in {"GET","HEAD"}:
                origin=request.headers.get("origin")
                if request.headers.get("x-artha-client")!="paper-workspace" or request.headers.get("sec-fetch-site")=="cross-site":
                    return JSONResponse(dict(detail="Same-origin workspace request required"),403)
                if origin and urlsplit(origin).netloc!=request.headers.get("host"):return JSONResponse(dict(detail="Cross-origin mutation denied"),403)
                if not request.headers.get("content-type","").startswith("application/json"):return JSONResponse(dict(detail="JSON required"),415)
                if "content-length" not in request.headers:return JSONResponse(dict(detail="Content length required"),411)
                if len(await request.body())>limit:return JSONResponse(dict(detail="Team request exceeds the size limit"),413)
        response=await call_next(request)
        if request.url.path=="/team" or request.url.path.startswith(("/api/team","/team-assets")):
            response.headers["Cache-Control"]="no-store"
            response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"
            response.headers["X-Content-Type-Options"]="nosniff";response.headers["Referrer-Policy"]="no-referrer"
        return response

def create_team_app(root,clock=None,hosts=None):
    team=Team(root,clock)
    @asynccontextmanager
    async def lifespan(app):
        yield
        team.close()
    app=FastAPI(title="Artha organization research",docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=hosts or ["127.0.0.1","localhost"])
    install(app,team)
    @app.get("/",include_in_schema=False)
    def home():return Response(status_code=307,headers={"Location":"/team"})
    @app.get("/api/health",include_in_schema=False)
    def health():
        with team.lock:
            check_auth_database(team.identity.db)
        return dict(status="ok",team_only=True,broker_routes_available=False)
    @app.exception_handler(RequestValidationError)
    async def invalid(request,exc):
        return JSONResponse(dict(detail="Check the team request fields; submitted values are not returned"),422)
    @app.exception_handler(ValueError)
    async def invalid_value(request,exc):
        return JSONResponse(dict(detail=str(exc)),400)
    return app
