"""把研究资料发给 HARNESS 主会话，并按标记取回这次的 AI 回复。

发送走 HARNESS 桥接插件的 /quant/chat2，读取走 /quant/recent；两者都只在本机 127.0.0.1。
每条提问第一行带唯一标记，回复取「该标记之后的最后一条助手消息」，连续一段时间不变才视为写完。
"""
from datetime import datetime
import json
import time
import urllib.error
import urllib.parse
import urllib.request


def settings():
    from factors.research_library import read_config
    auto = read_config('research_workflow').get('automation', {})
    return dict(url=auto.get('harness_url', 'http://127.0.0.1:3080').rstrip('/'),
                timeout=auto.get('ai_send_timeout', 10),
                stable_seconds=auto.get('ai_reply_stable_seconds', 12),
                give_up_seconds=auto.get('ai_reply_timeout_seconds', 900))


def _call(path, body=None, timeout=10):
    cfg = settings()
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    req = urllib.request.Request(cfg['url'] + path, data=data, method='POST' if body is not None else 'GET',
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def send(text, session_id=None):
    """返回 dict(sent, accepted, session_id, message)；HARNESS 不可用时不抛异常。"""
    try:
        r = _call('/quant/chat2', {'text': text, 'sessionId': session_id or None}, settings()['timeout'])
        ok = bool(r.get('accepted'))
        return dict(sent=True, accepted=ok, session_id=r.get('sessionId'),
                    message='已发送到 HARNESS 主会话，等待 AI 回复。' if ok else (r.get('error') or 'HARNESS 未接收这条消息。'))
    except urllib.error.HTTPError as e:
        return dict(sent=False, accepted=False, message='发送失败（HTTP %d）：%s' % (e.code, e.reason))
    except Exception as e:
        return dict(sent=False, accepted=False, message='无法连接 HARNESS（%s）；请确认 HARNESS 已启动并配置模型。' % str(e)[:120])


def reply_after(messages, marker):
    """标记所在用户消息之后的最后一条助手消息；没有则 None。"""
    start = None
    for i, m in enumerate(messages):
        if m.get('role') == 'user' and marker in (m.get('text') or ''):
            start = i
    if start is None:
        return None
    replies = [m.get('text') or '' for m in messages[start + 1:] if m.get('role') == 'assistant' and (m.get('text') or '').strip()]
    return replies[-1] if replies else None


def poll(ai):
    """更新记录里的 ai 段（就地修改并返回是否有变化）。状态：waiting → partial → done / timeout。"""
    if not ai or not ai.get('accepted') or ai.get('state') in ('done', 'timeout'):
        return False
    cfg, now = settings(), time.time()
    try:
        query = urllib.parse.urlencode({'sessionId': ai.get('session_id') or '', 'limit': 12})
        messages = _call('/quant/recent?' + query, timeout=6).get('messages', [])
    except Exception:
        return False      # HARNESS 暂不可达时下次再取
    text = reply_after(messages, ai['marker'])
    changed = False
    if text and text != ai.get('reply'):
        ai.update(reply=text, reply_seen=now, state='partial', reply_at=datetime.now().isoformat(timespec='seconds'))
        changed = True
    elif text and now - ai.get('reply_seen', now) >= cfg['stable_seconds']:
        ai['state'] = 'done'
        changed = True
    elif not text and now - ai.get('sent_ts', now) >= cfg['give_up_seconds']:
        ai.update(state='timeout', message='较长时间没有取到回复，请到「AI 辅助」页查看主会话。')
        changed = True
    return changed


def start(prompt, marker, session_id=None):
    """发送并生成可存档的 ai 段。prompt 第一行应包含 marker。"""
    r = send(prompt, session_id)
    return dict(marker=marker, sent_at=datetime.now().isoformat(timespec='seconds'), sent_ts=time.time(),
                state='waiting' if r['accepted'] else 'failed', reply=None, **r)
