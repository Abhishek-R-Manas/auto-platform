"""Record actual isolated acceptance outcomes for the 0.20.0 workspace."""
import json,os,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.research.jobs import CURRENT_BUILD
from tools.workspace_fingerprint import fingerprints

def main():
    env=dict(os.environ)
    for name in ('KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN','ARTHA_AI_API_KEY','ARTHA_AI_MODEL','OPENAI_API_KEY','ARTHA_TELEGRAM_BOT_TOKEN','ARTHA_TELEGRAM_CHAT_ID','ARTHA_LIVE_TRANSPORT_ENABLED'):env.pop(name,None)
    env['PYTHONDONTWRITEBYTECODE']='1';checks=[];identity=fingerprints()
    commands=[[sys.executable,'-B','-m','pytest','-q']]
    commands.extend([[sys.executable,'-B','tools/'+name] for name in ('workbench_browser.py','portfolio_browser.py','manual_trading_browser.py','trading_onboarding_browser.py','strategy_studio_browser.py','research_browser.py','onboarding_browser.py','forward_browser.py','candidates_browser.py','inputs_browser.py','contracts_browser.py','contract_research_browser.py','team_browser.py')])
    commands.append([sys.executable,'-B','-m','pip','check'])
    commands.extend([['node','--check',str(p.relative_to(ROOT))] for p in sorted((ROOT/'static').rglob('*.js'))])
    reuse=None
    if '--resume' in sys.argv:
        previous=json.loads((ROOT/'docs/workspace-validation.json').read_text());old=previous['identity']['files'];new=identity['files']
        changed={name for name in set(old)|set(new) if old.get(name)!=new.get(name)}
        if previous['build']!=CURRENT_BUILD or not changed<={'tools/validate_workbench.py','tools/workbench_browser.py','tools/portfolio_browser.py'}:raise ValueError('Resume only permits browser-harness changes; runtime, assets, unit tests and dependency locks must match the passed unit check')
        first=previous['checks'][0]
        if first['command']!=commands[0] or not first['passed']:raise ValueError('No compatible passed full unit check to reuse')
        reuse=dict(first,validated_at=previous['checked_at'])
    for command in commands:
        start=time.monotonic()
        if reuse and command==commands[0]:result=subprocess.CompletedProcess(command,0,stdout=reuse['output'],stderr='')
        else:result=subprocess.run(command,cwd=ROOT,env=env,text=True,capture_output=True)
        output=result.stdout+result.stderr
        entry=dict(command=command,passed=result.returncode==0,seconds=round(time.monotonic()-start,2),output=output);checks.append(entry)
        if reuse and command==commands[0]:entry['reused_passed_runtime_check_at']=reuse['validated_at']
        print(json.dumps(dict(command=command,passed=entry['passed'],seconds=entry['seconds'],summary=output[-2200:])),flush=True)
        record=dict(version='0.20.0',checked_at=datetime.now(timezone.utc).isoformat(),build=CURRENT_BUILD,identity=identity,checks=checks,all_passed=len(checks)==len(commands) and all(c['passed'] for c in checks),real_broker_orders_sent=False,genuine_provider_validation=False,live_transport_enabled=False,phase5_qualified=False)
        (ROOT/'docs/workspace-validation.json').write_text(json.dumps(record,indent=2)+'\n')
        if result.returncode:raise SystemExit(result.returncode)
    if identity!=fingerprints():raise ValueError('Source changed during final validation')
    print('All final workspace checks passed',flush=True)
if __name__=='__main__':main()
