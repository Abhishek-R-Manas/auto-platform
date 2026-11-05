"""Backed-up deployment of 0.10.1 with real broker transport required OFF."""
import argparse,json,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import build_opener,ProxyHandler
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.research.jobs import CURRENT_BUILD
from tools.archive_release import archive
from tools.forward_ops import snapshot,restore_check

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--recover-portfolio-from',type=Path);args=parser.parse_args()
    client=build_opener(ProxyHandler({}))
    def get(path):
        with client.open('http://127.0.0.1:8787'+path,timeout=20) as r:return json.load(r)
    def financial(s):return dict(cash=s['account']['cash'],capital=s['account']['capital'],positions=s['positions'],orders=s['orders'],source=s['source'])
    validation=json.loads((ROOT/'docs/workspace-validation.json').read_text())
    if not validation['all_passed'] or validation['build']['code_hash']!=CURRENT_BUILD['code_hash'] or validation['identity']!=__import__('tools.workspace_fingerprint',fromlist=['fingerprints']).fingerprints():raise ValueError('Final validation must cover this exact source build')
    environment=subprocess.run(['systemctl','show','artha.service','-p','Environment','--value'],check=True,capture_output=True,text=True).stdout
    if 'ARTHA_LIVE_TRANSPORT_ENABLED=1' in environment:raise ValueError('Refusing deployment with live-order transport enabled')
    before=get('/api/state');before_health=get('/api/health');before_watch=get('/api/workspace/watchlist');before_pilot=get('/api/pilot/status');archive()
    suffix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');dest=ROOT/'data/backups'/('pre-workspace-'+suffix)
    backup=snapshot(ROOT/'data',dest);restored=restore_check(dest)
    expected_portfolio=before_pilot.get('broker_portfolio');recovery=None
    if args.recover_portfolio_from:
        from tools.recover_portfolio_display import read_snapshot,restore_display
        from app.portfolio import broker_rows
        if before_pilot['transport_enabled'] or before_pilot['armed'] or before_pilot['orders']:raise ValueError('Display recovery requires the disabled, empty broker pilot')
        expected_portfolio=broker_rows(read_snapshot(args.recover_portfolio_from))
        subprocess.run(['systemctl','stop','artha.service'],check=True)
        try:recovery=restore_display(args.recover_portfolio_from,ROOT/'data/pilot/pilot.sqlite3')
        finally:subprocess.run(['systemctl','start','artha.service'],check=True)
    else:subprocess.run(['systemctl','restart','artha.service'],check=True)
    for _ in range(90):
        try:
            health=get('/api/health');version=get('/openapi.json')['info']['version']
            if version=='0.10.1':break
        except Exception:pass
        time.sleep(1)
    else:raise RuntimeError('Updated service did not become available')
    after=get('/api/state');pilot=get('/api/pilot/status');charts=get('/api/market/charts');forward=get('/api/forward/status')
    if expected_portfolio!=pilot.get('broker_portfolio'):raise ValueError('Cached broker holdings or positions changed during deployment')
    if pilot['transport_enabled'] or pilot['armed'] or pilot['orders']:raise ValueError('Pilot must deploy with no real-order transport, authority or orders')
    if health['status']!='ok' or not health['reconciliation']['ok'] or not pilot['audit']['ok']:raise ValueError('Post-deployment financial health failed')
    if financial(before)!=financial(after):raise ValueError('Original paper financial state changed')
    after_watch=get('/api/workspace/watchlist')
    if any(before_watch[k]!=after_watch[k] for k in ('name','keys')):raise ValueError('Existing watchlist changed during deployment')
    if forward['build']['code_hash']!=CURRENT_BUILD['code_hash']:raise ValueError('Deployed source identity differs')
    if before_health['candidate_registry']['head']!=health['candidate_registry']['head']:raise ValueError('Candidate registry changed during deployment')
    post=ROOT/'data/backups'/('post-workspace-'+suffix);post_backup=snapshot(ROOT/'data',post);post_restore=restore_check(post)
    result=dict(deployed_at=datetime.now(timezone.utc).isoformat(),version=version,build=CURRENT_BUILD,asset_identity=validation['identity']['sha256'],source_archive=str(ROOT/'data/releases'/CURRENT_BUILD['code_hash']),pre_backup=str(dest),pre_databases=len(backup['files']),pre_restore_ok=restored['ok'],post_backup=str(post),post_databases=len(post_backup['files']),post_restore_ok=post_restore['ok'],paper_financial_state_preserved=True,watchlist_preserved=True,cached_broker_portfolio_preserved=True,display_recovery={k:v for k,v in recovery.items() if k not in {'holdings_count','position_rows'}} if recovery else None,candidate_registry_preserved=before_health['candidate_registry']['head']==health['candidate_registry']['head'],health=health,pilot_transport_enabled=False,pilot_armed=False,pilot_order_count=0,chart_cache_available=True,authenticated=pilot['authenticated'],phase5_qualified=False,real_orders_sent=False)
    (ROOT/'docs/workspace-deployment.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)

if __name__=='__main__':main()
