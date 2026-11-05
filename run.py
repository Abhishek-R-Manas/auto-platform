"""Run one local paper workspace. No external network listener is created."""
import argparse
import fcntl
import os
from pathlib import Path

import uvicorn

ROOT=Path(__file__).resolve().parent
os.umask(0o077)

if __name__=="__main__":
    parser=argparse.ArgumentParser(description="Artha local paper trading")
    parser.add_argument("--port",type=int,default=8787)
    args=parser.parse_args()
    from app.configuration import PrivateConfig
    try:
        # Explicitly saved application settings take precedence after an onboarding change.
        for name,value in PrivateConfig(ROOT/".env.local").read().items():os.environ[name]=value
    except ValueError as exc:raise SystemExit(str(exc)) from None
    data=ROOT/"data"
    data.mkdir(exist_ok=True)
    lock=(data/"server.lock").open("a")
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("A paper workspace process is already running. Stop it before starting another.")
    from app.api import create_app
    print(f"Artha paper workspace: http://127.0.0.1:{args.port}",flush=True)
    uvicorn.run(create_app(),host="127.0.0.1",port=args.port,workers=1,access_log=False,timeout_graceful_shutdown=8)
