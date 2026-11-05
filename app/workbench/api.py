import asyncio,json
from typing import Annotated,Literal
from fastapi import Request
from fastapi.responses import StreamingResponse
from fastapi import APIRouter
from pydantic import Field
from .models import Watchlist,ChainRequest,Analysis,SaveDraft,Backtest,PaperBasket,TemplateApply,Screener,NewWatchlist,QuoteConnection,Key
from .analytics import analyze

def router(service,forward,pilot=None):
    from .studio import Studio
    from .models import StudioBasket,StrikeAdjust,MarginQuote,PositionImport,ChainSeries
    studio=Studio(service,pilot)
    api=APIRouter(prefix='/api/workspace',tags=['Trading desk and strategy builder'])
    @api.get('/meta')
    def meta():return service.meta()
    @api.get('/contract-grid')
    def contract_grid(underlying:Annotated[str,Field(pattern=r'^[A-Z0-9 &_.\-]{1,40}$')],expiry:Annotated[str,Field(pattern=r'^\d{4}-\d{2}-\d{2}$')],kind:Literal['CE','PE']):return service.contract_grid(underlying,expiry,kind)
    @api.get('/watchlist')
    def watchlist(watchlist_id:Annotated[str,Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')]|None=None):return service.watchlist(watchlist_id)
    @api.post('/watchlist')
    def save_watchlist(body:Watchlist):return service.save_watchlist(body)
    @api.post('/quotes/refresh')
    def quotes():return service.refresh_quotes()
    @api.get('/watchlists')
    def watchlists():return service.watchlists()
    @api.post('/watchlists')
    def new_watchlist(body:NewWatchlist):return service.create_watchlist(body)
    @api.post('/watchlists/{identifier}/save')
    def update_watchlist(identifier:Annotated[str,Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')],body:Watchlist):return service.save_watchlist(body,identifier)
    @api.post('/watchlists/{identifier}/activate')
    def activate_watchlist(identifier:Annotated[str,Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')]):return service.activate_watchlist(identifier)
    @api.post('/watchlists/{identifier}/delete')
    def delete_watchlist(identifier:Annotated[str,Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')]):return service.delete_watchlist(identifier)
    @api.post('/quotes/connect')
    def connect_quotes(body:QuoteConnection):return service.connect_quotes(body.selected)
    @api.get('/quotes/stream')
    async def quote_stream(request:Request,watchlist_id:Annotated[str,Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')],selected:Key|None=None,interval:Literal['day','5minute','15minute','60minute']='day'):
        service.watchlist(watchlist_id)
        async def events():
            while not await request.is_disconnected():
                try:payload=service.watchlist(watchlist_id,selected,interval)
                except ValueError:
                    yield 'event: watchlist_missing\ndata: {}\n\n';return
                yield 'event: quotes\ndata: '+json.dumps(payload,allow_nan=False,separators=(',',':'))+'\n\n'
                await asyncio.sleep(.25)
        return StreamingResponse(events(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no','Cache-Control':'no-store'})
    @api.get('/chain')
    def chain(underlying:Annotated[str,Field(pattern=r'^[A-Z0-9 &_.\-]{1,40}$')]='NIFTY',expiry:Annotated[str,Field(pattern=r'^\d{4}-\d{2}-\d{2}$')]|None=None):return service.chain(underlying,expiry)
    @api.post('/chain/refresh')
    def refresh(body:ChainRequest):return service.refresh_chain(body)
    @api.get('/chain/history')
    def history(underlying:Annotated[str,Field(pattern=r'^[A-Z0-9 &_.\-]{1,40}$')],expiry:Annotated[str,Field(pattern=r'^\d{4}-\d{2}-\d{2}$')]):return service.chain_history(underlying,expiry)
    @api.post('/template')
    def template(body:TemplateApply):return service.build_template(body)
    @api.post('/screener')
    def screen(body:Screener):return service.screen(body)
    @api.post('/analyze')
    def analysis(body:Analysis):return analyze(body,service.clock())
    @api.get('/drafts')
    def drafts():return service.drafts()
    @api.post('/drafts')
    def save(body:SaveDraft):return service.save(body)
    @api.get('/drafts/{identifier}')
    def draft(identifier:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]):return service.draft(identifier)
    @api.post('/paper-basket')
    def paper(body:PaperBasket):return service.paper_basket(body,forward)
    @api.post('/backtest')
    def backtest(body:Backtest):return service.backtest(body)
    @api.post('/studio/validate')
    def validate_studio(body:StudioBasket):return dict(basket=body.basket.model_dump(mode='json'),execution_authority=False)
    @api.post('/studio/adjust')
    def adjust(body:StrikeAdjust):return studio.adjust(body)
    @api.post('/studio/mark')
    def mark(body:StudioBasket):return studio.mark(body)
    @api.post('/studio/history')
    def marks(body:StudioBasket):return studio.history(body)
    @api.post('/studio/chain-series')
    def chain_series(body:ChainSeries):return studio.chain_series(body)
    @api.get('/studio/positions')
    def positions():return studio.candidates()
    @api.post('/studio/positions/import')
    def import_positions(body:PositionImport):return studio.import_positions(body)
    @api.post('/studio/margins')
    def margins(body:MarginQuote):return studio.margins(body)
    return api
