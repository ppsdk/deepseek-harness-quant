"""One isolated backend for new classical-rule and existing ML replay regressions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import requests

ROOT=Path(__file__).resolve().parents[1]


def main():
    out=ROOT/'output/strategy_research/technical_rule_index/verification';out.mkdir(parents=True,exist_ok=True)
    port=out/'port.txt';old=port.stat().st_mtime_ns if port.exists() else None
    with (out/'server.log').open('w',encoding='utf-8') as log:
        proc=subprocess.Popen([sys.executable,str(ROOT/'validation/workspace_smoke_server.py'),str(port)],cwd=ROOT,
            stdin=subprocess.PIPE,stdout=log,stderr=log,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            for _ in range(200):
                if port.exists() and port.stat().st_mtime_ns!=old:break
                if proc.poll() is not None:raise RuntimeError('Temporary backend exited')
                time.sleep(.1)
            base='http://127.0.0.1:'+port.read_text().strip();session=requests.Session();session.trust_env=False
            statuses={}
            for route in ['/backtests','/api/studio/config','/api/studio/technical-rules','/v2/components/rule_replay.js']:
                r=session.get(base+route,timeout=30);assert r.status_code==200,(route,r.status_code);statuses[route]=r.status_code
            env=dict(os.environ,RESEARCH_BASE_URL=base,NODE_PATH='C:/Users/Cy/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules')
            for name in ['test_technical_rules_ui.cjs','test_trade_replay_ui.cjs']:
                subprocess.run(['node',str(ROOT/'validation'/name)],cwd=ROOT,env=env,check=True,timeout=240)
            result=dict(ok=True,statuses=statuses,isolated_backend=True)
            (out/'http_verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
        finally:
            if proc.poll() is None:proc.stdin.write('stop\n');proc.stdin.flush();proc.wait(timeout=20)


if __name__=='__main__':main()
