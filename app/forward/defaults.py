"""Reviewed baseline, never an automatically approved trading policy."""
from datetime import datetime
from ..research.schemas import Strategy, Leg

CALENDAR_SOURCE='https://nsearchives.nseindia.com/content/circulars/FAOP71777.pdf'
HOLIDAYS_2026=['2026-01-15','2026-01-26','2026-03-03','2026-03-26','2026-03-31','2026-04-03','2026-04-14','2026-05-01','2026-05-28','2026-06-26','2026-09-14','2026-10-02','2026-10-20','2026-11-08','2026-11-10','2026-11-24','2026-12-25']

def defaults(at):
    strategy=Strategy(id='reference_trend',name='Unqualified trend reference',signal_instruments=['RELIANCE'],legs=[Leg(instrument_id='RELIANCE')]).model_dump(mode='json')
    return dict(request_id='',name='Forward paper experiment',origin='REFERENCE',capital=1000000,candidate_ids=[],strategies=[strategy],bindings={'RELIANCE':'NSE:RELIANCE'},interval_minutes=5,bar_source='KITE_CANDLES',
        calendar={'timezone':'Asia/Kolkata','holidays':HOLIDAYS_2026,'session':{'open':'09:15:00','close':'15:30:00'},'special_sessions':{},'settlement_days':1},
        calendar_valid_until='2026-12-31',calendar_source=CALENDAR_SOURCE,reviewer='',review_note='',data_rights_confirmed=False,assumptions_reviewed=False,
        notes=['This is an unqualified software/reference strategy, not an approved candidate or investment recommendation','Calendar baseline expires at end-2026; review current exchange circulars before activation','Special sessions, including Muhurat trading, are closed unless their actual timing is explicitly added','January 15 additional equity closure follows the current NSE holiday page; check segment-specific revisions'])
