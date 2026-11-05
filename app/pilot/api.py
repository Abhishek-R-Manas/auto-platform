from typing import Annotated
from fastapi import APIRouter
from pydantic import Field
from .service import Mandate,ManualMandate,Intention,Submit,Arm

def router(service):
    api=APIRouter(prefix='/api/pilot',tags=['Supervised real-money pilot'])
    @api.get('/status')
    def status():return service.status()
    @api.post('/mandate')
    def mandate(body:Mandate):return service.configure(body)
    @api.post('/manual/review')
    def manual_review(body:dict):return service.review_manual(body)
    @api.post('/manual/mandate')
    def manual_mandate(body:ManualMandate):return service.configure_manual(body)
    @api.post('/reconcile')
    def reconcile():return service.reconcile()
    @api.post('/arm')
    def arm(body:Arm):return service.arm(body)
    @api.post('/protection/restore')
    def protection():return service.restore_protection()
    @api.post('/pause')
    def pause():return service.pause(cancel_entries=True)
    @api.post('/preview')
    def preview(body:Intention):return service.preview(body)
    @api.post('/submit')
    def submit(body:Submit):return service.submit(body)
    @api.post('/orders/{identifier}/cancel')
    def cancel(identifier:Annotated[str,Field(pattern=r'^[a-f0-9]{32}$')]):return service.cancel(identifier)
    @api.get('/audit')
    def audit():return service.audit()
    return api
