"""Check existing research page and config API on an isolated temporary backend."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import requests

ROOT=Path(__file__).resolve().parents[1]


def main():
    folder=ROOT/'output/strategy_research/multiroute_v1'
    port_file=folder/'http_smoke_port.txt';old=port_file.stat().st_mtime_ns if port_file.exists() else None
    with (folder/'http_smoke_server.log').open('w',encoding='utf-8') as log:
        proc=subprocess.Popen([sys.executable,str(ROOT/'validation/workspace_smoke_server.py'),str(port_file)],
            cwd=ROOT,stdin=subprocess.PIPE,stdout=log,stderr=log,text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            for _ in range(200):
                if port_file.exists() and port_file.stat().st_mtime_ns!=old:break
                if proc.poll() is not None:raise RuntimeError('Temporary backend exited')
                time.sleep(.1)
            base='http://127.0.0.1:'+port_file.read_text().strip()
            session=requests.Session();session.trust_env=False;statuses={}
            for route in ['/research','/strategies','/api/studio/config','/api/studio/strategy-assembly']:
                response=session.get(base+route,timeout=45)
                assert response.status_code==200,(route,response.status_code,response.text[:200])
                statuses[route]=response.status_code
            if (folder/'platform_archives.json').exists():
                env=dict(os.environ,RESEARCH_BASE_URL=base,NODE_PATH='C:/Users/Cy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')
                subprocess.run(['node',str(ROOT/'validation/test_multiroute_archives_ui.cjs')],cwd=ROOT,env=env,check=True,timeout=120)
            result=dict(ok=True,statuses=statuses,scope='existing page/API regression; new research delivered as local files',isolated_backend=True)
            (folder/'http_verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result))
        finally:
            if proc.poll() is None:
                proc.stdin.write('stop\n');proc.stdin.flush();proc.wait(timeout=20)


if __name__=='__main__':main()
