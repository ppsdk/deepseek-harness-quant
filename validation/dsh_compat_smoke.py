"""DSH integration smoke check with an isolated home and local Messages fixture.

No market data, real credentials, external model calls, or model tool execution.
Run from the repository root: python validation/dsh_compat_smoke.py
"""
import argparse
import copy
import http.cookiejar
import json
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import yaml

BASE = Path(__file__).resolve().parent.parent
BIN = BASE / 'harness/node_modules/@deepseek-ai/dsh/lib/bin.js'
BRIDGE = BASE / 'harness/home/profiles/web/plugins/dsq-quant-bridge.js'
requests = []


class ModelFixture(BaseHTTPRequestHandler):
    protocol = 'messages'
    expected_model = None
    def log_message(self, *args):
        pass

    def do_POST(self):
        expected_path = '/v1/chat/completions' if self.protocol == 'chat-completions' else '/v1/messages'
        assert self.path == expected_path, self.path
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.expected_model:
            assert body['model'] == self.expected_model, body['model']
        requests.append(body)
        frames = [
            {'type': 'message_start', 'message': {'usage': {'input_tokens': 1}}},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'DSH_SMOKE_OK'}},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 1}},
            {'type': 'message_stop'},
        ]
        data = ''.join('event: ' + f['type'] + '\ndata: ' + json.dumps(f) + '\n\n' for f in frames).encode()
        if self.protocol == 'chat-completions':
            chunks = [
                {'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]},
                {'choices': [{'index': 0, 'delta': {'content': 'DSH_SMOKE_OK'}, 'finish_reason': None}]},
                {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}},
            ]
            for chunk in chunks:
                chunk.update(id='fixture-completion', object='chat.completion.chunk', created=1, model=body['model'])
            data = (''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_for(check, timeout=40):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            result = check()
            if result:
                return result
        except (OSError, urllib.error.URLError, ValueError) as error:
            last = error
        time.sleep(.25)
    raise AssertionError(f'condition timed out ({last})')


def system_text(request):
    system = request.get('system', [message for message in request.get('messages', []) if message.get('role') in ('system', 'developer')])
    return json.dumps(system, ensure_ascii=False)


def main(protocol='messages', harness_port=3080):
    if not BIN.exists():
        raise SystemExit('Install harness dependencies first.')
    if harness_port == 0:
        harness_port = free_port()
    with socket.socket() as check:
        check.bind(('127.0.0.1', harness_port))  # never interrupt an existing service
    ModelFixture.protocol = protocol
    model = ThreadingHTTPServer(('127.0.0.1', 0), ModelFixture)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    jar = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar))
    children = []
    output_lines = []
    announcements = queue.Queue()

    def request(path, payload=None, port=harness_port):
        req = urllib.request.Request('http://127.0.0.1:' + str(port) + path,
                                     data=None if payload is None else json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        with client.open(req, timeout=8) as response:
            body = response.read()
            return json.loads(body) if 'application/json' in response.headers.get('Content-Type', '') else body

    def collect(process):
        for line in process.stdout:
            output_lines.append(re.sub(r'token=[^\s]+', 'token=<redacted>', line))
            if line.startswith('dsh web: '):
                announcements.put(line.split('dsh web: ', 1)[1].strip())

    def stop(process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)

    try:
        with tempfile.TemporaryDirectory(prefix='dsh-compat-') as tmp:
            home = Path(tmp)
            profile = home / 'profiles/web'
            profile.mkdir(parents=True)
            shutil.copyfile(BASE / 'harness/home/profiles/web/package.json', profile / 'package.json')
            (profile / 'cordis.yml').write_text('[]\n', encoding='utf-8')
            diagnostic = home / 'diagnostic.cjs'
            diagnostic.write_text("module.exports = { apply(ctx) { ctx.on('agent/error', ({error}) => console.error('SMOKE_AGENT_ERROR', error)) } }\n", encoding='utf-8')
            patch = [
                {'insert': [{'id': 'smoke-diagnostic', 'name': diagnostic.as_posix()}, {'id': 'dsq-quant-bridge', 'name': BRIDGE.as_posix()}]},
                {'id': 'agent-default-model', 'config': {'provider': 'deepseek-official', 'model': 'deepseek-v4-flash', 'reasoningEffort': 'off'}},
                {'id': 'llm-deepseek', 'config': {'baseURL': f'http://127.0.0.1:{model.server_port}', 'apiKeyEnv': 'DSH_COMPAT_FIXTURE_KEY', 'thinking': 'disabled'}},
            ]
            if protocol == 'chat-completions':
                actual = yaml.safe_load((BASE / 'harness/home/profiles/web/cordis.patch.yml').read_text(encoding='utf-8'))
                default = copy.deepcopy(next(row for row in actual if row.get('id') == 'agent-default-model'))
                provider = default['config']['provider']
                routes = copy.deepcopy(next(row for row in actual if row.get('id') == 'llm-pi-ai'))
                selected = routes['config']['providers'][provider]
                assert selected['api'] == 'openai-completions'
                assert selected['baseURL'] == 'https://platform.dataeyes.ai/v1'
                selected.update(baseURL=f'http://127.0.0.1:{model.server_port}/v1', apiKeyEnv='DSH_COMPAT_FIXTURE_KEY')
                ModelFixture.expected_model = default['config']['model']
                patch = [patch[0], default, routes]
            (profile / 'cordis.patch.yml').write_text(json.dumps(patch), encoding='utf-8')
            env = dict(os.environ, DSH_HOME=str(home), DSH_COMPAT_FIXTURE_KEY='local-fixture-only', NO_PROXY='127.0.0.1,localhost')

            def start():
                process = subprocess.Popen([shutil.which('node'), str(BIN), 'web', '--no-open', '--host', '127.0.0.1', '--port', str(harness_port)],
                                           cwd=BASE / 'harness', env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, encoding='utf-8', errors='replace')
                children.append(process)
                threading.Thread(target=collect, args=(process,), daemon=True).start()
                url = announcements.get(timeout=40)
                with client.open(url, timeout=8) as response:
                    assert response.status == 200
                wait_for(lambda: request('/quant/sessions')['sessions'])
                return process

            version = subprocess.check_output([shutil.which('node'), str(BIN), '--version'], text=True).strip()
            assert version == '0.2.1-rc.2', version
            dsh = start()
            sessions = request('/quant/sessions')['sessions']
            assert len(sessions) == 1, sessions
            root_id = sessions[0]['id']
            assert request('/quant/chat2', {'text': 'root smoke', 'sessionId': root_id})['accepted']
            try:
                wait_for(lambda: any(m['text'] == 'DSH_SMOKE_OK' for m in request('/quant/recent?sessionId=' + root_id)['messages']))
            except AssertionError:
                print('fixture requests:', len(requests), file=sys.stderr)
                for log in home.rglob('*.jsonl'):
                    for line in log.read_text(encoding='utf-8').splitlines():
                        event = json.loads(line)
                        if event.get('type') in ('turn/end', 'error', 'agent/error'):
                            print(json.dumps(event, ensure_ascii=True)[:1500], file=sys.stderr)
                raise
            print('PASS root create, queued input, Messages stream, recent history')

            catalog = request('/quant/niu/sessions')['personas']
            key = catalog[0]['id']
            assert len(catalog) == len(json.loads((BASE / 'config/niu_personas.json').read_text(encoding='utf-8')))
            assert request('/quant/niu/chat', {'persona': key, 'text': 'persona first', 'snapshot': 'synthetic snapshot'})['accepted']
            recent_path = '/quant/niu/recent?persona=' + key
            wait_for(lambda: len(request(recent_path)['messages']) >= 2)
            assert request('/quant/niu/chat', {'persona': key, 'text': 'persona followup'})['accepted']
            wait_for(lambda: len(request(recent_path)['messages']) >= 4)
            persona_requests = [r for r in requests if catalog[0]['name'] in system_text(r)]
            assert len(persona_requests) >= 2, 'persona prompt was not injected'
            assert all(not r.get('tools') for r in persona_requests), 'persona inherited tools'
            print('PASS dynamic persona catalog, activation, followup, persona prompt, zero tool schemas')

            try:
                request('/quant/chat2', {'text': ''})
            except urllib.error.HTTPError as error:
                assert error.code == 400
            else:
                raise AssertionError('empty chat was accepted')

            deck_port = free_port()
            deck = subprocess.Popen([sys.executable, 'deck/deck_server.py', '--port', str(deck_port)], cwd=BASE,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            children.append(deck)
            wait_for(lambda: request('/control', port=deck_port))
            if harness_port == 3080:
                for endpoint in ['/api/proxy/quant/sessions', '/api/proxy/quant/recent?sessionId=' + root_id,
                                 '/api/proxy/quant/niu/sessions', '/api/proxy/quant/niu/recent?persona=' + key]:
                    assert request(endpoint, port=deck_port)['ok']
                assert request('/api/proxy/quant/chat2', {'text': 'proxy smoke', 'sessionId': root_id}, port=deck_port)['accepted']
            else:
                assert request('/quant/chat2', {'text': 'second root smoke', 'sessionId': root_id})['accepted']
            wait_for(lambda: len(request('/quant/recent?sessionId=' + root_id)['messages']) >= 4)
            print('PASS control page 200, bad input 400' + (', proxy GET/POST APIs' if harness_port == 3080 else ', isolated port (live proxy untouched)'))

            stop(dsh)
            dsh = start()
            assert request('/quant/sessions')['sessions'][0]['id'] == root_id
            assert len(request('/quant/recent?sessionId=' + root_id)['messages']) >= 4
            assert len(request(recent_path)['messages']) >= 4
            before_cold = len(requests)
            assert request('/quant/niu/chat', {'persona': key, 'text': 'persona cold resume'})['accepted']
            wait_for(lambda: len(request(recent_path)['messages']) >= 6)
            cold_requests = [r for r in requests[before_cold:] if catalog[0]['name'] in system_text(r)]
            assert cold_requests, 'cold resumed persona prompt was not injected'
            assert all(not r.get('tools') for r in cold_requests), 'cold resumed persona inherited tools'
            print('PASS restart, root resume, persisted child history, child cold resume without tools')
            for process in reversed(children):
                stop(process)
        print('PASS DSH ' + version + ' integration (local fixture; real API authentication untested)')
    except Exception:
        print(''.join(output_lines)[-7000:], file=sys.stderr)
        raise
    finally:
        for process in reversed(children):
            stop(process)
        model.shutdown()
        model.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', choices=['messages', 'chat-completions'], default='messages')
    parser.add_argument('--port', type=int, default=3080)
    args = parser.parse_args()
    main(args.protocol, args.port)
