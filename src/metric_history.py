"""Append-only metric observations alongside the existing daily projections."""
from datetime import datetime, timedelta, timezone
import uuid

import store

TZ=timezone(timedelta(hours=8))
METRICS=('play_count','digg_count','comment_count','collect_count','share_count')
_READY=None


def timestamp(value=None):
    if not value:
        return datetime.now(TZ).isoformat(timespec='milliseconds')
    parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ).isoformat(timespec='milliseconds')


def _insert(conn,row,source,scope,observed_at,event_key,origin):
    aid=str(row.get('aweme_id') or '')
    if not aid:return
    values=[row.get(key) for key in METRICS]
    # A response with no metrics cannot advance the last data-update time.
    if not any(value is not None for value in values):return
    observed_at=timestamp(observed_at)
    key=f'{event_key}:{aid}:{source}:{scope}' if event_key else uuid.uuid4().hex
    conn.execute('''INSERT OR IGNORE INTO video_metric_events
      (aweme_id,fetch_date,observed_at,source,scope,play_count,digg_count,comment_count,collect_count,share_count,request_id,event_key,origin)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
      (aid,row.get('fetch_date') or observed_at[:10],observed_at,source,scope,*values,
       str(row.get('request_id') or ''),key,origin))


def init():
    global _READY
    with store._LOCK:
        conn=store.get_conn()
        if _READY is conn:return
        with conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS video_metric_events (
              id INTEGER PRIMARY KEY,aweme_id TEXT NOT NULL,fetch_date TEXT NOT NULL,
              observed_at TEXT NOT NULL,source TEXT NOT NULL,scope TEXT NOT NULL,
              play_count INTEGER,digg_count INTEGER,comment_count INTEGER,collect_count INTEGER,share_count INTEGER,
              request_id TEXT,event_key TEXT NOT NULL UNIQUE,origin TEXT NOT NULL)''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_metric_events_video ON video_metric_events(aweme_id,observed_at,id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_metric_events_date ON video_metric_events(fetch_date,aweme_id)')
            conn.execute('CREATE TABLE IF NOT EXISTS metric_history_migrations (version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)')
            if not conn.execute('SELECT 1 FROM metric_history_migrations WHERE version=1').fetchone():
                tables={r['name'] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table,source,scope in [('playback_measurements',None,None),('external_video_observations',None,'unspecified'),
                                           ('account_video_snapshots',None,'unspecified'),('video_snapshots','public_mix_metric','unspecified')]:
                    if table not in tables:continue
                    for raw in conn.execute('SELECT * FROM '+table).fetchall():
                        row=dict(raw);src=source or row.get('source') or 'author_post';sc=scope or row.get('scope') or 'unspecified'
                        key=f'legacy:{table}:{row["fetch_date"]}'
                        _insert(conn,row,src,sc,row.get('fetched_at') or row['fetch_date'],key,'legacy_daily')
                conn.execute('INSERT INTO metric_history_migrations VALUES(1,?)',(timestamp(),))
        _READY=conn


def record(row,source=None,scope=None,observed_at=None,event_key=''):
    init()
    with store._LOCK:
        conn=store.get_conn()
        with conn:
            _insert(conn,row,source or row.get('source') or 'author_post',scope or row.get('scope') or 'unspecified',
                    observed_at,event_key,'refresh')


def latest():
    init()
    return {r['aweme_id']:r for r in store.q('''SELECT aweme_id,MAX(observed_at) metrics_updated_at,
      COUNT(*) history_count FROM video_metric_events GROUP BY aweme_id''')}


def video(aid):
    init()
    rows=store.q('SELECT * FROM video_metric_events WHERE aweme_id=? ORDER BY observed_at,id',(str(aid),))
    previous={}
    for row in rows:
        row.update(fetched_at=row['observed_at'],metric_status=row['source'])
        key=(row['source'],row['scope']);old=previous.get(key)
        row['play_delta']=row['play_count']-old if old is not None and row['play_count'] is not None else None
        if row['play_count'] is not None:previous[key]=row['play_count']
    return rows


def account(account_id,start,end):
    init()
    return store.q('''SELECT e.* FROM video_metric_events e WHERE e.aweme_id IN (
      SELECT aweme_id FROM tracked_account_videos WHERE sec_uid=? UNION
      SELECT v.aweme_id FROM video_snapshots v JOIN mixes m ON m.mix_id=v.mix_id WHERE m.sec_uid=?)
      AND e.fetch_date>=? AND e.fetch_date<=? ORDER BY e.observed_at,e.id''',(account_id,account_id,start,end))
