"""Reviewed ordinary NSE equity sessions. Special sessions remain unsupported.
Sources checked 2026-11-05:
https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf
https://www.nseindia.com/resources/exchange-communication-holidays?apiversion=v2
Review is valid for seven days; subsequent sessions require a new calendar review.
"""
from datetime import date
from ..research.catalog import bounds
REVIEWED='2026-11-05'
VALID_UNTIL='2026-11-12'
HOLIDAYS=['2026-01-15','2026-01-26','2026-03-03','2026-03-26','2026-03-31','2026-04-03','2026-04-14','2026-05-01','2026-05-28','2026-06-26','2026-09-14','2026-10-02','2026-10-20','2026-11-10','2026-11-24','2026-12-25']
def manual_policy(now):
    day=now.date().isoformat()
    if not REVIEWED<=day<=VALID_UNTIL:raise ValueError('Manual exchange calendar needs a fresh server review')
    return dict(manifest={'calendar':dict(timezone='Asia/Kolkata',session={'open':'09:15','close':'15:30'},holidays=HOLIDAYS.copy(),special_sessions={},settlement_days=1)},config={'calendar_valid_until':VALID_UNTIL},reviewed_at=REVIEWED,special_sessions_supported=False)
def policy_status(now):
    try:
        p=manual_policy(now);span=bounds(p['manifest']['calendar'],now.date())
        from datetime import timedelta
        return dict(reviewed_at=REVIEWED,valid_until=VALID_UNTIL,valid=True,session_open=bool(span and span[0]<=now<span[1]-timedelta(minutes=10)),note='Ordinary NSE sessions only; entries stop at 15:20 IST. Special sessions are unavailable.')
    except ValueError:return dict(reviewed_at=REVIEWED,valid_until=VALID_UNTIL,valid=False,session_open=False,note='Exchange calendar requires review before manual order entry.')
