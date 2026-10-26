"""本机定时研究任务：按配置的步骤顺序执行，不下单、不改持仓。

任务与运行记录保存在 data/cache/research_automation.db；步骤目录、默认值和模板来自
config/research_workflow.yaml 的 automation 段。调度线程按上海时区每分钟检查一次；
程序未运行时错过的时点不补跑历史，只在当天启动后补跑当天一次。
"""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from uuid import uuid4

BASE = Path(__file__).resolve().parent.parent
DB = BASE / 'data/cache/research_automation.db'
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo('Asia/Shanghai')
except Exception:   # Windows 未装 tzdata 时：上海不实行夏令时，固定 UTC+8 等价
    TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')
RUN_LOCK = threading.Lock()          # 同一时间只执行一个自动化任务
_state = dict(compute_lock=None, scheduler=None, running=None)


def settings():
    from factors.research_library import read_config
    return read_config('research_workflow')['automation']


def _now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=15)
    con.execute('CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, task_id TEXT, started_at TEXT, payload TEXT NOT NULL)')
    con.commit()
    return con


# ── 任务存取 ────────────────────────────────────────────────────────────────

def list_tasks():
    if not DB.exists():
        return []
    with closing(connect()) as con:
        return [json.loads(r[0]) for r in con.execute('SELECT payload FROM tasks ORDER BY rowid')]


def get_task(key):
    if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{32}', key):
        raise ValueError('任务编号无效。')
    with closing(connect()) as con:
        row = con.execute('SELECT payload FROM tasks WHERE id=?', (key,)).fetchone()
    if not row:
        raise ValueError('没有找到这个自动化任务。')
    return json.loads(row[0])


def _write_task(item):
    with closing(connect()) as con, con:
        con.execute('INSERT OR REPLACE INTO tasks VALUES(?,?)',
                    (item['id'], json.dumps(item, ensure_ascii=False, allow_nan=False)))


def _save_run(run):
    limit = int(settings()['history_limit'])
    with closing(connect()) as con, con:
        con.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?,?)',
                    (run['id'], run['task_id'], run['started_at'], json.dumps(run, ensure_ascii=False, allow_nan=False)))
        con.execute('DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY started_at DESC LIMIT ?)', (limit,))


def recent_runs(limit=None):
    if not DB.exists():
        return []
    limit = int(limit or settings()['history_limit'])
    with closing(connect()) as con:
        rows = [json.loads(r[0]) for r in con.execute('SELECT payload FROM runs ORDER BY started_at DESC LIMIT ?', (limit,))]
    active = (_state['running'] or {}).get('id')
    for run in rows:
        # 服务重启会中断后台线程；不要把中断的记录显示成仍在运行。
        if run['state'] == 'running' and run['id'] != active:
            run['state'] = 'interrupted'
            run['message'] = '程序重启，这次运行中断。'
    return rows


def save_task(payload):
    from backtest.research_runner import research_config
    from factors.research_library import read_config
    if not isinstance(payload, dict):
        raise ValueError('请求必须为JSON对象。')
    cfg, research = settings(), research_config()
    old = get_task(payload['id']) if payload.get('id') else None
    if not old and len(list_tasks()) >= int(cfg['max_tasks']):
        raise ValueError('自动化任务数量已达上限，请先删除不用的任务。')
    name = str(payload.get('name', '')).strip()
    if not name or len(name) > 80:
        raise ValueError('请填写不超过80字的任务名称。')
    at = str(payload.get('time') or cfg['defaults']['time'])
    if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', at):
        raise ValueError('执行时间格式应为 HH:MM。')
    days = payload.get('weekdays', cfg['defaults']['weekdays'])
    if (not isinstance(days, list) or not days
            or any(isinstance(d, bool) or not isinstance(d, int) or not 1 <= d <= 7 for d in days)):
        raise ValueError('请至少选择一个执行日。')
    steps = payload.get('steps')
    if not isinstance(steps, list) or not steps or any(s not in cfg['steps'] for s in steps):
        raise ValueError('请至少选择一个有效步骤。')
    steps = [s for s in cfg['steps'] if s in steps]   # 固定顺序：后一步使用前一步的结果
    raw = payload.get('params') or {}
    if not isinstance(raw, dict):
        raise ValueError('任务参数格式不正确。')
    defaults, params = cfg['defaults'], {}

    def integer(key, low, high, label):
        try:
            value = int(raw.get(key, defaults.get(key)))
        except (TypeError, ValueError):
            raise ValueError(label + '必须是整数。') from None
        if not low <= value <= high:
            raise ValueError(f'{label}应在{low}到{high}之间。')
        return value

    if {'screen_pool', 'backtest', 'ai_review'} & set(steps):
        strategy = raw.get('strategy') or research['default_strategy']
        if strategy not in research['strategies']:
            raise ValueError('请选择策略目录中的策略。')
        params['strategy'] = strategy
    if 'screen_pool' in steps:
        params['topn'] = integer('topn', 1, int(research['parameters']['topn']['max']), '选股数量')
        params['stocks'] = integer('stocks', 1, int(research['parameters']['stocks']['max']), '参与选股的股票数')
        if params['topn'] > params['stocks']:
            raise ValueError('选股数量不能超过参与选股的股票数。')
    if 'backtest' in steps:
        params['years'] = integer('years', 1, int(cfg['max_years']), '回测年数')
    if 'signal_check' in steps:
        plan, preset = raw.get('signal_plan_id') or '', raw.get('signal_preset') or ''
        if plan:
            from backtest.research_signals import plans
            plans({'id': plan})   # 不存在时抛出说明
            params['signal_plan_id'] = plan
        elif preset in read_config('research_workflow')['signals']['presets'] and 'screen_pool' in steps:
            params['signal_preset'] = preset
        else:
            raise ValueError('检查信号需要选择已保存的信号方案；或同时勾选选股步骤并选一个条件预设，为新名单自动建方案。')
    if 'ai_review' in steps:
        task = raw.get('assistant_task') or defaults['assistant_task']
        if task not in read_config('research_services')['assistant']['tasks']:
            raise ValueError('请选择AI复核的提问类型。')
        params['assistant_task'] = task

    now = datetime.now(TZ)
    item = dict(id=old['id'] if old else uuid4().hex, name=name, enabled=bool(payload.get('enabled', True)),
                time=at, weekdays=sorted(set(days)), steps=steps, params=params,
                created_at=old['created_at'] if old else _now(), updated_at=_now(),
                last_run_date=(old or {}).get('last_run_date'), last_run=(old or {}).get('last_run'))
    # 新建时今天的时点已过，从下一个执行日开始，避免保存后立即运行。
    if not old and now.strftime('%H:%M') >= at:
        item['last_run_date'] = now.date().isoformat()
    _write_task(item)
    nxt = next_run(item)
    return dict(ok=True, task=dict(item, next_run=nxt),
                message='已保存。' + ('下次执行：' + nxt if nxt else '任务已停用，不会自动执行。'))


def delete_task(payload):
    key = (payload or {}).get('id', '')
    task = get_task(key)
    with closing(connect()) as con, con:
        con.execute('DELETE FROM tasks WHERE id=?', (key,))
    return dict(ok=True, message='已删除「' + task['name'] + '」；历史运行记录保留。')


# ── 调度 ────────────────────────────────────────────────────────────────────

def _due(task, now):
    return (task.get('enabled') and now.isoweekday() in task['weekdays']
            and task.get('last_run_date') != now.date().isoformat() and now.strftime('%H:%M') >= task['time'])


def next_run(task, now=None):
    if not task.get('enabled'):
        return None
    now = now or datetime.now(TZ)
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        if day.isoweekday() not in task['weekdays']:
            continue
        if offset == 0 and task.get('last_run_date') == day.isoformat():
            continue
        if offset == 0 and now.strftime('%H:%M') >= task['time']:
            return '即将执行（今天 ' + task['time'] + ' 已到）'
        return day.isoformat() + ' ' + task['time']
    return None


def tick(now=None):
    now = now or datetime.now(TZ)
    if RUN_LOCK.locked():
        return None
    for task in list_tasks():
        if _due(task, now):
            task['last_run_date'] = now.date().isoformat()
            _write_task(task)
            thread = threading.Thread(target=execute, args=(task['id'], 'schedule'), daemon=True,
                                      name='automation-' + task['id'][:8])
            thread.start()
            return task['id']   # 一次只启动一个，其余任务下个周期再检查
    return None


def start_scheduler(compute_lock):
    """由 launcher 在 HTTP 服务创建后调用；与网页请求共用同一把研究计算锁。"""
    _state['compute_lock'] = compute_lock
    if _state['scheduler'] and _state['scheduler'].is_alive():
        return

    def loop():
        while True:
            try:
                tick()
                pause = int(settings().get('scheduler_seconds', 60))
            except Exception as error:
                print('[automation] 调度检查失败：' + str(error)[:200])
                pause = 60
            time.sleep(max(15, pause))

    _state['scheduler'] = threading.Thread(target=loop, daemon=True, name='research-automation')
    _state['scheduler'].start()


def run_now(payload, compute_lock):
    if _state['compute_lock'] is None:
        _state['compute_lock'] = compute_lock
    task = get_task((payload or {}).get('id', ''))
    if RUN_LOCK.locked():
        raise ValueError('已有自动化任务在运行，请等它完成后再试。')
    threading.Thread(target=execute, args=(task['id'], 'manual'), daemon=True,
                     name='automation-' + task['id'][:8]).start()
    return dict(ok=True, message='已开始执行「' + task['name'] + '」，每一步结果显示在运行记录中。')


def execute(key, trigger='manual'):
    if not RUN_LOCK.acquire(timeout=3600):
        return None
    try:
        task = get_task(key)
        labels = settings()['steps']
        run = dict(id=uuid4().hex, task_id=key, task_name=task['name'], trigger=trigger,
                   state='running', started_at=_now(), steps=[])
        _state['running'] = run
        _save_run(run)
        ctx = dict(task_name=task['name'], summary=[])
        for step in task['steps']:
            record = dict(step=step, label=labels[step]['label'], state='running', started_at=_now())
            run['steps'].append(record)
            _save_run(run)
            try:
                record.update(STEPS[step](task['params'], ctx), state='done')
            except Exception as error:
                record.update(state='failed', message=str(error)[:500])
            record['finished_at'] = _now()
            ctx['summary'].append(record['label'] + '：' + ('' if record['state'] == 'done' else '未完成，') + record.get('message', ''))
            _save_run(run)
        states = {s['state'] for s in run['steps']}
        run['state'] = 'done' if states == {'done'} else 'failed' if states == {'failed'} else 'partial'
        run['finished_at'] = _now()
        run['message'] = {'done': '全部步骤完成。', 'partial': '部分步骤未完成，查看各步骤说明。',
                          'failed': '所有步骤都未完成。'}[run['state']]
        _save_run(run)
        try:
            latest = get_task(key)   # 重新读取，避免覆盖运行期间的编辑
            latest['last_run'] = dict(run_id=run['id'], state=run['state'], finished_at=run['finished_at'], message=run['message'])
            _write_task(latest)
        except ValueError:
            pass   # 运行期间任务被删除
        return run
    finally:
        _state['running'] = None
        RUN_LOCK.release()


# ── 步骤实现 ────────────────────────────────────────────────────────────────

def _with_compute(fn):
    lock = _state['compute_lock']
    if lock is None:
        return fn()
    if not lock.acquire(timeout=1800):
        raise ValueError('等待其他研究计算超过30分钟，本步跳过。')
    try:
        return fn()
    finally:
        lock.release()


def _pct(value):
    return '—' if value is None else f'{value * 100:.2f}%'


def _step_market(params, ctx):
    from data import market_dashboard as md
    md.refresh('automation')
    deadline = time.monotonic() + int(settings()['market_wait_seconds'])
    while time.monotonic() < deadline:
        with md._lock:            # 只读取刷新进度，不改状态
            state = dict(md._state)
        if state['state'] != 'running':
            break
        time.sleep(3)
    else:
        return dict(message='市场数据仍在后台更新，本步不再等待。', link='/research#workbench-market')
    if state['state'] == 'failed':
        raise ValueError(state.get('message') or '市场数据更新失败。')
    return dict(message=state.get('message') or '已更新。', link='/research#workbench-market')


def _step_market_report(params, ctx):
    from data.market_analysis import build
    record = build({'refresh': True, 'ai': True}, _state['compute_lock'])['record']
    s, ai = record['scores'], record.get('ai') or {}
    ctx['market_state'] = s['state']
    return dict(message=f'{record["date"]}：{s["state"]}（{s["total"]} 分）。' + (ai.get('message') or ''),
                link='/market#market-analysis')


def _step_screen(params, ctx):
    from backtest.research_runner import research_config
    from backtest.research_workspace import screen
    from backtest.research_pools import save_pool
    from data.research_quality import closed_day
    result = _with_compute(lambda: screen(dict(strategy=params['strategy'], topn=params['topn'],
                                               stocks=params['stocks'], end=closed_day())))
    rows = result['candidates']
    if not rows:
        raise ValueError('没有符合条件的候选。' + ''.join(result.get('warnings', [])[-1:]))
    day = str(result['signal_date'])[:10]
    title = research_config()['strategies'][params['strategy']]['name']
    pool = save_pool(dict(name=(title + ' ' + day + ' 自动')[:80], codes=[r['code'] for r in rows],
                          signal_date=day, source_strategy=params['strategy'], source_params=result['params']))
    ctx.update(pool_id=pool['id'], signal_date=day)
    return dict(message=f'按 {day} 收盘选出 {len(rows)} 只，已保存为「{pool["name"]}」。',
                items=[(r['code'] + ' ' + r['name']).strip() for r in rows[:10]],
                link='/signals?pool_id=' + pool['id'])


def _step_signals(params, ctx):
    from backtest.research_signals import check, save
    from data.research_quality import closed_day
    from factors.research_library import read_config
    plan_id, created = params.get('signal_plan_id'), None
    if not plan_id:
        if not ctx.get('pool_id'):
            raise ValueError('选股步骤没有保存名单，无法按预设新建信号方案。')
        preset = read_config('research_workflow')['signals']['presets'][params['signal_preset']]
        plan = save(dict(pool_id=ctx['pool_id'], name=(preset['label'] + ' · ' + ctx['signal_date'])[:80],
                         entry=preset['entry'], exit=preset['exit']))
        plan_id = created = plan['id']
    result = _with_compute(lambda: check(dict(id=plan_id, end=closed_day())))
    counts = {}
    for row in result['rows']:
        counts[row['label']] = counts.get(row['label'], 0) + 1
    entries = [(r['code'] + ' ' + r['name']).strip() for r in result['rows'] if r['state'] == 'entry']
    ctx['entries'] = entries
    return dict(message='截至 ' + str(result['asof'])[:10] + '：' + '，'.join(f'{k} {v} 只' for k, v in counts.items()) + '。',
                items=entries[:20], plan_id=plan_id, created_plan=created, link='/signals')


def _step_backtest(params, ctx):
    from backtest.research_runner import research_config, run_research
    from data.research_quality import closed_day
    cfg, end = research_config(), date.fromisoformat(closed_day())
    start = date(end.year - params['years'], end.month, min(end.day, 28)).isoformat()
    spec = cfg['strategies'][params['strategy']]
    # 换手率 2019 年前缺失：含换手因子的策略只回测 2019 年及以后。
    if any(f['name'] == 'turn_20d' or 'turn' in cfg['factor_catalog'].get(f['name'], {}).get('requires', [])
           for f in spec['factor_list']):
        start = max(start, '2019-01-01')
    values = dict(strategy=params['strategy'], start=start, end=end.isoformat())
    if params.get('stocks'):
        values['stocks'] = params['stocks']
    # 回测策略的选股规则本身，不使用今天才选出的名单（否则就是用未来名单检验过去）。
    result = _with_compute(lambda: run_research(values))
    m = result.get('metrics') or {}
    ctx['run'] = result['name']
    return dict(message=f'{result["title"]} {start} 至 {end}：评级 {result["verdict"]}，年化 {_pct(m.get("annual_return"))}，'
                        f'最大回撤 {_pct(m.get("max_drawdown"))}，夏普 {m.get("sharpe", 0):.2f}。',
                verdict=result['verdict'], run=result['name'], link='/backtests?run=' + result['name'])


def _step_ai(params, ctx):
    from backtest.research_assistant import draft
    question = '\n'.join(['自动化任务「' + ctx['task_name'] + '」本次结果：'] + ctx['summary'])[:3000]
    result = draft(dict(task=params['assistant_task'], strategy=params.get('strategy'),
                        pool_id=ctx.get('pool_id', ''), run=ctx.get('run', ''), question=question, send=True))
    if not (result.get('sent') and result.get('accepted')):
        raise ValueError(result['message'])
    return dict(message=result['message'], link='/control')


STEPS = {'market_refresh': _step_market, 'market_report': _step_market_report, 'screen_pool': _step_screen, 'signal_check': _step_signals,
         'backtest': _step_backtest, 'ai_review': _step_ai}


# ── 页面数据 ────────────────────────────────────────────────────────────────

def overview(query=None):
    from backtest.research_runner import research_config
    from backtest.research_signals import plans
    from factors.research_library import read_config
    cfg, research, now = settings(), research_config(), datetime.now(TZ)
    scheduler = _state['scheduler']
    return dict(ok=True, settings=cfg,
                tasks=[dict(t, next_run=next_run(t, now)) for t in list_tasks()],
                runs=recent_runs(),
                strategies={k: v['name'] for k, v in research['strategies'].items()},
                default_strategy=research['default_strategy'],
                signal_plans=[dict(id=p['id'], name=p['name'], pool_name=p.get('pool_name', '')) for p in plans()['plans']],
                signal_presets={k: v['label'] for k, v in read_config('research_workflow')['signals']['presets'].items()},
                assistant_tasks={k: v['label'] for k, v in read_config('research_services')['assistant']['tasks'].items()},
                limits=dict(topn=research['parameters']['topn']['max'], stocks=research['parameters']['stocks']['max'],
                            years=cfg['max_years']),
                scheduler=dict(active=bool(scheduler and scheduler.is_alive()), now=now.strftime('%Y-%m-%d %H:%M'),
                               running=dict(_state['running']) if _state['running'] else None))
