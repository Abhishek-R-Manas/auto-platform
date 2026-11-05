"""Recover only a display cache from a verified backup; execution state is never restored."""
import json,sqlite3
from pathlib import Path
from tools.forward_ops import restore_check,check_database
from app.research.catalog import digest,canonical


def read_snapshot(directory):
    directory=Path(directory).resolve();restore_check(directory)
    source=directory/'pilot/pilot.sqlite3'
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as db:
        settings={name:json.loads(value) for name,value in db.execute("SELECT name,document FROM settings WHERE name IN ('snapshot','portfolio_snapshot')")}
    snapshot=settings.get('portfolio_snapshot') or settings.get('snapshot')
    if snapshot is None:raise ValueError('No broker display snapshot exists in the verified backup')
    return snapshot


def restore_display(directory,target):
    snapshot=read_snapshot(directory);target=Path(target).resolve();check_database(target)
    with sqlite3.connect(target,timeout=15) as db:
        db.execute('BEGIN IMMEDIATE')
        before=db.execute("SELECT document FROM settings WHERE name='snapshot'").fetchone()
        event=db.execute("SELECT document FROM events WHERE json_extract(document,'$.kind')='RECONCILIATION' ORDER BY sequence DESC LIMIT 1").fetchone()
        if not event or json.loads(event[0])['details']['hash']!=digest(snapshot):raise ValueError('Backup snapshot is not the current audited reconciliation; refusing stale recovery')
        old=db.execute("SELECT document FROM settings WHERE name='portfolio_snapshot'").fetchone()
        if old and json.loads(old[0]) is not None and digest(json.loads(old[0]))!=digest(snapshot):raise ValueError('A different display cache already exists; refusing overwrite')
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('portfolio_snapshot',canonical(snapshot)))
        assert db.execute("SELECT document FROM settings WHERE name='snapshot'").fetchone()==before
    check_database(target)
    return dict(restored=True,holdings_count=len(snapshot['holdings']),position_rows=len(snapshot['positions']['net']),execution_snapshot_restored=False,arming_changed=False)
