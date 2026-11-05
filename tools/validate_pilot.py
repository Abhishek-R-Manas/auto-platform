"""Run isolated final checks and record actual outcomes for release 0.7.0."""
import json,os,re,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.research.jobs import CURRENT_BUILD

def main():
    env=dict(os.environ)
    for name in ('KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN','ARTHA_AI_API_KEY','OPENAI_API_KEY','ARTHA_TELEGRAM_BOT_TOKEN','ARTHA_TELEGRAM_CHAT_ID','ARTHA_LIVE_TRANSPORT_ENABLED'):env.pop(name,None)
    env['PYTHONDONTWRITEBYTECODE']='1';checks=[]
    commands=[[sys.executable,'-B','-m','pytest','-q'],[sys.executable,'-B','tools/pilot_charts_browser.py'],[sys.executable,'-B','tools/browser_smoke.py'],[sys.executable,'-B','tools/market_browser.py'],[sys.executable,'-B','tools/onboarding_browser.py'],[sys.executable,'-B','-m','pip','check']]
    commands.extend([['node','--check',str(p.relative_to(ROOT))] for p in sorted((ROOT/'static').glob('*.js'))])
    for command in commands:
        start=time.monotonic();result=subprocess.run(command,cwd=ROOT,env=env,text=True,capture_output=True);output=result.stdout+result.stderr
        entry=dict(command=command,passed=result.returncode==0,seconds=round(time.monotonic()-start,2),output=output);checks.append(entry)
        print(json.dumps(dict(command=command,passed=entry['passed'],seconds=entry['seconds'],summary=output[-1600:])),flush=True)
        record=dict(version='0.7.0',checked_at=datetime.now(timezone.utc).isoformat(),build=CURRENT_BUILD,checks=checks,all_passed=all(c['passed'] for c in checks),real_broker_orders_sent=False,genuine_provider_validation=False,live_transport_enabled=False,phase5_qualified=False)
        (ROOT/'docs/phase-5-validation.json').write_text(json.dumps(record,indent=2)+'\n')
        if result.returncode:raise SystemExit(result.returncode)
    print('All final checks passed',flush=True)
if __name__=='__main__':main()
