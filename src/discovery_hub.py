"""Persistent discovery library, comparable observations, local reports and schedules."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import csv
import html
import io
import json
import re
import statistics
import threading
import uuid

import discovery as d
import douk
import playback
import store
import tikhub
import creative_focus as creative
import intelligence
from media_cache import cached_image

LOCK = threading.RLock()
PRICE_DATE = '2026-11-04'
UNIT_USD = .001
DEFAULTS = {'enabled': False, 'times': ['08:00', '20:00'], 'pages': 5,
            'categories': ['601', '607', '643'], 'window': 168, 'include_hot': True, 'include_live': True,
            'daily_report': True, 'weekly_report': True, 'report_time': '21:00',
            'enabled_at': '', 'revision': ''}


def init():
    d.init()
    for sql in (
        'CREATE TABLE IF NOT EXISTS discovery_hub_index(snapshot_id TEXT PRIMARY KEY)',
        'CREATE TABLE IF NOT EXISTS discovery_entries(kind TEXT NOT NULL,item_id TEXT NOT NULL,value_json TEXT NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,PRIMARY KEY(kind,item_id))',
        'CREATE TABLE IF NOT EXISTS discovery_sightings(snapshot_id TEXT NOT NULL,kind TEXT NOT NULL,item_id TEXT NOT NULL,source TEXT NOT NULL,scope_key TEXT NOT NULL,query_json TEXT NOT NULL,observed_at TEXT NOT NULL,value_json TEXT NOT NULL,PRIMARY KEY(snapshot_id,item_id))',
        'CREATE INDEX IF NOT EXISTS discovery_seen_item ON discovery_sightings(kind,item_id,observed_at)',
        'CREATE INDEX IF NOT EXISTS discovery_seen_scope ON discovery_sightings(scope_key,observed_at)',
        'CREATE TABLE IF NOT EXISTS discovery_hub_settings(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS discovery_hub_runs(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,payload_json TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS discovery_hub_slots(kind TEXT NOT NULL,slot TEXT NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL,PRIMARY KEY(kind,slot))',
        'CREATE TABLE IF NOT EXISTS discovery_reports(id TEXT PRIMARY KEY,kind TEXT NOT NULL,start_at TEXT NOT NULL,end_at TEXT NOT NULL,generated_at TEXT NOT NULL,payload_json TEXT NOT NULL)',
    ):
        store.x(sql)


def ingest(snapshot_id):
    """One transaction per snapshot, including the index marker; safe after a restart."""
    init()
    if store.q('SELECT 1 FROM discovery_hub_index WHERE snapshot_id=?', (snapshot_id,)):
        return
    rows = store.q('SELECT * FROM discovery_snapshots WHERE id=?', (snapshot_id,))
    if not rows:
        return
    snap = rows[0]
    q = json.loads(snap['query_json'])
    section = d.SOURCES.get(q['source'], ('', '', ''))[2]
    kind = 'works' if section == 'works' else 'hot' if section == 'hot' else None
    scope = {k: v for k, v in q.items() if k != 'date'}
    with store._LOCK, store.get_conn() as conn:
        if conn.execute('SELECT 1 FROM discovery_hub_index WHERE snapshot_id=?', (snapshot_id,)).fetchone():
            return
        if kind:
            for position, item in enumerate(json.loads(snap['payload_json']).get('items', [])):
                ident = str(item.get('id') or item.get('aweme_id') or '')
                if not ident:
                    continue
                item = dict(item)
                item['source']=q['source']
                item.setdefault('rank', position + 1)
                observed_at=item.get('fetched_at') or snap['created_at']
                try:datetime.fromisoformat(observed_at)
                except (ValueError,TypeError):observed_at=snap['created_at']
                item['fetched_at'] = observed_at
                if conn.execute('SELECT 1 FROM discovery_sightings WHERE kind=? AND item_id=? AND source=? AND scope_key=? AND observed_at=?',
                                (kind,ident,q['source'],d.key(scope),observed_at)).fetchone():
                    continue
                text = json.dumps(item, ensure_ascii=False)
                conn.execute('INSERT OR IGNORE INTO discovery_sightings VALUES(?,?,?,?,?,?,?,?)',
                             (snapshot_id, kind, ident, q['source'], d.key(scope), snap['query_json'], observed_at, text))
                old = conn.execute('SELECT * FROM discovery_entries WHERE kind=? AND item_id=?', (kind, ident)).fetchone()
                if old:
                    first, last = min(old['first_seen'], observed_at), max(old['last_seen'], observed_at)
                    base, update = (json.loads(old['value_json']), item) if observed_at >= old['last_seen'] else (item, json.loads(old['value_json']))
                    base.update({k: v for k, v in update.items() if v is not None and v != ''})
                    text = json.dumps(base, ensure_ascii=False)
                else:
                    first = last = observed_at
                conn.execute('INSERT OR REPLACE INTO discovery_entries VALUES(?,?,?,?,?)', (kind, ident, text, first, last))
        conn.execute('INSERT INTO discovery_hub_index VALUES(?)', (snapshot_id,))


def backfill():
    init()
    for row in store.q('SELECT s.id FROM discovery_snapshots s LEFT JOIN discovery_hub_index i ON s.id=i.snapshot_id WHERE i.snapshot_id IS NULL ORDER BY s.created_at,s.id'):
        ingest(row['id'])


def config():
    init()
    rows = store.q('SELECT value FROM discovery_hub_settings WHERE id=1')
    return {**DEFAULTS, **(json.loads(rows[0]['value']) if rows else {})}


def validate_config(data, original=None):
    if not isinstance(data, dict) or set(data) - set(DEFAULTS) - {'confirmed', 'fingerprint'}:
        raise ValueError('自动更新设置格式无效')
    cfg = {**(original or config()), **{k: v for k, v in data.items() if k not in {'confirmed', 'fingerprint'}}}
    for key in ['enabled', 'include_hot', 'include_live', 'daily_report', 'weekly_report']:
        if not isinstance(cfg[key], bool):
            raise ValueError('计划开关无效')
    cfg['pages'] = d.integer(cfg['pages'], 1, 5, '每榜抓取页数')
    cfg['window'] = d.integer(cfg['window'], 1, 168, '榜单周期')
    if cfg['window'] not in {1, 24, 72, 168}:
        raise ValueError('榜单周期无效')
    cats = cfg['categories']
    if not isinstance(cats, list) or not 1 <= len(cats) <= 8 or any(str(c) not in {str(v['value']) for v in d.categories()} for c in cats):
        raise ValueError('请选择 1 至 8 个平台分类')
    cfg['categories'] = sorted(set(map(str, cats)))
    times = cfg['times']
    valid_time = lambda t: isinstance(t, str) and bool(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', t))
    if not isinstance(times, list) or not 1 <= len(times) <= 6 or not all(valid_time(t) for t in times) or not valid_time(cfg['report_time']):
        raise ValueError('每天可设 1 至 6 个更新时间，格式为 HH:MM')
    cfg['times'] = sorted(set(times))
    return cfg


def queries(cfg, at=None):
    at = at or d.now()
    result = [{'source': 'video_total', 'categories': cfg['categories'], 'window': cfg['window']}]
    if cfg['include_hot']:
        result.append({'source': 'hot_total', 'date': at.date().isoformat(), 'window': 168})
    if cfg['include_live']:
        result.append({'source': 'hot_live'})
    return result


def sync_plan(data=None, at=None):
    cfg = validate_config(data or {})
    plans = [d.plan({**q, 'pages': 1 if q['source']=='hot_live' else cfg['pages']}) for q in queries(cfg, at)]
    calls = sum(p['calls'] for p in plans)
    reason = next((p['reason'] for p in plans if not p['allowed']), '')
    if calls + tikhub.usage() > playback.settings()['daily_call_limit']:
        reason = '今日剩余请求额度不足以完成这一轮'
    identity = {'queries': [p['query'] for p in plans], 'pages': cfg['pages']}
    return {'allowed': not reason, 'reason': reason, 'calls': calls,
            'estimated_usd': round(calls * UNIT_USD, 4), 'price_date': PRICE_DATE,
            'daily_requests': calls * len(cfg['times']), 'monthly_usd': round(calls * len(cfg['times']) * 30 * UNIT_USD, 3),
            'pages': cfg['pages'], 'queries': [p['query'] for p in plans], 'fingerprint': d.key(identity),
            'plans': plans, 'settings': cfg}


def save_config(data):
    before = config()
    cfg = validate_config(data, before)
    if cfg['enabled']:
        p = sync_plan(cfg)
        if not p['allowed']:
            raise ValueError(p['reason'])
        if p['daily_requests'] > playback.settings()['daily_call_limit']:
            raise ValueError('计划每日请求数超过已设置的全局每日额度')
    cfg['revision'] = uuid.uuid4().hex
    cfg['enabled_at'] = d.now().isoformat()
    store.x('INSERT OR REPLACE INTO discovery_hub_settings VALUES(1,?)', (json.dumps(cfg),))
    return automation()


def submit_sync(data, scheduled=False):
    with LOCK:
        p = sync_plan(data)
        if not p['allowed']:
            raise ValueError(p['reason'])
        if not scheduled and (data.get('confirmed') is not True or data.get('fingerprint') != p['fingerprint']):
            raise ValueError('采集范围已变化，请重新核对预计请求数')
        # A repeated click or an overlapping timer must not create a second paid round.
        for row in store.q('SELECT payload_json FROM discovery_hub_runs ORDER BY created_at DESC LIMIT 8'):
            ids = set(json.loads(row['payload_json']).get('jobs', []))
            if any(j['id'] in ids and j['status'] in {'queued', 'running'} for j in douk.jobs()):
                raise ValueError('已有榜单同步正在执行，请等待完成')
        cfg = p['settings']
        record = {'jobs': [], 'errors': [], 'calls': p['calls'], 'estimated_usd': p['estimated_usd'],
                  'queries': p['queries'], 'scheduled': scheduled}
        run_id = uuid.uuid4().hex
        store.x('INSERT INTO discovery_hub_runs VALUES(?,?,?)', (run_id, d.now().isoformat(), json.dumps(record)))
        for plan in p['plans']:
            try:
                req = {'endpoint': 'discovery', 'query': plan['query'], 'pages': plan['pages'], 'page_start': 1,
                       'confirmed': True, 'fingerprint': plan['fingerprint'], 'title': '榜单同步 · ' + plan['title']}
                if scheduled:
                    req['hub_revision'] = cfg['revision']
                record['jobs'].append(douk.submit(req)['id'])
            except ValueError as exc:
                record['errors'].append(str(exc))
                break
            finally:
                store.x('UPDATE discovery_hub_runs SET payload_json=? WHERE id=?', (json.dumps(record), run_id))
        return run_state(run_id)


def job_allowed(request):
    cfg = config()
    return cfg['enabled'] and cfg['revision'] == request.get('hub_revision')


def run_state(identity):
    rows = store.q('SELECT * FROM discovery_hub_runs WHERE id=?', (str(identity),))
    if not rows:
        raise ValueError('同步任务不存在')
    row = rows[0]
    payload = json.loads(row['payload_json'])
    jobs = {j['id']: j for j in douk.jobs()}
    items = [jobs[i] for i in payload['jobs'] if i in jobs]
    active = any(j['status'] in {'queued', 'running'} for j in items)
    status = 'running' if active else 'partial' if payload['errors'] or len(items)!=len(payload['jobs']) or any(j['status'] != 'completed' for j in items) else 'completed'
    return {'id': row['id'], 'created_at': row['created_at'], **payload, 'jobs': items, 'status': status}


def automation():
    cfg = config()
    plans = queries(cfg)
    calls = sum(1 if q['source']=='hot_live' else cfg['pages'] for q in plans)
    at = d.now()
    upcoming = [datetime.fromisoformat(f'{(at.date()+timedelta(days=day)).isoformat()}T{t}:00').replace(tzinfo=d.TZ)
                for day in [0, 1] for t in cfg['times']]
    return {'settings': cfg, 'next_at': min(t for t in upcoming if t > at).isoformat() if cfg['enabled'] else None,
            'calls_per_round': calls, 'daily_requests': calls * len(cfg['times']),
            'monthly_usd': round(calls * len(cfg['times']) * 30 * UNIT_USD, 3), 'price_date': PRICE_DATE,
            'recent_runs': [run_state(r['id']) for r in store.q('SELECT id FROM discovery_hub_runs ORDER BY created_at DESC LIMIT 8')],
            'recent_slots': store.q('SELECT kind,slot,status,payload_json FROM discovery_hub_slots ORDER BY slot DESC LIMIT 12')}


def all_rows(kind='works', include_native=False, until=None):
    backfill()
    args = [kind]
    where = 'kind=?'
    if not include_native:
        where += " AND source NOT IN ('native','native_hot')"
    if until:
        where += ' AND observed_at<?'
        args.append(until)
    rows = store.q('SELECT * FROM discovery_sightings WHERE ' + where + ' ORDER BY observed_at,snapshot_id,item_id', args)
    return rows


def decorate(observations, until=None):
    grouped = defaultdict(list)
    for row in observations:
        grouped[row['item_id']].append(row)
    entries = []
    names = {str(c['value']): c['label'] for c in d.categories()}
    for ident, rows in grouped.items():
        latest = rows[-1]
        if latest['kind']=='hot':
            live=[r for r in rows if r['source']=='hot_live']
            if live and datetime.fromisoformat(rows[-1]['observed_at'])-datetime.fromisoformat(live[-1]['observed_at'])<=timedelta(hours=36):
                latest=live[-1]
        item = json.loads(latest['value_json'])
        # Last seen keeps its own source and metric units; no field mixing across boards.
        item.update(id=ident, kind=latest['kind'], first_seen=rows[0]['observed_at'], last_seen=latest['observed_at'])
        item['scope_key']=latest['scope_key']
        item['query']=json.loads(latest['query_json'])
        boards = {}
        scopes = set()
        for r in rows:
            q = json.loads(r['query_json'])
            scopes.update(q.get('categories') or ([q['category']] if q.get('category') else []))
            boards[r['source']] = {'source': r['source'], 'title': d.SOURCES[r['source']][0],
                                   'rank': json.loads(r['value_json']).get('rank'), 'observed_at': r['observed_at']}
        item['boards'] = list(boards.values())
        item['categories'] = sorted(scopes)
        item['scope_names'] = [names.get(c, c) for c in sorted(scopes)]
        comparable = [r for r in rows if r['scope_key'] == latest['scope_key'] and r['observed_at']<latest['observed_at']]
        item['comparison'] = None
        if comparable:
            prev = comparable[-1]
            before = json.loads(prev['value_json'])
            delta = lambda key: item.get(key) - before[key] if item.get(key) is not None and before.get(key) is not None else None
            item['comparison'] = {'previous_at': prev['observed_at'], 'current_at': latest['observed_at'],
                                  'rank_change': -delta('rank') if delta('rank') is not None else None,
                                  'heat_change': delta('heat'), 'heat_percent': round(delta('heat') / before['heat'] * 100, 1) if delta('heat') is not None and before.get('heat') else None,
                                  'board_play_change': delta('board_play_count')}
        item['observations'] = len(rows)
        for field in ('cover', 'avatar'):
            saved = cached_image(item.get(field+'_url'))
            if saved:
                item[field+'_path'] = saved
        entries.append(item)
    import ip_radar
    ip_radar.overlay([r for r in entries if r['kind'] == 'works'], until=until)
    return account_states(entries)


def account_states(items):
    """Use stable IDs only. Matching nicknames never establish identity."""
    intelligence.init_account_tables()
    accounts = {r['sec_uid']: r for r in intelligence.list_tracked_accounts()}
    bindings = {r['aweme_id']: r['sec_uid'] for r in store.q('SELECT aweme_id,sec_uid FROM tracked_account_videos')}
    for item in items:
        aid = str(item.get('id') or item.get('aweme_id') or '')
        cached_uid, bound_uid = item.get('sec_uid') or '', bindings.get(aid, '')
        conflict = bool(cached_uid and bound_uid and cached_uid != bound_uid)
        uid = '' if conflict else cached_uid or bound_uid
        item['account_identity_conflict'] = conflict
        if uid:
            item['sec_uid'] = uid
        account = accounts.get(uid)
        item['account_monitor'] = {'sec_uid': uid, 'enabled': bool(account['enabled'])} if account else None
    return items


def monitor_account(data):
    """One click: add a cached identity, or resolve one video through the queue."""
    if not isinstance(data, dict) or set(data) - {'aweme_id', 'resolve_only'} or not isinstance(data.get('resolve_only', False), bool) or not re.fullmatch(r'\d{10,25}', str(data.get('aweme_id', ''))):
        raise ValueError('请选择已收录作品的作者')
    aid = str(data['aweme_id'])
    init()
    with LOCK:
        rows = store.q("SELECT value_json FROM discovery_sightings WHERE kind='works' AND item_id=? ORDER BY observed_at DESC LIMIT 1", (aid,))
        if rows:
            item = json.loads(rows[0]['value_json'])
        else:
            ref = next((r for r in creative.REFERENCES if r.get('aweme_id') == aid), None)
            if not ref:
                raise ValueError('尚未收录这条作品，请先采集')
            item = {'id': aid}
        item['id'] = aid
        import ip_radar
        ip_radar.overlay([item])
        account_states([item])
        if item['account_identity_conflict']:
            raise ValueError('作品的作者身份不一致，请核实后再添加')
        uid = item.get('sec_uid') or ''
        if uid:
            if not re.fullmatch(r'[A-Za-z0-9_=-]{12,}', uid):
                raise ValueError('缓存作者 ID 无效，未添加账号')
            with store._LOCK, store.get_conn() as conn:
                before = conn.execute('SELECT * FROM tracked_accounts WHERE sec_uid=?', (uid,)).fetchone()
                conn.execute('''INSERT INTO tracked_accounts(sec_uid,profile_url,nickname,avatar_path,follower_count,role,enabled,created_at)
                    VALUES(?,?,?,?,?,'peer',1,?) ON CONFLICT(sec_uid) DO UPDATE SET enabled=1''',
                    (uid, 'https://www.douyin.com/user/'+uid, item.get('account_name') or '',
                     item.get('avatar_path') or item.get('avatar_url'), item.get('fans'), store.now_str()))
            account = store.q('SELECT sec_uid,nickname,role,enabled FROM tracked_accounts WHERE sec_uid=?', (uid,))[0]
            return {'account': account, 'created': before is None, 'resumed': bool(before and not before['enabled']), 'job': None}
        if data.get('resolve_only'):
            raise ValueError('作者核实结果尚未保存，请到采集中心查看任务')
        # Reuse a pending identity lookup instead of charging for a double click.
        for job in douk.jobs():
            if job.get('endpoint') != 'discovery' or job['status'] not in {'queued', 'running'}:
                continue
            request = douk.read(douk.metadata(job['id']).parent/'request.json', {})
            query = request.get('query') or {}
            if query.get('source') == 'monitor' and query.get('aweme_ids') == [aid]:
                return {'job': job, 'account': None}
        query = {'source': 'monitor', 'aweme_id': aid}
        preview = d.plan(query)
        if not preview['allowed']:
            raise ValueError(preview['reason'])
        if preview['calls'] != 1:
            raise ValueError('账号快捷添加只允许核实一条作品')
        return {'job': d.submit({**query, 'confirmed': True, 'fingerprint': preview['fingerprint']}), 'account': None}


def library(data=None, export_all=False):
    data = data or {}
    kind = data.get('kind', 'works')
    if kind not in {'works', 'hot'}:
        raise ValueError('内容类型无效')
    native = data.get('provider') == 'all'
    observations = all_rows(kind, native)
    source = str(data.get('source') or '')
    category = str(data.get('category') or '')
    if source:
        if source not in d.SOURCES:
            raise ValueError('榜单来源无效')
        observations = [r for r in observations if r['source'] == source]
    if category:
        observations = [r for r in observations if category in list(map(str, json.loads(r['query_json']).get('categories') or [json.loads(r['query_json']).get('category')]))]
    rows = decorate(observations)
    focus = creative.validate(data.get('focus'))
    for row in rows:
        row['creative'] = creative.classify(row)
    rows = [row for row in rows if creative.matches(row, focus)]
    data_status = data.get('data_status', 'all')
    if data_status not in {'all', 'available', 'pending', 'watching'}:
        raise ValueError('数据状态无效')
    if data_status != 'all':
        rows = [r for r in rows if (data_status == 'available' and r.get('play_count') is not None) or
                (data_status == 'pending' and r.get('play_count') is None) or
                (data_status == 'watching' and r.get('radar_watching'))]
    word = str(data.get('keyword') or '').strip().casefold()
    if len(word) > 120:
        raise ValueError('搜索关键词过长')
    if word:
        rows = [r for r in rows if word in (r.get('title', '')+' '+r.get('account_name', '')+' '+r['id']).casefold()]
    days = d.integer(data.get('days', 0), 0, 365, '发布时间')
    if days:
        cutoff = (d.now()-timedelta(days=days)).isoformat()
        rows = [r for r in rows if r.get('published_at') and r['published_at'] >= cutoff]
    if data.get('max_fans') not in (None, ''):
        maximum = d.integer(data['max_fans'], 0, 1000000000, '粉丝上限')
        rows = [r for r in rows if r.get('fans') is not None and r['fans'] <= maximum]
    sort = data.get('sort', 'seen')
    if sort == 'quality':
        rows.sort(key=lambda r: (r.get('play_count') is not None, r.get('detail_updated_at') is not None,
                                  r.get('play_count') or 0, r.get('digg_count') or 0), reverse=True)
    elif sort == 'rank':
        # Numeric rank belongs to one board; do not present unlike ranks as a global rank.
        rows.sort(key=lambda r: (r.get('source', ''), r.get('rank') if r.get('rank') is not None else float('inf')))
    else:
        fields = {'seen': 'last_seen', 'new': 'published_at', 'plays': 'board_play_count', 'likes': 'digg_count', 'heat': 'heat',
                  'video_plays': 'play_count', 'comments': 'comment_count', 'saves': 'collect_count', 'shares': 'share_count',
                  'updated': 'metrics_updated_at', 'fans': 'fans', 'author': 'account_name', 'title': 'title'}
        if sort == 'rising':
            rows.sort(key=lambda r: ((r.get('comparison') or {}).get('rank_change') is not None, (r.get('comparison') or {}).get('rank_change') or 0), reverse=True)
        elif sort in fields:
            field = fields[sort]
            text_field = field in {'last_seen', 'published_at', 'metrics_updated_at', 'account_name', 'title'}
            missing = [r for r in rows if r.get(field) is None]
            rows = sorted([r for r in rows if r.get(field) is not None], key=lambda r: r[field], reverse=data.get('direction', 'desc') != 'asc')+missing
        else:
            raise ValueError('排序无效')
    size = d.integer(data.get('page_size', 20), 1, 100, '每页数量')
    page = d.integer(data.get('page', 1), 1, 1000000, '页码')
    total = len(rows)
    page = min(page, max(1, (total+size-1)//size))
    return {'items': rows if export_all else rows[(page-1)*size:page*size], 'total': total, 'page': page, 'page_size': size,
            'last_updated': max((r['last_seen'] for r in rows), default=None), 'query': data}


def history(kind, ident):
    if kind not in {'works', 'hot'} or len(str(ident)) > 240:
        raise ValueError('内容标识无效')
    backfill()
    rows = store.q('SELECT * FROM discovery_sightings WHERE kind=? AND item_id=? ORDER BY observed_at,snapshot_id', (kind, str(ident)))
    if not rows:
        raise ValueError('尚未收录这条内容')
    item = decorate(rows)[0]
    item['creative'] = creative.classify(item)
    import metric_history
    return {'item': item, 'metric_history': metric_history.video(str(ident)) if kind == 'works' else [],
            'history': [{'observed_at': r['observed_at'], 'source': r['source'], 'source_title': d.SOURCES[r['source']][0],
                                     'query': json.loads(r['query_json']), 'scope_key': r['scope_key'], 'snapshot_id': r['snapshot_id'],
                                     **{k: json.loads(r['value_json']).get(k) for k in ['rank', 'heat', 'board_play_count', 'digg_count', 'comment_count', 'share_count', 'collect_count']}}
                                    for r in reversed(rows)]}


def report_bounds(kind, date=None):
    if kind not in {'daily', 'weekly'}:
        raise ValueError('报告类型无效')
    try:
        end_day = datetime.strptime(date or d.now().date().isoformat(), '%Y-%m-%d').date()
    except ValueError:
        raise ValueError('报告日期无效') from None
    if end_day > d.now().date():
        raise ValueError('不能生成未来日期的报告')
    start = datetime.combine(end_day-timedelta(days=6 if kind == 'weekly' else 0), datetime.min.time(), tzinfo=d.TZ)
    end = min(datetime.combine(end_day+timedelta(days=1), datetime.min.time(), tzinfo=d.TZ), d.now())
    return start, end


def generate_report(data=None):
    data = data or {}
    kind = data.get('kind', 'daily')
    start, end = report_bounds(kind, data.get('date'))
    start_s, end_s = start.isoformat(), end.isoformat()
    works_obs = all_rows('works', until=end_s)
    hot_obs = all_rows('hot', until=end_s)
    works = [r for r in decorate(works_obs, until=end_s) if r['last_seen'] >= start_s]
    hot = [r for r in decorate(hot_obs, until=end_s) if r['last_seen'] >= start_s]
    focus = creative.validate(data.get('focus'))
    if focus != 'all':
        for item in works + hot:
            item['creative'] = creative.classify(item)
        works = [r for r in works if creative.matches(r, focus)]
        hot = [r for r in hot if creative.matches(r, focus)]
        work_ids, hot_ids = {r['id'] for r in works}, {r['id'] for r in hot}
        works_obs = [r for r in works_obs if r['item_id'] in work_ids]
        hot_obs = [r for r in hot_obs if r['item_id'] in hot_ids]
    fresh = [r for r in works if r['first_seen'] >= start_s]
    rising = [r for r in hot if r['comparison'] and (r['comparison'].get('rank_change') or 0) > 0]
    rising.sort(key=lambda r: r['comparison']['rank_change'], reverse=True)
    hot.sort(key=lambda r: (r.get('source') == 'hot_live', r.get('heat') or 0), reverse=True)
    references = sorted(works, key=lambda r: (r.get('source') == 'video_total', r.get('board_play_count') or 0, r.get('digg_count') or 0), reverse=True)
    groups = defaultdict(list)
    for row in works:
        for term in set(re.findall(r'#([^\s#@，,。!！?？]{2,20})', row['title'])):
            groups[(term, row['source'], row['scope_key'])].append(row)
    keywords = []
    for (word, source, scope_key), samples in groups.items():
        if len(samples) < 2:
            continue
        plays = [r['board_play_count'] for r in samples if r.get('board_play_count') is not None]
        keywords.append({'word': word, 'source': source, 'count': len(samples), 'median_board_plays': statistics.median(plays) if plays else None,
                         'scope_key':scope_key,'query':samples[0]['query'],
                         'samples': [r['id'] for r in samples[:5]]})
    keywords.sort(key=lambda r: r['count'], reverse=True)
    shapes = [('一口气看完', r'一口气|合集|完整版'), ('假设与脑洞', r'如果|假如|当.+时|要是'),
              ('反转与悬念', r'没想到|竟然|结果|直到|真相|以为'), ('过程与教程', r'教程|如何|怎么|步骤|实测|制作|工作流')]
    if focus != 'all':
        shapes = [('分集连载', r'第[一二三四五六七八九十百\d]+[集话]|先导片|预告'),
                  ('一口气看完', r'一口气|合集|完整版'), ('假设与脑洞', r'如果|假如|当.+时|要是'),
                  ('反转与悬念', r'没想到|竟然|结果|直到|真相|以为')]
    structures = []
    for label, pattern in shapes:
        samples = [r for r in works if re.search(pattern, r['title'])]
        if samples:
            structures.append({'label': label, 'count': len(samples), 'samples': [r['id'] for r in samples[:5]]})
    ideas = []
    creative_candidate = lambda r: r.get('allow_publish') is not False and (r.get('category') in {'科技','娱乐','游戏','动漫','影视','文化','生活'} or re.search(r'AI|人工智能|动漫|电影|短剧|游戏|音乐|宠物|搞笑|梗|挑战|机器人|动画|演唱会|猫|狗|穿越|变装|反转',r['title'],re.I))
    candidates = [r for r in rising if creative_candidate(r)] or [r for r in hot if creative_candidate(r)]
    candidates.sort(key=lambda r:(bool(re.search(r'AI|智能|机器人|动画|短剧',r['title'],re.I)),r.get('source')=='hot_live',r.get('heat') or 0),reverse=True)
    for index, topic in enumerate(candidates[:6]):
        title = topic['title']
        sample = references[index % len(references)] if references else None
        if re.search(r'舞|转场|变装|挑战',title):
            form,draft,outline='AI 转场挑战',f'回应“{title}”：用同一个动作串起三种风格','选一个能立即辨认的动作，依次切换三种角色或场景，尾帧接回首帧形成循环。先在热点原视频中核对动作与音乐。'
        elif re.search(r'AI|智能|机器人|科技',title,re.I):
            form,draft,outline='科技拟人短片',f'把“{title}”的差异，拍成一场看得见的较量','将热点里的不同能力拟人成角色，给它们同一个具体任务，用可见的结果形成反差。涉及产品能力的内容先核对事实。'
        elif re.search(r'短剧|职业|行业|职场',title):
            form,draft,outline='职业反转短剧',f'借“{title}”做一条三十秒的职业反转','选一个熟悉的职业，开场给出一个具体难题，中段放大日常冲突，结尾用职业技能完成反转。可以采用 AI 漫剧表现。'
        elif re.search(r'猫|狗|宠物|动物',title):
            form,draft,outline='宠物拟人喜剧',f'如果“{title}”里的动物会说话','抓住原热点的一个动作或表情，给动物一个清楚的目标，用一次误解和一次反转完成短情节。'
        else:
            form,draft,outline='反差情景短片',f'围绕“{title}”，找一个可以反转的日常场景','只取热点里一个清楚的冲突，将它放进两个角色的短场景，用一次选择完成反转；先核对热点语境与原始内容。'
        ideas.append({'topic_id': topic['id'], 'topic': title, 'format': form, 'draft': draft, 'outline': outline,
                      'heat': topic.get('heat'), 'rank': topic.get('rank'), 'comparison': topic.get('comparison'),
                      'reference_id': sample['id'] if sample else None,
                      'reference_title': sample['title'] if sample else None,
                      'note': '创意草案；参考视频仅用于学习表达形式，未验证与该热点相关。'})
    sources = Counter(r['source'] for r in works_obs+hot_obs if start_s <= r['observed_at'] < end_s)
    valid_sources=[s for s,v in d.SOURCES.items() if v[2] in {'works','hot'} and s not in {'native','native_hot'}]
    snapshots = store.q('SELECT id,source,query_json,created_at,status FROM discovery_snapshots WHERE created_at>=? AND created_at<? AND source IN ('+','.join('?' for _ in valid_sources)+') ORDER BY created_at', (start_s, end_s,*valid_sources))
    daily=[]
    for offset in range((end.date()-start.date()).days+1):
        date=(start.date()+timedelta(days=offset)).isoformat()
        if date>=end_s[:10] and end.hour==0 and end.minute==0:continue
        queries_count=sum(1 for s in snapshots if s['created_at'][:10]==date)
        daily.append({'date':date,'queries':queries_count,
                      'works':len({r['item_id'] for r in works_obs if r['observed_at'][:10]==date}) if queries_count else None,
                      'hot':len({r['item_id'] for r in hot_obs if r['observed_at'][:10]==date}) if queries_count else None})
    small_accounts=[{**r,'like_fan_ratio':round(r['digg_count']/r['fans'],2)} for r in works if r.get('fans') and r['fans']<=100000 and (r.get('digg_count') or 0)>=5000]
    small_accounts.sort(key=lambda r:r['like_fan_ratio'],reverse=True)
    anchor_date=data.get('date') or d.now().date().isoformat()
    payload = {'kind': kind, 'start_at': start_s, 'end_at': end_s, 'generated_at': d.now().isoformat(),
               'title': ('选题日报' if kind == 'daily' else '选题周报')+' · '+anchor_date,
               'coverage': {'works': len(works), 'hot': len(hot), 'new_works': len(fresh), 'rising_hot': len(rising),
                            'snapshots': len(snapshots), 'daily':daily,'sources': [{'source': k, 'title': d.SOURCES[k][0], 'observations': n} for k, n in sources.items()]},
               'rising': rising[:15], 'hot': hot[:15], 'references': references[:15], 'keywords': keywords[:20],
               'structures': structures, 'ideas': ideas, 'small_accounts':small_accounts[:10],'snapshots': snapshots,
               'method': '报告只使用已保存的公开样本。排名、热度变化仅比较同一榜单和相同采集范围；首次采集无涨幅。关键词频次与标题结构属于样本描述，不能证明推流原因或保证播放量。'}
    ident = kind+'-'+anchor_date
    if focus != 'all':
        # A separate namespace keeps previously saved general reports intact.
        ident += '-'+focus
        import ip_radar
        radar = ip_radar.report(start_s, end_s)
        payload.update(focus=focus, anchor_date=anchor_date, ideas=[], radar=radar,
                       title=('AI 内容日报' if kind == 'daily' else 'AI 内容周报')+' · '+anchor_date,
                       opportunities=creative.opportunities(works),
                       lanes=[{**lane, 'works': len([r for r in works if lane['id'] in r['creative']['lanes']]),
                               'accounts': len({r.get('sec_uid') or r.get('account_name') or r['id'] for r in works if lane['id'] in r['creative']['lanes']})}
                              for lane in creative.LANES])
    payload['id'] = ident
    store.x('INSERT OR REPLACE INTO discovery_reports VALUES(?,?,?,?,?,?)',
            (ident, kind, start_s, end_s, payload['generated_at'], json.dumps(payload, ensure_ascii=False)))
    return payload


def reports(identity=None):
    init()
    if identity:
        rows = store.q('SELECT payload_json FROM discovery_reports WHERE id=?', (str(identity),))
        if not rows:
            raise ValueError('报告不存在')
        payload = json.loads(rows[0]['payload_json'])
        account_states(payload.get('references', []) + [r['work'] for r in payload.get('opportunities', [])])
        return payload
    return {'items': store.q('SELECT id,kind,start_at,end_at,generated_at FROM discovery_reports ORDER BY generated_at DESC LIMIT 120')}


def tick(at=None):
    at = at or d.now()
    cfg = config()
    with LOCK:
        due = []
        if cfg['enabled']:
            sync_due=[]
            for time in cfg['times']:
                moment = datetime.combine(at.date(), datetime.strptime(time, '%H:%M').time(), tzinfo=d.TZ)
                if moment <= at and moment.isoformat() >= cfg['enabled_at']:
                    sync_due.append(moment)
            if sync_due:due.append(('sync',max(sync_due)))
        moment = datetime.combine(at.date(), datetime.strptime(cfg['report_time'], '%H:%M').time(), tzinfo=d.TZ)
        if moment <= at:
            if cfg['daily_report']:
                due.append(('daily', moment))
            if cfg['weekly_report'] and at.weekday() == 0:
                due.append(('weekly', moment))
        for kind, moment in due:
            if kind!='sync' and any(r['status']=='running' for r in [run_state(row['id']) for row in store.q('SELECT id FROM discovery_hub_runs ORDER BY created_at DESC LIMIT 8')]):
                continue
            slot = moment.isoformat()
            with store._LOCK, store.get_conn() as conn:
                inserted=conn.execute('INSERT OR IGNORE INTO discovery_hub_slots VALUES(?,?,?,?)', (kind,slot,'claimed','{}')).rowcount
            if not inserted:continue
            try:
                if kind == 'sync':
                    result = submit_sync(cfg, scheduled=True)
                    payload = {'run_id': result['id']}
                else:
                    date = (at.date()-timedelta(days=1) if kind == 'weekly' else at.date()).isoformat()
                    payload = {'report_id': generate_report({'kind': kind, 'date': date, 'focus': 'ai'})['id']}
                store.x('UPDATE discovery_hub_slots SET status=?,payload_json=? WHERE kind=? AND slot=?', ('completed', json.dumps(payload), kind, slot))
            except Exception as exc:
                # No paid retries after restart, partial submission, pause or quota failure.
                store.x('UPDATE discovery_hub_slots SET status=?,payload_json=? WHERE kind=? AND slot=?', ('blocked', json.dumps({'error': str(exc)[:200]}), kind, slot))


def export_library(data):
    rows = library(data, export_all=True)['items']
    out = io.StringIO();writer = csv.writer(out)
    fields = ['title', 'account_name', 'published_at', 'rank', 'board_play_count', 'digg_count', 'comment_count', 'collect_count', 'share_count', 'fans', 'heat', 'first_seen', 'last_seen', 'source', 'url']
    writer.writerow(['标题', '作者', '发布时间', '榜内排名', '榜单窗口播放', '点赞', '评论', '收藏', '分享', '粉丝', '热度', '首次收录', '最近采集', '最近来源', '原视频'])
    for r in rows:
        writer.writerow(["'"+v if isinstance(v, str) and v.startswith(('=', '+', '-', '@')) else v for v in [r.get(k, '') for k in fields]])
    return out.getvalue().encode('utf-8-sig')


def export_report(identity):
    r = reports(identity)
    esc = lambda value: html.escape(str(value or ''))
    if r.get('radar'):
        body = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(r['title'])+'</title><style>body{font:14px/1.7 system-ui;max-width:1120px;margin:32px auto;padding:0 16px;color:#24394c}h1{font-size:23px}h2{font-size:17px}table{border-collapse:collapse;width:100%}td,th{padding:10px;text-align:left;border-bottom:1px solid #dce4eb}small{display:block;color:#748594}a{color:#236c91}</style><h1>'+esc(r['title'])+'</h1><p>'+esc(r['start_at'])+' — '+esc(r['end_at'])+'</p>'
        for title, groups in [('观测增长', r['radar']['rising']), ('新收录 IP', r['radar']['new_ips']), ('近期参考', [g for g in r['radar']['candidates'] if g['recent_works']])]:
            body += '<h2>'+title+'</h2><table><tr><th>IP / 作者</th><th>识别线索</th><th>近期作品</th><th>代表作播放</th><th>点赞</th><th>日均增长</th></tr>'
            for g in groups:
                w = g['representative']
                cell = lambda v: esc(v) if v is not None else '—'
                body += '<tr><td><a href="'+esc(w['url'])+'">'+esc(g['name'])+'</a><small>'+esc(g['account_name'])+'</small></td>'+''.join('<td>'+cell(v)+'</td>' for v in (g['evidence'], g['recent_works'], w.get('play_count'), w.get('digg_count'), g['peak_daily_growth']))+'</tr>'
            body += '</table>'
        return (body+'</html>').encode('utf-8')
    ideas = ''.join('<article><h3>'+esc(i['draft'])+'</h3><p>'+esc(i['outline'])+'</p><p>热点依据：'+esc(i['topic'])+'；热度 '+esc(i['heat'])+'</p>'+('<p><a href="https://www.douyin.com/video/'+esc(i['reference_id'])+'">形式参考：'+esc(i['reference_title'])+'</a></p>' if i['reference_id'] else '')+'</article>' for i in r['ideas'])
    if r.get('focus'):
        ideas = ''.join('<article><h3>'+esc(i['label'])+' · <a href="'+esc(i['work']['url'])+'">'+esc(i['work']['title'])+'</a></h3><p>'+esc(i['study'])+'</p></article>' for i in r['opportunities'])
    samples = ''.join('<tr><td><a href="'+esc(i['url'])+'">'+esc(i['title'])+'</a></td><td>'+esc(i.get('account_name'))+'</td><td>'+esc(i.get('board_play_count'))+'</td><td>'+esc(i.get('digg_count'))+'</td></tr>' for i in r['references'])
    def table(headers, rows):
        cell=lambda v:html.escape(str(v)) if v is not None else '—'
        return '<table><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+cell(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</table>'
    hot=table(['热点','来源','热度','排名变化'],[(i['title'],d.SOURCES[i['source']][0],i.get('heat'),(i.get('comparison') or {}).get('rank_change')) for i in (r['rising'] or r['hot'])])
    keywords=table(['标签','同范围样本','窗口播放中位数'],[(i['word'],i['count'],i['median_board_plays']) for i in r['keywords']])
    daily=table(['日期','查询轮次','去重作品','热点'],[(i['date'],i['queries'],i['works'],i['hot']) for i in r['coverage'].get('daily',[])])
    small=table(['作品','作者粉丝','点赞','点赞/粉丝'],[(i['title'],i['fans'],i['digg_count'],i['like_fan_ratio']) for i in r.get('small_accounts',[])])
    body = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(r['title'])+'</title><style>body{font:15px/1.7 system-ui;max-width:1080px;margin:32px auto;color:#23394b;padding:0 20px}h1{font-size:24px}h3{font-size:17px}article{border-bottom:1px solid #dce4eb;padding:12px 0}table{border-collapse:collapse;width:100%}td,th{padding:9px;border-bottom:1px solid #dce4eb;text-align:left}a{color:#256f95}small{color:#697e8d}@media print{a{color:inherit}}</style><h1>'+esc(r['title'])+'</h1><small>'+esc(r['start_at'])+' 至 '+esc(r['end_at'])+'</small><p>'+esc(r['method'])+'</p><h2>采集概览</h2>'+daily+'<h2>可试选题</h2>'+ideas+'<h2>热点观察</h2>'+hot+'<h2>反复出现的标签</h2>'+keywords+'<h2>小账号作品样本</h2>'+small+'<h2>参考作品</h2><table><tr><th>作品</th><th>作者</th><th>榜单播放</th><th>点赞</th></tr>'+samples+'</table><h2>完整报告数据</h2><details><summary>展开 JSON</summary><pre style="white-space:pre-wrap">'+esc(json.dumps(r, ensure_ascii=False, indent=2))+'</pre></details></html>'
    return body.encode('utf-8')
