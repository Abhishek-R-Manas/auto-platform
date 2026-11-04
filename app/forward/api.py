"""Local same-origin forward paper controls. No live execution endpoints."""
from typing import Annotated
from fastapi import APIRouter
from pydantic import Field
from .schemas import Portfolio,Control,Acknowledge,RequestJob,Monitoring,AIConfig,GuardianReview
from .alerts import AlertConfig,AlertRetry

def router(service):
    api=APIRouter(prefix='/api/forward',tags=['Forward paper evaluation'])
    @api.get('/status')
    def status():return service.status()
    @api.get('/defaults')
    def defaults():
        from .defaults import defaults
        return defaults(service.now())
    @api.get('/portfolios')
    def portfolios():return service.portfolios()
    @api.post('/portfolios')
    def create(body:Portfolio):return service.create(body)
    @api.get('/portfolios/{identifier}')
    def portfolio(identifier:str):return service.portfolio(identifier)
    @api.post('/portfolios/{identifier}/control')
    def control(identifier:str,body:Control):return service.control(identifier,body)
    @api.get('/portfolios/{identifier}/events')
    def events(identifier:str,after:Annotated[int,Field(ge=0)]=0,limit:Annotated[int,Field(ge=1,le=500)]=100,kind:Annotated[str,Field(max_length=80)]|None=None):return service.events(identifier,after,limit,kind)
    @api.get('/portfolios/{identifier}/report')
    def report(identifier:str):return service.report(identifier)
    @api.get('/portfolios/{identifier}/export')
    def export(identifier:str):return service.export(identifier)
    @api.post('/portfolios/{identifier}/reviews')
    def review(identifier:str,body:GuardianReview):return service.review(identifier,body)
    @api.get('/portfolios/{identifier}/reviews')
    def reviews(identifier:str):return service.reviews(identifier)
    @api.get('/portfolios/{identifier}/audit')
    def audit(identifier:str):return service.audit(identifier)
    @api.post('/portfolios/{identifier}/warmup')
    def warmup(identifier:str,body:RequestJob):return service.warmup(identifier,body.request_id)
    @api.post('/portfolios/{identifier}/monitor')
    def monitor(identifier:str,body:RequestJob):return service.request_monitor(identifier,body.request_id)
    @api.post('/portfolios/{identifier}/drills')
    def drills(identifier:str,body:RequestJob):return service.run_drills(identifier,body.request_id)
    @api.get('/incidents')
    def incidents(portfolio_id:str|None=None):return service.incidents(portfolio_id)
    @api.post('/incidents/{identifier}/review')
    def acknowledge(identifier:str,body:Acknowledge):return service.acknowledge(identifier,body)
    @api.get('/jobs')
    def jobs(portfolio_id:str|None=None):return service.jobs(portfolio_id)
    @api.post('/jobs/{identifier}/cancel')
    def cancel(identifier:str):return service.cancel_job(identifier)
    @api.post('/monitoring')
    def monitoring(body:Monitoring):return service.set_monitoring(body)
    @api.post('/ai/configure')
    def configure(body:AIConfig):return service.configure_ai(body)
    @api.post('/notifications/configure')
    def notifications(body:AlertConfig):return service.configure_alerts(body)
    @api.post('/notifications/{identifier}/retry')
    def retry(identifier:str,body:AlertRetry):return service.retry_notification(identifier,body)
    return api
