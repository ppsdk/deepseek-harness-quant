"""Verify strategy catalog and archived plots on an isolated local backend."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import requests

ROOT=Path(__file__).resolve().parents[1]


def main():
    folder=ROOT/'output/strategy_research/strategy_assembly_index';folder.mkdir(parents=True,exist_ok=True)
    port_file=folder/'smoke_port.txt';previous=port_file.stat().st_mtime_ns if port_file.exists() else None
    with (folder/'server.log').open('w',encoding='utf-8') as log:
        proc=subprocess.Popen([sys.executable,str(ROOT/'validation/workspace_smoke_server.py'),str(port_file)],cwd=ROOT,stdin=subprocess.PIPE,stdout=log,stderr=log,
            text=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            for _ in range(150):
                if port_file.exists() and port_file.stat().st_mtime_ns!=previous:break
                if proc.poll() is not None:raise RuntimeError('Temporary backend exited; see server.log')
                time.sleep(.1)
            base='http://127.0.0.1:'+port_file.read_text().strip();session=requests.Session();session.trust_env=False
            statuses={}
            for route in ['/strategies','/api/studio/strategy-assembly']:
                r=session.get(base+route,timeout=45);assert r.status_code==200,(route,r.text[:200]);statuses[route]=r.status_code
            env=dict(os.environ,RESEARCH_BASE_URL=base,NODE_PATH='C:/Users/Cy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')
            subprocess.run(['node',str(ROOT/'validation/test_strategy_assembly_ui.cjs')],cwd=ROOT,env=env,check=True,timeout=120)
            (folder/'http_verification.json').write_text(json.dumps(dict(ok=True,statuses=statuses,isolated_backend=True),indent=2),encoding='utf-8');print(statuses)
        finally:
            if proc.poll() is None:proc.stdin.write('stop\n');proc.stdin.flush();proc.wait(timeout=20)


if __name__=='__main__':main()
