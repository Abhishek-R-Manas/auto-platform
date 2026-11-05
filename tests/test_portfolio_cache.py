"""Display-cache persistence and bounded recovery must never restore execution authority."""
import json,sqlite3
import pytest
from app.pilot.service import Pilot
from app.research.catalog import digest
from tools.forward_ops import snapshot,check_database
from tools.recover_portfolio_display import restore_display
from test_pilot_charts import Kite,Forward,Broker,AT
from test_portfolio_view import snapshot as broker_fixture


def make(path):
    kite=Kite();broker=Broker();broker.enabled=False;broker.data=broker_fixture();service=Pilot(path,kite,Forward(),broker,lambda:AT)
    return service,kite,broker


def test_restart_preserves_display_but_invalidates_execution_reconciliation(tmp_path):
    p,k,b=make(tmp_path);p.reconcile();saved=p.status()['broker_portfolio'];p.close()
    restarted=Pilot(tmp_path,k,Forward(),b,lambda:AT)
    try:
        assert restarted.status()['broker_portfolio']==saved and restarted.status()['portfolio_view']['holdings_summary']['count']==3
        assert restarted._get('snapshot') is None and restarted.session is None and not restarted.status()['armed'] and restarted.audit()['ok']
        with pytest.raises(ValueError,match='Reconcile'):restarted._snapshot({'account_id':'CONTROLLED123'})
        assert not b.sent and not b.cancelled
    finally:restarted.close()


def test_legacy_execution_snapshot_migrates_only_to_display_on_restart(tmp_path):
    p,k,b=make(tmp_path);p.reconcile();saved=digest(p._get('snapshot'))
    with p.transaction():p.db.execute("DELETE FROM settings WHERE name='portfolio_snapshot'")
    p.close();restarted=Pilot(tmp_path,k,Forward(),b,lambda:AT)
    try:assert digest(restarted._get('portfolio_snapshot'))==saved and restarted._get('snapshot') is None and restarted.audit()['ok']
    finally:restarted.close()


def test_tampered_display_is_hidden_and_restore_check_rejects_it(tmp_path):
    p,_,_=make(tmp_path);p.reconcile()
    with p.transaction():
        value=p._get('portfolio_snapshot');value['holdings'][0]['quantity']=999;p._set('portfolio_snapshot',value)
    assert not p.audit()['ok'] and p.status()['portfolio_view'] is None
    with pytest.raises(ValueError,match='display snapshot'):check_database(tmp_path/'pilot.sqlite3')
    p.close()


def test_recovery_restores_only_display_and_matches_audited_backup(tmp_path):
    root=tmp_path/'data';directory=root/'pilot';p,k,b=make(directory);p.reconcile();saved=p._get('snapshot');p.close();backup=tmp_path/'verified-backup';snapshot(root,backup)
    target=directory/'pilot.sqlite3'
    with sqlite3.connect(target) as db:
        db.execute("DELETE FROM settings WHERE name='portfolio_snapshot'");db.execute("UPDATE settings SET document='null' WHERE name='snapshot'")
        before={name:value for name,value in db.execute('SELECT name,document FROM settings')}
    result=restore_display(backup,target);assert result['restored'] and not result['execution_snapshot_restored']
    with sqlite3.connect(target) as db:
        after={name:value for name,value in db.execute('SELECT name,document FROM settings')};assert {k:v for k,v in after.items() if k!='portfolio_snapshot'}==before
        assert digest(json.loads(after['portfolio_snapshot']))==digest(saved)
    restarted=Pilot(directory,k,Forward(),b,lambda:AT)
    try:assert restarted.status()['portfolio_view']['holdings_summary']['count']==3 and restarted._get('snapshot') is None and not restarted.status()['armed']
    finally:restarted.close()


def test_recovery_cannot_replace_a_newer_reconciliation(tmp_path):
    root=tmp_path/'data';directory=root/'pilot';p,_,b=make(directory);p.reconcile();backup=tmp_path/'older-backup';snapshot(root,backup)
    b.data['holdings'][0]['last_price']=1501;p.reconcile();p.close()
    with pytest.raises(ValueError,match='current audited reconciliation'):restore_display(backup,directory/'pilot.sqlite3')
