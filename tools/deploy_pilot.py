"""Backed-up deployment of 0.7.0 with real broker transport required OFF."""
import json,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import build_opener,ProxyHandler
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.research.jobs import CURRENT_BUILD
from tools.archive_release import archive
from tools.forward_ops import snapshot,restore_check

def main():
    client=build_opener(ProxyHandler({}))
    def get(path):
        with client.open('http://127.0.0.1:8787'+path,timeout=20) as r:return json.load(r)
    def financial(s):return dict(cash=s['account']['cash'],capital=s['account']['capital'],positions=s['positions'],orders=s['orders'],source=s['source'])
    validation=json.loads((ROOT/'docs/phase-5-validation.json').read_text())
    if not validation['all_passed'] or validation['build']['code_hash']!=CURRENT_BUILD['code_hash'] or len(validation['checks'])!=15:raise ValueError('Final validation must cover this exact source build')
    environment=subprocess.run(['systemctl','show','artha.service','-p','Environment','--value'],check=True,capture_output=True,text=True).stdout
    if 'ARTHA_LIVE_TRANSPORT_ENABLED=1' in environment:raise ValueError('Refusing deployment with live-order transport enabled')
    before=get('/api/state');before_health=get('/api/health');archive()
    suffix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');dest=ROOT/'data/backups'/('pre-pilot-'+suffix)
    backup=snapshot(ROOT/'data',dest);restored=restore_check(dest)
    subprocess.run(['systemctl','restart','artha.service'],check=True)
    for _ in range(90):
        try:
            health=get('/api/health');version=get('/openapi.json')['info']['version']
            if version=='0.7.0':break
        except Exception:pass
        time.sleep(1)
    else:raise RuntimeError('Updated service did not become available')
    after=get('/api/state');pilot=get('/api/pilot/status');charts=get('/api/market/charts');forward=get('/api/forward/status')
    if pilot['transport_enabled'] or pilot['armed'] or pilot['orders']:raise ValueError('Pilot must deploy with no real-order transport, authority or orders')
    if health['status']!='ok' or not health['reconciliation']['ok'] or not pilot['audit']['ok']:raise ValueError('Post-deployment financial health failed')
    if financial(before)!=financial(after):raise ValueError('Original paper financial state changed')
    if forward['build']['code_hash']!=CURRENT_BUILD['code_hash']:raise ValueError('Deployed source identity differs')
    post=ROOT/'data/backups'/('post-pilot-'+suffix);post_backup=snapshot(ROOT/'data',post);post_restore=restore_check(post)
    result=dict(deployed_at=datetime.now(timezone.utc).isoformat(),version=version,build=CURRENT_BUILD,source_archive=str(ROOT/'data/releases'/CURRENT_BUILD['code_hash']),pre_backup=str(dest),pre_databases=len(backup['files']),pre_restore_ok=restored['ok'],post_backup=str(post),post_databases=len(post_backup['files']),post_restore_ok=post_restore['ok'],paper_financial_state_preserved=True,candidate_registry_preserved=before_health['candidate_registry']['head']==health['candidate_registry']['head'],health=health,pilot_transport_enabled=False,pilot_armed=False,pilot_order_count=0,chart_cache_available=True,authenticated=pilot['authenticated'],phase5_qualified=False,real_orders_sent=False)
    (ROOT/'docs/phase-5-deployment.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)

if __name__=='__main__':main()
