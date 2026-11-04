"""Install the reviewed Phase 4 single-host service, preserving account evidence and backups."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.archive_release import archive
from tools.forward_ops import snapshot,restore_check
from app.research.jobs import CURRENT_BUILD


def fetch(path):
    opener=build_opener(ProxyHandler({}))
    with opener.open('http://127.0.0.1:8787'+path,timeout=15) as response:return json.load(response)


def connection_status():
    setup=fetch('/api/onboarding/status')
    return dict(kite_keys_still_configured=bool(setup['credentials']['api_key_set'] and setup['credentials']['api_secret_set']),kite_session_after_restart=setup['session']['authenticated'])


def account(state):
    return {key:state[key] for key in ('mode','source','account','positions','orders','live_trading_available')}


def run(*args):return subprocess.run(args,check=True,capture_output=True,text=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--expected-pid',type=int,required=True);args=parser.parse_args()
    if ROOT!=Path('/root/amanas/temp/auto-platform') or os.geteuid()!=0:raise SystemExit('Run as the authorized owner in the reviewed workspace')
    pid=args.expected_pid
    if pid<2:raise SystemExit('Invalid expected PID')
    process=Path('/proc')/str(pid)
    command=(process/'cmdline').read_bytes().split(b'\x00')
    if (process/'cwd').resolve()!=ROOT or not any(value.endswith(b'run.py') for value in command):raise SystemExit('Existing process identity differs; refusing stop')
    os.umask(0o077)
    before=fetch('/api/state');health=fetch('/api/health');assert health['status']=='ok' and before['paused'] and not before['positions'] and not before['orders'] and before['reconcile']['ok']
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    archive()
    # Validate the complete online snapshot before any process/service change.
    saved=ROOT/'data/backups'/('pre-phase4-'+stamp)
    manifest=snapshot(ROOT/'data',saved);restore=restore_check(saved)
    print(json.dumps(dict(stage='backup_verified',directory=str(saved),databases=len(manifest['files']),artifacts=len(manifest['artifacts']))),flush=True)
    units=('artha.service','artha-backup.service','artha-backup.timer')
    run('systemd-analyze','verify',*[str(ROOT/'deploy'/name) for name in units])
    for name in units:
        target=Path('/etc/systemd/system')/name
        if target.exists() and target.read_bytes()!=(ROOT/'deploy'/name).read_bytes():shutil.copy2(target,saved/('previous-'+name))
        shutil.copyfile(ROOT/'deploy'/name,target);target.chmod(0o644)
    run('systemctl','daemon-reload')
    os.kill(pid,signal.SIGTERM)
    deadline=time.monotonic()+100
    while (process/'cmdline').exists() and time.monotonic()<deadline:
        if not (process/'cmdline').read_bytes():break
        time.sleep(.25)
    if (process/'cmdline').exists() and (process/'cmdline').read_bytes():raise SystemExit('Old process did not stop; no force kill attempted')
    # Paused source state is preserved; the first new process runs under the service manager.
    run('systemctl','enable','--now','artha.service')
    run('systemctl','enable','--now','artha-backup.timer')
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        try:
            health=fetch('/api/health')
            if health['status']=='ok' and health['forward']['worker']['healthy']:break
        except OSError:pass
        time.sleep(.5)
    else:raise SystemExit('Managed application did not become healthy; inspect service status')
    after=fetch('/api/state');forward=fetch('/api/forward/status');setup=connection_status()
    assert account(before)==account(after) and after['paused'] and after['reconcile']['ok'] and not health['live_order_access']
    unit=run('systemctl','show','artha.service','--property=ActiveState,SubState,MainPID,WatchdogUSec,WatchdogTimestamp,Restart,NRestarts').stdout
    timer=run('systemctl','show','artha-backup.timer','--property=ActiveState,NextElapseUSecRealtime').stdout
    result=dict(deployed_at=datetime.now(timezone.utc).isoformat(),url='http://127.0.0.1:8787/#forward',application_version=fetch('/openapi.json')['info']['version'],build=CURRENT_BUILD,previous_pid=pid,source_archive=str(ROOT/'data/releases'/CURRENT_BUILD['code_hash']),backup=str(saved),backup_databases=len(manifest['files']),backup_artifacts=len(manifest['artifacts']),isolated_restore=restore,service=unit,timer=timer,health=health,forward=forward,main_account_preserved=True,main_account=account(after),entries_paused=after['paused'],**setup,real_orders_enabled=False,genuine_ai_monitor_validated=False,genuine_telegram_validated=False,public_multiuser_deployment=False)
    (ROOT/'docs/phase-4-deployment.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(stage='deployed',url=result['url'],health=health['status'],main_account_preserved=True,kite_session_requires_signin=not setup['kite_session_after_restart'],service=unit,timer=timer)),flush=True)

if __name__=='__main__':main()
