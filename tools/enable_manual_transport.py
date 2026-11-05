"""Owner-requested transport deployment. Never configures limits, arms, authenticates or sends an order."""
import json,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import build_opener,ProxyHandler
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.workspace_fingerprint import fingerprints
from tools.forward_ops import snapshot,restore_check
from tools.archive_release import archive

def main():
    if '--owner-requested' not in sys.argv:raise ValueError('Use only after the owner explicitly requests real-money trading')
    client=build_opener(ProxyHandler({}))
    def get(path):
        with client.open('http://127.0.0.1:8787'+path,timeout=20) as response:return json.load(response)
    validation=json.loads((ROOT/'docs/workspace-validation.json').read_text());deployed=json.loads((ROOT/'docs/workspace-deployment.json').read_text())
    identity=fingerprints()['sha256']
    if not validation['all_passed'] or identity!=validation['identity']['sha256'] or identity!=deployed['asset_identity']:raise ValueError('Exact source must be fully validated and deployed before enabling the connection')
    if get('/openapi.json')['info']['version']!='0.18.0':raise ValueError('Expected manual-trading release is not running')
    before=get('/api/pilot/status');state=get('/api/state');health=get('/api/health');watch=get('/api/workspace/watchlist')
    if before['armed'] or before['orders'] or before['mandate'] or not before['audit']['ok'] or health['status']!='ok':raise ValueError('Initial connection enablement requires a healthy, empty and inactive broker journal')
    archive();suffix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');backup=ROOT/'data/backups'/('pre-live-connection-'+suffix);snapshot(ROOT/'data',backup);assert restore_check(backup)['ok']
    target=Path('/etc/systemd/system/artha.service.d/20-live-transport.conf');content=(ROOT/'deploy/artha-live-transport.conf').read_text()
    old=target.read_text() if target.exists() else None
    if old is not None and old!=content:raise ValueError('Unrecognized existing live transport configuration; refusing to replace it')
    changed=old!=content
    try:
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content);target.chmod(0o644)
        subprocess.run(['systemctl','daemon-reload'],check=True);subprocess.run(['systemctl','restart','artha.service'],check=True)
        for _ in range(60):
            try:
                after=get('/api/pilot/status');new_health=get('/api/health')
                if after['transport_enabled'] and new_health['status']=='ok':break
            except OSError:pass
            time.sleep(1)
        else:raise RuntimeError('Enabled connection did not become healthy')
        new_state=get('/api/state');new_watch=get('/api/workspace/watchlist')
        if after['armed'] or after['orders'] or after['mandate'] or not after['audit']['ok']:raise ValueError('Account execution state must remain inactive')
        if before['broker_portfolio']!=after['broker_portfolio']:raise ValueError('Cached portfolio changed')
        if any(state[k]!=new_state[k] for k in ('account','orders','positions')):raise ValueError('Paper account changed')
        if any(watch[k]!=new_watch[k] for k in ('name','keys')):raise ValueError('Watchlist changed')
        if health['candidate_registry']['head']!=new_health['candidate_registry']['head']:raise ValueError('Strategy registry changed')
        post=ROOT/'data/backups'/('post-live-connection-'+suffix);snapshot(ROOT/'data',post);assert restore_check(post)['ok']
        record=dict(checked_at=datetime.now(timezone.utc).isoformat(),version='0.18.0',identity=identity,transport_enabled=True,account_armed=False,mandate_configured=False,authenticated=after['authenticated'],owner_limits_required=True,broker_static_ip_and_delivery_confirmation_required=True,paper_state_preserved=True,watchlist_preserved=True,cached_portfolio_preserved=True,audit_ok=True,pre_backup_restore_ok=True,post_backup_restore_ok=True,real_orders_sent=False,genuine_order_acceptance_tested=False,phase5_strategy_qualified=False)
        (ROOT/'docs/manual-trading-activation.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record))
    except BaseException:
        if changed:
            if old is None:target.unlink(missing_ok=True)
            else:target.write_text(old)
            subprocess.run(['systemctl','daemon-reload'],check=True);subprocess.run(['systemctl','restart','artha.service'],check=True)
        raise
if __name__=='__main__':main()
