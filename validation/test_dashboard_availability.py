"""Missing local outputs are empty states, not broken dashboard routes."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen
from http.server import ThreadingHTTPServer

from deck import deck_server as server, live_api


class AvailabilityTests(unittest.TestCase):
    def test_browser_asset_burst_is_served_without_connection_refusal(self):
        http=server.ResearchHTTPServer(('127.0.0.1',0),server.Handler)
        worker=threading.Thread(target=http.serve_forever,daemon=True);worker.start()
        origin=f'http://127.0.0.1:{http.server_port}'
        gate=threading.Barrier(24)
        def fetch(_):
            gate.wait(timeout=5)
            with urlopen(origin+'/v2/components/studio.js',timeout=5) as response:
                return response.status,len(response.read())
        try:
            with ThreadPoolExecutor(max_workers=24) as pool:results=list(pool.map(fetch,range(24)))
            self.assertTrue(all(code==200 and n>0 for code,n in results))
        finally:http.shutdown();http.server_close();worker.join()

    def test_system_python_launcher_selects_project_environment(self):
        import launcher
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            python = base / '.venv/Scripts/python.exe'
            python.parent.mkdir(parents=True)
            python.touch()
            with patch.object(launcher, 'base_dir', return_value=base), \
                    patch.object(launcher.sys, 'prefix', str(base / 'system')), \
                    patch.object(launcher.subprocess, 'call', return_value=0) as call:
                self.assertEqual(launcher.main(), 0)
                self.assertEqual(call.call_args.args[0][:2], [str(python), str(base / 'launcher.py')])
                self.assertEqual(call.call_args.kwargs['cwd'], base)

    def test_missing_outputs_and_unknown_route(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            routes = {key: base / file.name for key, file in server.ROUTES.items()}
            with patch.object(server, 'BASE', base), patch.object(server, 'LOGS', base), \
                    patch.object(server, 'OUTPUT', base), patch.object(server, 'ROUTES', routes):
                http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
                worker = threading.Thread(target=http.serve_forever, daemon=True)
                worker.start()
                origin = f'http://127.0.0.1:{http.server_port}'
                try:
                    for endpoint in ('pitch_track', 'pitch_v2', 'tech_pitch', 'etf_map', 'daily_report'):
                        with self.subTest(endpoint=endpoint), urlopen(origin + '/api/' + endpoint) as response:
                            self.assertEqual(response.status, 200)
                            result = json.load(response)
                            self.assertEqual(result['state'], 'not_generated')
                            self.assertTrue(result['message'])
                    with self.assertRaises(HTTPError) as error:
                        urlopen(origin + '/api/unknown')
                    self.assertEqual(error.exception.code, 404)
                    result_file = routes['/api/pitch_track']
                    result_file.write_text('{"entries": [{"code": "fixture"}]}', encoding='utf-8')
                    with urlopen(origin + '/api/pitch_track') as response:
                        self.assertEqual(json.load(response)['entries'][0]['code'], 'fixture')
                    with urlopen(origin + '/api/harness') as response:
                        state = json.load(response)
                        self.assertFalse(state['available'])
                        self.assertNotIn('online', state['harness'])
                finally:
                    http.shutdown()
                    http.server_close()
                    worker.join()

    def test_basic_catalog_has_no_fabricated_validation(self):
        from factors.factor_engine import FACTOR_FUNCS
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(live_api, 'BASE', Path(directory)), \
                patch.object(live_api, '_fd_cache', {'ts': 0, 'data': None}):
            result = live_api.live_factor_dash()
        self.assertEqual(result['state'], 'basic_only')
        rows = result['manifest']['factors']
        self.assertEqual({row['code'] for row in rows}, set(FACTOR_FUNCS))
        self.assertTrue(all(row['icir_60'] is None and row['status'] == '未验证' for row in rows))

    def test_realtime_does_not_wait_or_start_duplicate_workers(self):
        entered, release = threading.Event(), threading.Event()
        def slow_fetch():
            entered.set()
            release.wait(5)
        with patch.object(live_api, '_rt_cache', {'ts': 0, 'data': None, 'err': None}), \
                patch.object(live_api, '_rt_attempt', 0), \
                patch.object(live_api, '_fetch_realtime', side_effect=slow_fetch) as fetch:
            try:
                self.assertEqual(live_api.live_realtime()['state'], 'loading')
                self.assertTrue(entered.wait(1))
                self.assertEqual(live_api.live_realtime()['state'], 'loading')
                self.assertEqual(fetch.call_count, 1)
            finally:
                release.set()


if __name__ == '__main__':
    unittest.main()
