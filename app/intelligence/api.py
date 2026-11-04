"""Same-origin external input setup and immutable evidence views."""
from typing import Annotated
from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import Field
from .schemas import FeedPolicy,ImportReport,Refresh,Enrich

def router(service,catalog):
    api=APIRouter(prefix='/api/intelligence',tags=['Dated research inputs'])
    @api.get('/status')
    def status():return service.status()
    @api.post('/policy')
    def policy(body:FeedPolicy):return service.set_policy(body)
    @api.post('/import')
    def ingest(body:ImportReport):
        result=service.ingest(body)
        return {key:value for key,value in result.items() if key not in {'content','observations'}}
    @api.post('/refresh')
    def refresh(body:Refresh):return service.refresh(body.source,body.request_id)
    @api.get('/jobs')
    def jobs():return service.jobs()
    @api.get('/artifacts')
    def artifacts():return service.artifacts()
    @api.get('/artifacts/{identifier}')
    def artifact(identifier:str):return service.artifact(identifier)
    @api.get('/observations')
    def observations(limit:Annotated[int,Field(ge=1,le=500)]=200):return service.observations(limit)
    @api.get('/surfaces')
    def surfaces():return service.surfaces()
    @api.get('/audit')
    def audit():
        result=service.audit();service.audit_result=result;return result
    @api.post('/datasets/enrich')
    def enrich(body:Enrich):return service.enrich(body.dataset_id,body.features,catalog)
    @api.get('/templates/{kind}')
    def template(kind:str):
        if kind=='fii':content='Category,Date,Buy Value (Rs. Crores),Sell Value (Rs. Crores),Net Value (Rs. Crores)\n'
        elif kind=='macro':content='series,period,value,unit,base_year,published_at,status\n'
        else:raise ValueError('Unknown report template')
        return Response(content,media_type='text/csv',headers={'Content-Disposition':'attachment; filename="'+kind+'-template.csv"'})
    return api
