"""HTTP and cached-data ledger checks against a fresh, isolated local server."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import requests

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT/'output/diversification_validation'
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp, (out/'server.log').open('w', encoding='utf-8') as log:
        port = Path(temp)/'port.txt'
        process = subprocess.Popen([sys.executable, str(ROOT/'validation/workspace_smoke_server.py'), str(port)],
                                   cwd=ROOT, stdin=subprocess.PIPE, stdout=log, stderr=log,
                                   text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for _ in range(150):
                if port.exists():
                    break
                if process.poll() is not None:
                    raise RuntimeError('Smoke server exited')
                time.sleep(.1)
            base = 'http://127.0.0.1:'+port.read_text().strip()
            session = requests.Session(); session.trust_env=False
            statuses = {}
            for path in ['/backtests', '/api/studio/diversification', '/api/studio/config']:
                response=session.get(base+path,timeout=60)
                assert response.status_code == 200, (path,response.text[:500])
                statuses[path]=response.status_code
            # Uses existing downloaded history, no network downloads or performance target.
            ids=[]; runs=[]
            for strategy in ('tech3','lowvol_defense'):
                response=session.post(base+'/api/research/run',json=dict(
                    strategy=strategy,start='2024-01-01',end='2024-06-28',stocks=40,topn=5,
                    diversification={'portfolio':dict(enabled=True,window=60,min_observations=30)}),timeout=180)
                assert response.status_code == 200, response.text[:1000]
                record=response.json(); ids.append(record['name'])
                assert record['diversification']['settings']['enabled']
                assert record['diversification']['signal']
                assert record['signal_candidates']
                assert all('position_weights' in r and 'exposures' in r for r in record['daily_account'])
                assert all(t['signal_date'] < t['date'] for t in record['trades'])
                assert all(r['cash'] >= -1e-7 for r in record['daily_account'])
                assert record['diversification']['final']['exposures']['industry']['unknown_weight'] >= 0
                runs.append(dict(name=record['name'],trades=len(record['trades']),days=len(record['daily_account'])))
            response=session.post(base+'/api/studio/strategy-risk/compare',json=dict(
                archive_ids=ids,as_of='2024-06-28',constraints={'min_observations':30}),timeout=60)
            assert response.status_code == 200, response.text[:1000]
            risk=response.json(); assert risk['pairwise'][0]['return_days'] >= 30
            assert risk['pairwise'][0]['signal']['state']=='ready'
            assert risk['pairwise'][0]['holdings']['state']=='ready'
            assert sum(risk['constrained_weights'].values()) <= 1
            bad=session.post(base+'/api/studio/strategy-risk/compare',json={'archive_ids':['../secret','b']},timeout=30)
            assert bad.status_code==400
            result=dict(ok=True,statuses=statuses,runs=runs,comparison=risk,path_traversal_status=bad.status_code)
            if '--browser' in sys.argv:
                env = dict(os.environ, RESEARCH_BASE_URL=base)
                subprocess.run(['node', str(ROOT/'validation/test_chart_controls_ui.cjs')],
                               cwd=ROOT, env=env, check=True, timeout=240)
                result['existing_chart_browser_regression'] = True
            (out/'http_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
            print(json.dumps(dict(ok=True,statuses=statuses,runs=runs,pairs=len(risk['pairwise'])),ensure_ascii=False))
        finally:
            if process.poll() is None:
                process.stdin.write('stop\n'); process.stdin.flush(); process.wait(timeout=20)


if __name__=='__main__':
    main()
