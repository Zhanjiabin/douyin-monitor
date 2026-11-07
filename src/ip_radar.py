"""A bounded public-data discovery loop, with explicit identity and metric evidence.

Searches find candidates; video details establish author/series identity; single
statistics calls establish comparable observations. None of these imply that a
video has been watched or that Douyin's recommendation algorithm is known.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import hashlib
import json
import math
import re
import threading
import uuid

import creative_focus as creative
import discovery as d
import douk
import metric_history as mh
import playback
import store
import tikhub

LOCK = threading.RLock()
DETAIL = '/api/v1/douyin/app/v3/fetch_one_video_v3'
STATISTICS = '/api/v1/douyin/app/v3/fetch_video_statistics'
METRICS = mh.METRICS
DEFAULTS = {
    'enabled': False, 'interval_hours': 6, 'recent_days': 14,
    'refresh_hours': 12, 'watch_limit': 12, 'cycle_calls': 5,
    'daily_calls': 30, 'expand_keywords': True,
    'keywords': ['AI漫剧', 'AI真人短剧', 'AI萌宠', 'AI电影',
                 '悬疑漫剧', 'AI短剧', 'AI动物故事', 'AI中式恐怖',
                 '末日漫剧', 'AI剧情短片', 'AI猫咪连续剧', 'AI科幻短片'],
    'revision': '', 'enabled_at': '',
}
COMMENTARY = re.compile(r'解说|盘点|混剪|搬运|转载|反应视频|观后感|影评|reaction', re.I)
COMPILATION = re.compile(r'一口气|合集|完整版|全集')
EPISODE = re.compile(r'第\s*[零一二三四五六七八九十百两\d]+\s*[集话章]|[Ee][Pp]\.?\s*\d+')
GENERIC = re.compile(r'^(?:ai|aigc|AI.*(?:计划|创作|视频|萌宠|漫剧|短剧)|抖音.*|.*(?:教程|工具|工作流|成长计划|扶持计划|新星计划|激励计划)|'
                     r'可爱|萌宠|短剧|漫剧|原创|动画|剧情|影视|治愈|治愈系|搞笑|恐怖|末日|悬疑|小说|动漫|国漫|推荐|热门|上热门|seko|即梦.*|可灵.*|RoboNeo|LibTV.*)$', re.I)


def init():
    for sql in (
        'CREATE TABLE IF NOT EXISTS radar_settings(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS radar_details(aweme_id TEXT NOT NULL,observed_at TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(aweme_id,observed_at))',
        'CREATE TABLE IF NOT EXISTS radar_attempts(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,job_id TEXT NOT NULL,kind TEXT NOT NULL,target TEXT NOT NULL,status TEXT NOT NULL,error TEXT)',
        'CREATE INDEX IF NOT EXISTS radar_attempt_target ON radar_attempts(kind,target,created_at)',
        'CREATE TABLE IF NOT EXISTS radar_slots(slot TEXT PRIMARY KEY,revision TEXT NOT NULL,job_id TEXT,status TEXT NOT NULL,error TEXT)',
        'CREATE TABLE IF NOT EXISTS radar_pins(aweme_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL,created_at TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS radar_auto_watch(aweme_id TEXT PRIMARY KEY,added_at TEXT NOT NULL,expires_at TEXT NOT NULL)',
    ):
        store.x(sql)
    mh.init()


def stamp(value=None):
    return mh.timestamp(value or d.now().isoformat())


def at(value):
    return datetime.fromisoformat(stamp(value))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def config():
    init()
    rows = store.q('SELECT value FROM radar_settings WHERE id=1')
    return {**DEFAULTS, **(json.loads(rows[0]['value']) if rows else {})}


def validate(data, old=None):
    if not isinstance(data, dict) or set(data) - set(DEFAULTS) - {'confirmed'}:
        raise ValueError('AI 监测设置格式无效')
    cfg = {**(old or config()), **{k: v for k, v in data.items() if k not in {'confirmed', 'revision', 'enabled_at'}}}
    for key in ('enabled', 'expand_keywords'):
        if not isinstance(cfg[key], bool):
            raise ValueError('开关格式无效')
    for key, lo, hi in [('interval_hours', 1, 168), ('recent_days', 1, 90), ('refresh_hours', 1, 168),
                        ('watch_limit', 1, 50), ('cycle_calls', 1, 20), ('daily_calls', 1, 200)]:
        cfg[key] = d.integer(cfg[key], lo, hi, key)
    if not isinstance(cfg['keywords'], list):
        raise ValueError('关键词格式无效')
    cfg['keywords'] = list(dict.fromkeys(str(v).strip() for v in cfg['keywords'] if str(v).strip()))
    if not 4 <= len(cfg['keywords']) <= 40 or any(len(v) > 40 for v in cfg['keywords']):
        raise ValueError('请设置 4–40 个关键词，每个不超过 40 字')
    if cfg['cycle_calls'] > cfg['daily_calls']:
        raise ValueError('每轮上限不能超过 AI 监测每日上限')
    return cfg


def save_config(data):
    with LOCK:
        old = config()
        cfg = validate(data, old)
        if cfg['enabled'] and data.get('confirmed') is not True:
            raise ValueError('请确认按所设请求上限开启自动监测')
        cfg['revision'] = uuid.uuid4().hex
        cfg['enabled_at'] = old['enabled_at'] if old['enabled'] and cfg['enabled'] else stamp() if cfg['enabled'] else ''
        store.x('INSERT OR REPLACE INTO radar_settings VALUES(1,?)', (json.dumps(cfg, ensure_ascii=False),))
    return status()


def usage():
    init()
    return store.q('SELECT COUNT(*) n FROM radar_attempts WHERE created_at>=?', (stamp()[:10],))[0]['n']


def clean_name(value):
    name = re.sub(r'\s+', '', value).strip('《》【】「」『』<>：:·-—_#，,。.！!？?')
    name = re.split(r'第[零一二三四五六七八九十百两\d]+[季集话章]|[Ee][Pp]\.?\d+|合集\d*', name)[0]
    name = name.strip('：:·-—_')
    if name in {'商城补贴', '原创动画', '漫剧推荐', '视频同款', '工具推荐'}:
        return ''
    if name.startswith(('本作品', '本视频', '点击', '欢迎', '关注')):
        return ''
    return name if 2 <= len(name) <= 24 and not GENERIC.fullmatch(name) else ''


def names(item):
    title = item.get('title') or ''
    result = []
    mix = item.get('mix_name')
    if mix and clean_name(mix):
        result.append((clean_name(mix), '合集名称'))
    for value in re.findall(r'[《【「『]([^》】」』\n]{2,40})[》】」』]', title):
        name = clean_name(value)
        if name:
            result.append((name, '标题剧名'))
    prefix = re.split(r'[#@\n]', title)[0]
    match = EPISODE.search(prefix)
    if match:
        candidate = clean_name(prefix[:match.start()])
        if candidate:
            result.append((candidate, '分集标题'))
    for value in re.findall(r'#([^\s#@，,。!！?？|｜]{2,32})', title):
        name = clean_name(value)
        if name and not re.search(r'喊话|复刻|卡点|同款|唱歌|创作|生成|制作|模型|推荐|补贴|优惠|模仿|原创动画|新星|热门', name, re.I):
            result.append((name, '作品标签'))
    return list(dict.fromkeys(result))


def content_type(item):
    title = item.get('title') or ''
    if COMMENTARY.search(title):
        return 'commentary', '解说 / 二创线索'
    if COMPILATION.search(title):
        return 'compilation', '合集 / 完整版'
    return 'narrative', '叙事作品线索'


def growth(events, now=None):
    """At least one hour, same source/scope, actual elapsed time; no window sums."""
    now = now or d.now()
    groups = defaultdict(list)
    for e in events:
        if e.get('play_count') is not None and at(e['observed_at']) <= now:
            groups[(e['source'], e['scope'])].append(e)
    choices = []
    for key, rows in groups.items():
        rows.sort(key=lambda e: (e['observed_at'], e.get('id', 0)))
        latest = rows[-1]
        previous = [e for e in rows[:-1] if 1 <= (at(latest['observed_at'])-at(e['observed_at'])).total_seconds()/3600 <= 168]
        if not previous:
            continue
        # Prefer a day-long comparison instead of amplifying a one-hour burst.
        before = min(previous, key=lambda e: abs((at(latest['observed_at'])-at(e['observed_at'])).total_seconds()/3600-24))
        hours = (at(latest['observed_at'])-at(before['observed_at'])).total_seconds()/3600
        delta = latest['play_count']-before['play_count']
        choices.append({'source': key[0], 'scope': key[1], 'before_at': before['observed_at'], 'observed_at': latest['observed_at'],
                        'hours': round(hours, 2), 'delta': delta, 'per_day': round(delta*24/hours) if delta >= 0 else None,
                        'percent': round(delta/before['play_count']*100, 1) if before['play_count'] and delta >= 0 else None,
                        'status': 'correction' if delta < 0 else 'measured', 'stale': (now-at(latest['observed_at'])).total_seconds() > 48*3600})
    return max(choices, key=lambda e: e['observed_at'], default=None)


def overlay(items, until=None):
    """Overlay each known field; never erase another field with an omitted value."""
    init()
    ids = {str(i.get('id') or i.get('aweme_id') or '') for i in items}
    if not ids:
        return items
    limit = stamp(until) if until else None
    details = {}
    for r in store.q('SELECT * FROM radar_details ORDER BY observed_at'):
        if r['aweme_id'] in ids and (not limit or r['observed_at'] < limit):
            details[r['aweme_id']] = r
    events = defaultdict(list)
    for r in store.q('SELECT * FROM video_metric_events ORDER BY observed_at,id'):
        if r['aweme_id'] in ids and (not limit or r['observed_at'] < limit):
            events[r['aweme_id']].append(r)
    attempts = {}
    for r in store.q("SELECT * FROM radar_attempts WHERE kind='statistics' ORDER BY created_at"):
        if r['target'] in ids and (not limit or r['created_at'] < limit):
            attempts[r['target']] = r
    pinned = {r['aweme_id'] for r in store.q('SELECT aweme_id FROM radar_pins WHERE enabled=1')}
    watched = {r['aweme_id'] for r in store.q('SELECT aweme_id FROM radar_auto_watch WHERE expires_at>?', (stamp(),))}
    for item in items:
        aid = str(item.get('id') or item.get('aweme_id') or '')
        field_meta = {k: {'source': item.get('source', 'search'), 'scope': 'search_or_board_snapshot',
                          'observed_at': stamp(item['last_seen'])}
                      for k in METRICS if item.get(k) is not None and item.get('last_seen')}
        detail = details.get(aid)
        if detail:
            value = json.loads(detail['value'])
            for key in ('title', 'sec_uid', 'account_name', 'fans', 'avatar_url', 'cover_url', 'avatar_path', 'cover_path', 'duration', 'published_at', 'mix_id', 'mix_name'):
                if value.get(key) not in (None, ''):
                    item[key] = value[key]
            item['identity_verified'] = bool(value.get('sec_uid'))
            item['detail_updated_at'] = detail['observed_at']
        for event in events[aid]:
            for key in METRICS:
                if event.get(key) is not None and (key not in field_meta or event['observed_at'] >= field_meta[key]['observed_at']):
                    item[key] = event[key]
                    field_meta[key] = {k: event[k] for k in ('source', 'scope', 'observed_at')}
        item['metric_fields'] = field_meta
        item['metrics_updated_at'] = max((v['observed_at'] for v in field_meta.values()), default=item.get('last_seen'))
        item['play_updated_at'] = (field_meta.get('play_count') or {}).get('observed_at')
        item['play_scope'] = (field_meta.get('play_count') or {}).get('scope')
        item['growth'] = growth(events[aid], at(limit) if limit else d.now())
        item['enrichment'] = 'complete' if item.get('play_count') is not None and detail else 'detail' if detail else 'pending'
        attempt = attempts.get(aid)
        item['last_statistics_attempt'] = attempt
        item['radar_pinned'] = aid in pinned
        item['radar_watching'] = aid in pinned or aid in watched
        item['play_status'] = 'available' if item.get('play_count') is not None else 'failed' if attempt and attempt['status'] == 'failed' else 'not_returned' if attempt and attempt['status'] == 'completed' else 'pending'
    return items


def works(until=None):
    import discovery_hub as hub
    hub.backfill()
    # Use chronological sightings, so a historical report cannot see future titles.
    rows = hub.decorate(hub.all_rows('works', until=until), until=until)
    for row in rows:
        row['creative'] = creative.classify(row)
    return [r for r in rows if creative.matches(r, 'ai')]


def age_days(item, now=None):
    if not item.get('published_at'):
        return None
    try:
        return ((now or d.now())-at(item['published_at'])).total_seconds()/86400
    except (ValueError, TypeError):
        return None


def potential(item, now=None):
    age = age_days(item, now)
    likes = max(0, item.get('digg_count') or 0)
    fans = item.get('fans') or 0
    return math.log1p(likes)*3 + min(4, likes/max(100, fans)) - math.log1p(max(0, age or 0))


def groups(items=None, now=None):
    items = works() if items is None else items
    now = now or d.now()
    # Repeated distinctive tags help identify an unknown series, scoped to an
    # author candidate. A display name alone is never treated as verified ID.
    tags = Counter()
    for item in items:
        author = item.get('sec_uid') or digest([item.get('account_name'), item.get('avatar_url')])
        for name, evidence in names(item):
            if evidence == '作品标签':
                tags[(author, name)] += 1
    bucket = defaultdict(list)
    evidence_by = {}
    for item in items:
        author = item.get('sec_uid') or 'unverified-'+digest([item.get('account_name'), item.get('avatar_url')])
        options = names(item)
        named = next(((n, e) for n, e in options if e != '作品标签'), None)
        if not named:
            named = next(((n, e) for n, e in options if tags[(author.removeprefix('unverified-'), n)] >= 2), None)
        # Recurring characters without a titled series remain creator candidates.
        name, evidence = named or (item.get('account_name') or '待核实作者', '作者创作线索')
        kind, label = content_type(item)
        key = digest([author, name.casefold(), kind])
        bucket[key].append(item)
        evidence_by[key] = (name, evidence, kind, label)
    result = []
    for key, members in bucket.items():
        name, evidence, kind, label = evidence_by[key]
        ordered = sorted(members, key=lambda r: (r.get('published_at') or '', r['id']), reverse=True)
        recent = [r for r in ordered if age_days(r, now) is not None and 0 <= age_days(r, now) <= 14]
        measured = [r for r in recent if r.get('growth') and r['growth']['status'] == 'measured' and not r['growth']['stale']]
        best = max(measured, key=lambda r: r['growth']['per_day']) if measured else max(recent or ordered, key=lambda r: potential(r, now))
        verified = bool(best.get('sec_uid'))
        best_age = age_days(best, now)
        # Compare only verified authors and roughly similar publishing ages.
        baseline = [r['digg_count'] for r in items if verified and r.get('sec_uid') == best['sec_uid'] and
                    r['id'] != best['id'] and r.get('digg_count') is not None and best_age is not None and
                    age_days(r, now) is not None and abs(age_days(r, now)-best_age) <= max(3, best_age*.5)]
        import statistics
        median_likes = statistics.median(baseline) if len(baseline) >= 3 else None
        result.append({'id': key, 'name': name, 'evidence': evidence, 'kind': kind, 'kind_label': label,
                       'identity_verified': verified, 'sec_uid': best.get('sec_uid'), 'account_name': best.get('account_name'),
                       'lanes': sorted({l for m in members for l in m['creative']['lanes']}),
                       'works': len(members), 'recent_works': len(recent), 'latest_published_at': ordered[0].get('published_at'),
                       'first_seen': min(r['first_seen'] for r in members),
                       'plays_available': sum(r.get('play_count') is not None for r in members),
                       'measured_growth_works': len(measured), 'peak_daily_growth': max((r['growth']['per_day'] for r in measured), default=None),
                       'sample_median_likes': median_likes,
                       'account_baseline_samples': len(baseline),
                       'breakout_multiple': round((best.get('digg_count') or 0)/median_likes, 2) if median_likes else None,
                       'state': 'rising' if measured and any(r['growth']['delta'] > 0 for r in measured) else 'baseline' if recent and any(r.get('play_count') is not None for r in recent) else 'candidate' if recent else 'classic',
                       'representative': best, 'latest': ordered[:3], 'member_ids': [r['id'] for r in ordered],
                       'score': potential(best, now), 'account_monitor': best.get('account_monitor')})
    return sorted(result, key=lambda r: (r['state'] != 'classic', r['peak_daily_growth'] is not None,
                                         r['peak_daily_growth'] or 0, r['state'] == 'baseline', r['score']), reverse=True)


def library(data=None):
    data = data or {}
    rows = groups()
    focus = creative.validate(data.get('focus', 'ai'))
    if focus not in {'ai', 'all'}:
        rows = [r for r in rows if focus in r['lanes']]
    state = data.get('state', 'recent')
    if state not in {'all', 'recent', 'rising', 'classic', 'verified'}:
        raise ValueError('IP 状态无效')
    rows = [r for r in rows if state == 'all' or (state == 'recent' and r['recent_works']) or
            (state == 'verified' and r['identity_verified']) or r['state'] == state]
    word = str(data.get('keyword') or '').strip().casefold()
    if len(word) > 120:
        raise ValueError('关键词过长')
    if word:
        rows = [r for r in rows if word in (r['name']+' '+(r['account_name'] or '')+' '+r['representative']['title']).casefold()]
    size = d.integer(data.get('page_size', 12), 1, 50, '每页数量')
    page = d.integer(data.get('page', 1), 1, 100000, '页码')
    total = len(rows); page = min(page, max(1, math.ceil(total/size)))
    return {'items': rows[(page-1)*size:page*size], 'total': total, 'page': page, 'page_size': size}


def keyword_pool(items, cfg):
    pool = [{'keyword': k, 'origin': '方向词'} for k in cfg['keywords']]
    if cfg['expand_keywords']:
        # Only repeat evidence from at least two works, not every stray hashtag.
        candidates = [g for g in groups(items) if g['works'] >= 2 and g['recent_works'] and
                      (g['evidence'] in {'合集名称', '标题剧名', '分集标题'} or
                       g['evidence'] == '作品标签' and clean_name(g['name']) == clean_name(g['account_name'] or '')) and
                      g['kind'] != 'commentary']
        known = {r['keyword'].casefold() for r in pool}
        for g in candidates:
            if g['name'].casefold() not in known:
                pool.append({'keyword': g['name'], 'origin': '重复作品发现', 'samples': g['member_ids'][:3]})
                known.add(g['name'].casefold())
            if len(pool) >= len(cfg['keywords'])+12:
                break
    searched = {r['target']: r['last_at'] for r in store.q("SELECT target,MAX(created_at) last_at FROM radar_attempts WHERE kind='search' GROUP BY target")}
    # Three broad queries for every learned-name query. Learned names cannot
    # crowd out the ongoing search for wholly new creators and stories.
    n = store.q("SELECT COUNT(*) n FROM radar_attempts WHERE kind='search'")[0]['n']
    preferred = '重复作品发现' if n % 4 == 3 else '方向词'
    return sorted(pool, key=lambda r: (r['origin'] != preferred, searched.get(r['keyword'], '')))


def group_detail(identity):
    items = works()
    group = next((g for g in groups(items) if g['id'] == identity), None)
    if not group:
        raise ValueError('IP 分组已更新，请重新搜索')
    members = set(group['member_ids'])
    return {'group': group, 'items': [r for r in items if r['id'] in members]}


def targets(items, cfg, manual_ids=None):
    if manual_ids is not None:
        lookup = {r['id']: r for r in items}
        return [lookup[k] for k in manual_ids if k in lookup]
    pins = {r['aweme_id'] for r in store.q('SELECT aweme_id FROM radar_pins WHERE enabled=1')}
    retained = {r['aweme_id']: r['added_at'] for r in store.q('SELECT * FROM radar_auto_watch WHERE expires_at>?', (stamp(),))}
    eligible = [r for r in items if (r['id'] in pins or age_days(r) is not None and 0 <= age_days(r) <= cfg['recent_days']) and content_type(r)[0] != 'commentary']
    ordered = sorted(eligible, key=lambda r: (r['id'] in pins, r['id'] in retained, potential(r)), reverse=True)
    selected, per_author = [], Counter()
    for row in sorted(eligible, key=lambda r: (r['id'] not in pins, retained.get(r['id'], 'z'))):
        if row['id'] not in pins:
            continue
        selected.append(row)
        per_author[row.get('sec_uid') or (row.get('account_name'), row.get('avatar_url'))] += 1
        if len(selected) >= cfg['watch_limit']:
            return selected
    # Round-robin lanes avoid one search's 1,000 cat videos taking every slot.
    for _ in range(cfg['watch_limit']):
        for lane in [l['id'] for l in creative.LANES]:
            candidate = next((r for r in ordered if r not in selected and lane in r['creative']['lanes'] and
                              per_author[r.get('sec_uid') or (r.get('account_name'), r.get('avatar_url'))] < 2), None)
            if candidate:
                selected.append(candidate)
                per_author[candidate.get('sec_uid') or (candidate.get('account_name'), candidate.get('avatar_url'))] += 1
            if len(selected) >= cfg['watch_limit']:
                return selected
    for candidate in ordered:
        author = candidate.get('sec_uid') or (candidate.get('account_name'), candidate.get('avatar_url'))
        if candidate not in selected and per_author[author] < 2:
            selected.append(candidate); per_author[author] += 1
        if len(selected) >= cfg['watch_limit']:
            break
    return selected


def pending_jobs():
    return [j for j in douk.jobs() if j['endpoint'] == 'ip_radar' and j['status'] in {'queued', 'running'}]


def task_list(action, ids, cfg, items):
    tasks = []
    if action == 'cycle':
        keyword = keyword_pool(items, cfg)[0]
        tasks.append({'kind': 'search', 'target': keyword['keyword'], 'origin': keyword['origin']})
    chosen = targets(items, cfg, ids if action == 'enrich' else None)
    last = {(r['kind'], r['target']): r['last_at'] for r in store.q('SELECT kind,target,MAX(created_at) last_at FROM radar_attempts GROUP BY kind,target')}
    # Least recently attempted works first; errors cool down too. An empty or
    # unavailable statistic does not cause an expensive request every 15 seconds.
    chosen.sort(key=lambda r: (last.get(('statistics', r['id']), ''), last.get(('detail', r['id']), '')))
    for item in chosen:
        for kind in ('detail', 'statistics'):
            previous = last.get((kind, item['id']))
            age = (d.now()-at(previous)).total_seconds()/3600 if previous else float('inf')
            due = age >= (72 if kind == 'detail' and item.get('identity_verified') else cfg['refresh_hours'])
            if kind == 'detail' and item.get('identity_verified') and item.get('detail_updated_at') and (d.now()-at(item['detail_updated_at'])).total_seconds() < 72*3600:
                continue
            if action == 'enrich' or due:
                tasks.append({'kind': kind, 'target': item['id'], 'title': item['title'][:100]})
        if action == 'cycle' and len(tasks) >= cfg['cycle_calls']:
            break
    return tasks[:cfg['cycle_calls']] if action == 'cycle' else tasks


def plan(data=None):
    data = data or {}
    if set(data) - {'action', 'ids', 'confirmed', 'fingerprint'}:
        raise ValueError('监测请求格式无效')
    cfg = config(); action = data.get('action', 'cycle')
    if action not in {'cycle', 'enrich'}:
        raise ValueError('监测操作无效')
    ids = list(dict.fromkeys(str(v) for v in data.get('ids', [])))
    if action == 'enrich' and (not ids or len(ids) > 50 or any(not re.fullmatch(r'\d{10,25}', v) for v in ids)):
        raise ValueError('请选择 1–50 条已收录作品')
    items = works()
    if ids and not set(ids) <= {r['id'] for r in items}:
        raise ValueError('只能补采已收录的 AI 作品')
    tasks = task_list(action, ids, cfg, items)
    calls = len(tasks)
    reason = ''
    if not playback.settings()['key_set']:
        reason = '请先设置 TikHub API Key'
    if calls+usage() > cfg['daily_calls'] or calls+tikhub.usage() > playback.settings()['daily_call_limit']:
        reason = '今日剩余请求额度不足，请减少作品或调整每日上限'
    if pending_jobs():
        reason = '已有 AI 监测任务在执行，请等待完成'
    fingerprint = digest({'action': action, 'ids': ids, 'revision': cfg['revision'], 'tasks': tasks})
    return {'action': action, 'ids': ids, 'tasks': tasks, 'calls': calls, 'allowed': bool(calls) and not reason,
            'reason': reason or ('' if calls else '当前没有到期作品'), 'fingerprint': fingerprint,
            'revision': cfg['revision'], 'today_calls': usage(), 'daily_calls': cfg['daily_calls'],
            'endpoints': [d.SOURCES['search'][1] if t['kind'] == 'search' else DETAIL if t['kind'] == 'detail' else STATISTICS for t in tasks]}


def submit(data, scheduled=False):
    with LOCK:
        preview = plan({k: v for k, v in data.items() if k != 'slot'})
        if not preview['allowed'] or data.get('confirmed') is not True or data.get('fingerprint') != preview['fingerprint']:
            raise ValueError(preview['reason'] or '条件已变化，请重新预览')
        job = douk.submit({'endpoint': 'ip_radar', 'title': 'AI 雷达 · 自动巡检' if scheduled else 'AI 雷达 · '+('补采作品' if preview['action'] == 'enrich' else '发现与追踪'),
                           'radar_plan': preview, 'scheduled': scheduled, 'confirmed': True})
        return job


def request_for(task):
    if task['kind'] == 'detail':
        return d.api_request(DETAIL, {'aweme_id': task['target']})
    if task['kind'] == 'statistics':
        # Deliberately never select fetch_multi_video_statistics or a batch path.
        return d.api_request(STATISTICS, {'aweme_ids': task['target']})
    raise ValueError('不支持的请求')


def save_detail(body, aid, observed_at, event_key):
    detail = d.unwrap(body)
    if isinstance(detail, dict):
        detail = detail.get('aweme_detail', detail)
    if not isinstance(detail, dict) or str(detail.get('aweme_id')) != aid:
        raise ValueError('返回作品 ID 不匹配，未保存')
    author = detail.get('author') or {}
    uid = str(author.get('sec_uid') or '')
    if not re.fullmatch(r'[A-Za-z0-9_=-]{12,}', uid):
        raise ValueError('返回作者 ID 无效，未标记为已核实')
    value = douk.normalize(detail)
    if not value:
        raise ValueError('作品详情结构无法识别')
    previous = store.q('SELECT value FROM radar_details WHERE aweme_id=? ORDER BY observed_at DESC LIMIT 1', (aid,))
    if previous and json.loads(previous[0]['value']).get('sec_uid') != uid:
        raise ValueError('作品作者身份发生冲突，未覆盖原身份')
    value.update(account_name=value.pop('author_name'), fans=d.number(author.get('follower_count')),
                 published_at=d.published(value.get('create_time')),
                 duration=d.number((detail.get('video') or {}).get('duration')))
    if value['duration'] is not None:
        value['duration'] /= 1000
    for key in ('avatar_url', 'cover_url'):
        value[key] = d.public_url(value.get(key))
    mix = detail.get('mix_info') or {}
    value.update(sec_uid=uid, mix_id=str(mix.get('mix_id') or ''), mix_name=mix.get('mix_name') or '')
    # Some detail endpoints return zero as an unavailable playback placeholder.
    if not value.get('play_count'):
        value['play_count'] = None
    store.x('INSERT OR REPLACE INTO radar_details VALUES(?,?,?)', (aid, observed_at, json.dumps(value, ensure_ascii=False)))
    mh.record({'aweme_id': aid, **{k: value.get(k) for k in METRICS}}, source='tikhub_app_v3_detail', scope='unspecified', observed_at=observed_at, event_key=event_key)
    # Cache only the chosen work's two images, after the evidence is durable.
    # This is public thumbnail I/O, never another paid provider request.
    from media_cache import cache_images
    try:
        saved = cache_images([value.get('cover_url'), value.get('avatar_url')])
        for field in ('cover', 'avatar'):
            if saved.get(value.get(field+'_url')):
                value[field+'_path'] = saved[value[field+'_url']]
        store.x('UPDATE radar_details SET value=? WHERE aweme_id=? AND observed_at=?',
                (json.dumps(value, ensure_ascii=False), aid, observed_at))
    except OSError:
        pass
    return value


def execute(request, progress=lambda p: None, cancelled=lambda: False):
    p = request.get('radar_plan') or {}
    if request.get('confirmed') is not True or p.get('revision') != config()['revision']:
        raise ValueError('AI 监测计划已修改，请重新提交')
    tasks = p.get('tasks') or []
    if p.get('fingerprint') != digest({k: p[k] for k in ('action', 'ids', 'revision', 'tasks')}):
        raise ValueError('AI 监测计划校验失败')
    if not 1 <= len(tasks) <= 100 or p.get('calls') != len(tasks):
        raise ValueError('AI 监测请求数量无效')
    allowed = {r['id'] for r in works()}
    if any(t.get('kind') not in {'detail', 'statistics', 'search'} or t['kind'] != 'search' and t.get('target') not in allowed for t in tasks):
        raise ValueError('AI 监测作品范围无效')
    if p['action'] == 'cycle':
        cfg = config()
        for row in targets(works(), cfg):
            # Retain a cohort long enough to compare it, rather than replacing
            # every sample whenever the next keyword returns 1,000 new works.
            expiry = d.now()+timedelta(days=7)
            store.x('''INSERT INTO radar_auto_watch VALUES(?,?,?) ON CONFLICT(aweme_id) DO UPDATE SET
                       added_at=excluded.added_at,expires_at=excluded.expires_at
                       WHERE radar_auto_watch.expires_at<=excluded.added_at''', (row['id'], stamp(), stamp(expiry.isoformat())))
    done = 0; receipts = []; errors = []; outcomes = []
    for task in tasks:
        cfg = config()
        if cancelled() or p['revision'] != cfg['revision'] or request.get('scheduled') and not cfg['enabled']:
            errors.append({'error': '任务取消或计划已修改，停止后续请求'}); break
        with LOCK:
            if usage() >= cfg['daily_calls'] or tikhub.usage() >= playback.settings()['daily_call_limit']:
                errors.append({'error': '已达到每日请求上限'}); break
            ident = uuid.uuid4().hex
            store.x('INSERT INTO radar_attempts VALUES(?,?,?,?,?,?,?)', (ident, stamp(), request.get('job_id', ''), task['kind'], task['target'], 'started', ''))
        error = ''; response = None
        progress({'done': done, 'total': len(tasks), 'phase': task['kind'], 'target': task['target'], 'count': len(outcomes)})
        try:
            if task['kind'] == 'search':
                query = {'source': 'search', 'keyword': task['target'], 'days': 7, 'duration': 0}
                quote = d.plan(query)
                if not quote['allowed']:
                    raise ValueError(quote['reason'])
                response = d.execute({'query': quote['query'], 'fingerprint': quote['fingerprint'], 'confirmed': True, 'job_id': request.get('job_id', '')}, cancelled=cancelled)
                if response.get('errors'):
                    raise ValueError(response['errors'][0]['error'])
                outcomes.append({'kind': 'search', 'keyword': task['target'], 'count': len(response.get('data', []))})
            else:
                req = request_for(task); quote = tikhub.plan(req)
                if not quote['allowed']:
                    raise ValueError(quote['reason'])
                response = tikhub.execute({**req, 'allow_charges': True, 'fingerprint': quote['fingerprint']}, cancelled=cancelled)
                good = [r for r in response.get('receipts', []) if r.get('ok')]
                if not good:
                    raise ValueError((response.get('errors') or [{'error': '供应商没有返回有效响应'}])[0]['error'])
                receipt = good[0]; observed = stamp(); aid = task['target']
                event_key = 'radar:'+ident
                if task['kind'] == 'detail':
                    value = save_detail(receipt['response'], aid, observed, event_key)
                    outcomes.append({'kind': 'detail', 'id': aid, 'sec_uid': value['sec_uid']})
                else:
                    rows = playback.parse(receipt['response'], 'app', [aid])
                    playback.persist(rows, event_key=event_key)
                    for row in rows:
                        mh.record(row, observed_at=observed, event_key=event_key)
                    outcomes.append({'kind': 'statistics', 'id': aid, 'play_count': next((r['play_count'] for r in rows if r['aweme_id'] == aid), None)})
                if response.get('errors'):
                    error = response['errors'][0]['error']
        except (ValueError, TypeError, KeyError) as exc:
            error = str(exc)[:200]
        if response:
            receipts.extend(response.get('receipts') or [])
        store.x('UPDATE radar_attempts SET status=?,error=? WHERE id=?', ('failed' if error else 'completed', error, ident))
        done += 1
        if error:
            errors.append({'kind': task['kind'], 'target': task['target'], 'error': error})
        progress({'done': done, 'total': len(tasks), 'count': len(outcomes), 'phase': 'saved', 'errors': len(errors), 'outcomes': outcomes[-5:]})
        # Failure is not an invitation to try another endpoint or repeat a charge.
        if error and ('HTTP 4' in error or '额度' in error or 'Key' in error or '网络' in error):
            break
    return {'ok': bool(outcomes), 'data': outcomes, 'errors': errors, 'receipts': receipts, 'attempted': done,
            'message': f'完成 {done}/{len(tasks)} 次单独请求，保存 {len(outcomes)} 项；失败 {len(errors)} 项'}


def tick(now=None):
    now = now or d.now(); cfg = config()
    if not cfg['enabled'] or not cfg['enabled_at']:
        return None
    elapsed = (now-at(cfg['enabled_at'])).total_seconds()
    if elapsed < 0:
        return None
    slot = cfg['revision']+':'+str(int(elapsed//(cfg['interval_hours']*3600)))
    with LOCK, store._LOCK, store.get_conn() as conn:
        if conn.execute('SELECT 1 FROM radar_slots WHERE slot=?', (slot,)).fetchone() or pending_jobs():
            return None
        conn.execute('INSERT INTO radar_slots VALUES(?,?,NULL,?,NULL)', (slot, cfg['revision'], 'claimed'))
    try:
        quote = plan({'action': 'cycle'})
        if not quote['allowed']:
            raise ValueError(quote['reason'])
        job = submit({'action': 'cycle', 'confirmed': True, 'fingerprint': quote['fingerprint']}, scheduled=True)
        store.x('UPDATE radar_slots SET status=?,job_id=? WHERE slot=?', ('submitted', job['id'], slot))
        return job
    except (ValueError, TypeError) as exc:
        store.x('UPDATE radar_slots SET status=?,error=? WHERE slot=?', ('blocked', str(exc)[:200], slot))
    return None


def status():
    cfg = config(); rows = works()
    last = store.q('SELECT * FROM radar_attempts ORDER BY created_at DESC LIMIT 30')
    next_at = None
    if cfg['enabled'] and cfg['enabled_at']:
        start = at(cfg['enabled_at']); seconds = cfg['interval_hours']*3600
        elapsed = max(0, (d.now()-start).total_seconds())
        next_at = (start+timedelta(seconds=(int(elapsed//seconds)+1)*seconds)).isoformat()
    return {'settings': cfg, 'today_calls': usage(), 'global_today_calls': tikhub.usage(), 'next_at': next_at,
            'keywords': keyword_pool(rows, cfg), 'targets': targets(rows, cfg), 'attempts': last,
            'jobs': pending_jobs(), 'recent_slots': store.q('SELECT * FROM radar_slots ORDER BY rowid DESC LIMIT 8')}


def pin(data):
    aid = str(data.get('id') or '')
    if not isinstance(data.get('enabled'), bool) or aid not in {r['id'] for r in works()}:
        raise ValueError('请选择已收录的 AI 作品')
    init()
    store.x('INSERT OR REPLACE INTO radar_pins VALUES(?,?,?)', (aid, int(data['enabled']), stamp()))
    return {'ok': True}


def balanced_groups(rows, limit):
    selected = []
    for _ in range(limit):
        for lane in creative.LANES:
            group = next((g for g in rows if g not in selected and lane['id'] in g['lanes']), None)
            if group:
                selected.append(group)
            if len(selected) >= limit:
                return selected
    return (selected+[g for g in rows if g not in selected])[:limit]


def report(start, end):
    items = works(until=end)
    candidates = groups(items, now=at(end))
    observed = [r for r in items if r.get('metrics_updated_at') and stamp(start) <= stamp(r['metrics_updated_at']) < stamp(end)]
    return {'candidates': balanced_groups(candidates, 24), 'new_ips': balanced_groups([g for g in candidates if stamp(start) <= stamp(g['first_seen']) < stamp(end)], 12),
            'rising': [g for g in candidates if g['state'] == 'rising'][:12],
            'recent_references': [g['representative'] for g in candidates if g['recent_works'] and g['kind'] != 'commentary'][:12],
            'classic_references': [g['representative'] for g in candidates if g['state'] == 'classic'][:6],
            'coverage': {'observed_works': len(observed), 'play_count': sum(r.get('play_count') is not None for r in items),
                         'growth_comparable': sum(r.get('growth') is not None for r in items), 'candidates': len(candidates)},
            'searches': store.q("SELECT target,created_at,status FROM radar_attempts WHERE kind='search' AND created_at>=? AND created_at<? ORDER BY created_at", (stamp(start), stamp(end)))}
