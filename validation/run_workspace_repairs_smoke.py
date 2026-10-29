"""Isolated HTTP/browser checks; close only the temporary server started here."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import requests

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'output/workspace_repairs'

def main():
    FOLDER.mkdir(parents=True,exist_ok=True)
    bootstrap=Path(__file__).with_name('workspace_smoke_server.py')
    port_file=FOLDER/'smoke_port.txt'
    previous=port_file.stat().st_mtime_ns if port_file.exists() else None
    with (FOLDER/'server.log').open('w',encoding='utf-8') as log:
        proc=subprocess.Popen([sys.executable,str(bootstrap),str(port_file)],cwd=ROOT,stdin=subprocess.PIPE,stdout=log,stderr=log,text=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            for _ in range(150):
                if port_file.exists() and port_file.stat().st_mtime_ns!=previous:break
                if proc.poll() is not None:raise RuntimeError('Temporary server exited; see server.log')
                time.sleep(.1)
            base='http://127.0.0.1:'+port_file.read_text().strip()
            session=requests.Session();session.trust_env=False
            statuses={}
            for route in ['/research','/backtest','/data','/signals','/ml','/api/research/config','/api/studio/config','/api/data/akshare','/api/research/fundamentals','/api/studio/signals','/api/studio/pools']:
                r=session.get(base+route,timeout=45);assert r.status_code==200,(route,r.text[:300]);statuses[route]=r.status_code
            env=dict(os.environ,RESEARCH_BASE_URL=base,NODE_PATH='C:/Users/Cy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')
            for test in ['test_workspace_repairs_ui.cjs','test_chart_controls_ui.cjs','test_research_cadence_ui.cjs','test_market_cloud_ui.cjs']:
                subprocess.run(['node',str(ROOT/'validation'/test)],cwd=ROOT,env=env,check=True)
            (FOLDER/'http_verification.json').write_text(json.dumps(dict(ok=True,isolated_backend=True,statuses=statuses,existing_service_restarted=False),ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(dict(ok=True,statuses=statuses)))
        finally:
            if proc.poll() is None:
                proc.stdin.write('stop\n');proc.stdin.flush();proc.wait(timeout=20)

if __name__=='__main__':main()
