"""Platform-qualified content and comments shared by crawler jobs and workspaces."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

import store

# Native upstream table names are fixed here; request values never become SQL identifiers.
TABLES = {
    'dy': ('douyin_aweme', 'aweme_id', 'douyin_aweme_comment'),
    'xhs': ('xhs_note', 'note_id', 'xhs_note_comment'),
    'bili': ('bilibili_video', 'video_id', 'bilibili_video_comment'),
    'ks': ('kuaishou_video', 'video_id', 'kuaishou_video_comment'),
    'wb': ('weibo_note', 'note_id', 'weibo_note_comment'),
    'tieba': ('tieba_note', 'note_id', 'tieba_comment'),
    'zhihu': ('zhihu_content', 'content_id', 'zhihu_comment'),
    'reddit': ('reddit_post', 'post_id', 'reddit_comment'),
}


def init():
    store.x('''CREATE TABLE IF NOT EXISTS content_library (
        platform TEXT NOT NULL,content_id TEXT NOT NULL,title TEXT,description TEXT,
        url TEXT,cover_url TEXT,author_name TEXT,creator_hash TEXT,published_at TEXT,
        source TEXT NOT NULL,job_id TEXT,fetched_at TEXT,raw_json TEXT,
        PRIMARY KEY(platform,content_id))''')
    store.x('''CREATE TABLE IF NOT EXISTS content_metrics (
        platform TEXT NOT NULL,content_id TEXT NOT NULL,fetch_date TEXT NOT NULL,source TEXT NOT NULL,
        play_count INTEGER,digg_count INTEGER,comment_count INTEGER,collect_count INTEGER,
        share_count INTEGER,fetched_at TEXT,PRIMARY KEY(platform,content_id,fetch_date,source))''')
    store.x('''CREATE TABLE IF NOT EXISTS content_comments (
        platform TEXT NOT NULL,comment_id TEXT NOT NULL,content_id TEXT,parent_id TEXT,content TEXT,
        published_at TEXT,like_count INTEGER,creator_hash TEXT,author_name TEXT,source TEXT,
        job_id TEXT,fetched_at TEXT,raw_json TEXT,PRIMARY KEY(platform,comment_id))''')


def first(row, *keys):
    return next((row[k] for k in keys if row.get(k) not in (None, '')), None)


def number(value, positive=False):
    if isinstance(value, bool):
        return None
    try:
        value = int(value)
        return value if value >= (1 if positive else 0) else None
    except (ValueError, TypeError, OverflowError):
        return None


def published(value):
    try:
        if not value:
            return None
        ts = float(value)
        if ts > 1e11:
            ts /= 1000
        return datetime.fromtimestamp(ts, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError, OverflowError, OSError):
        return str(value)[:40] if value else None


def safe_url(value):
    if not isinstance(value, str):
        return ''
    parsed = urlsplit(value)
    return value if parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username else ''


def normalize(row, platform):
    identifier = first(row, TABLES[platform][1], 'content_id', 'id')
    if not identifier:
        return None
    return {'platform': platform, 'content_id': str(identifier),
        'title': first(row, 'title', 'desc', 'content', 'content_text') or '',
        'description': first(row, 'desc', 'content', 'content_text', 'self_text') or '',
        'url': safe_url(first(row, 'aweme_url', 'note_url', 'video_url', 'content_url', 'post_url', 'permalink')),
        'cover_url': safe_url(first(row, 'cover_url', 'video_cover_url')),
        'author_name': first(row, 'nickname', 'user_nickname') or '',
        'creator_hash': row.get('creator_hash') or '',
        'published_at': published(first(row, 'create_time', 'created_time', 'time', 'created_utc', 'publish_time')),
        'play_count': number(first(row, 'play_count', 'video_play_count', 'viewd_count'), True),
        'digg_count': number(first(row, 'liked_count', 'voteup_count', 'score')),
        'comment_count': number(first(row, 'comment_count', 'comments_count', 'video_comment', 'num_comments')),
        'collect_count': number(row.get('collected_count')), 'share_count': number(first(row, 'share_count', 'shared_count'))}


def save_content(row, raw, source, job_id):
    init()
    now = store.now_str()
    store.x('''INSERT INTO content_library VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(platform,content_id) DO UPDATE SET title=COALESCE(NULLIF(excluded.title,''),content_library.title),
        description=excluded.description,url=COALESCE(NULLIF(excluded.url,''),content_library.url),
        cover_url=COALESCE(NULLIF(excluded.cover_url,''),content_library.cover_url),
        published_at=COALESCE(excluded.published_at,content_library.published_at),author_name=excluded.author_name,
        creator_hash=excluded.creator_hash,source=excluded.source,job_id=excluded.job_id,fetched_at=excluded.fetched_at,raw_json=excluded.raw_json''',
        (row['platform'], row['content_id'], row['title'], row['description'], row['url'], row['cover_url'],
         row['author_name'], row['creator_hash'], row['published_at'], source, job_id, now, json.dumps(raw, ensure_ascii=False)))
    metrics = [row.get(k) for k in ('play_count','digg_count','comment_count','collect_count','share_count')]
    store.x('''INSERT INTO content_metrics VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(platform,content_id,fetch_date,source) DO UPDATE SET
        play_count=COALESCE(excluded.play_count,content_metrics.play_count),
        digg_count=COALESCE(excluded.digg_count,content_metrics.digg_count),
        comment_count=COALESCE(excluded.comment_count,content_metrics.comment_count),
        collect_count=COALESCE(excluded.collect_count,content_metrics.collect_count),
        share_count=COALESCE(excluded.share_count,content_metrics.share_count),fetched_at=excluded.fetched_at''',
        (row['platform'],row['content_id'],store.today_str(),source,*metrics,now))
    if row['platform'] == 'dy':
        import douk
        aid = row['content_id']
        # Upstream deliberately anonymizes authors. Only match a known author by exact video ID.
        if store.q('SELECT aweme_id FROM tracked_account_videos WHERE aweme_id=?', (aid,)):
            douk.init_tables()
            store.x('''INSERT INTO external_video_observations VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(aweme_id,fetch_date,source) DO UPDATE SET fetched_at=excluded.fetched_at,
                play_count=COALESCE(excluded.play_count,external_video_observations.play_count),
                digg_count=COALESCE(excluded.digg_count,external_video_observations.digg_count),
                comment_count=COALESCE(excluded.comment_count,external_video_observations.comment_count),
                collect_count=COALESCE(excluded.collect_count,external_video_observations.collect_count),
                share_count=COALESCE(excluded.share_count,external_video_observations.share_count)''',
                (aid,store.today_str(),now,*metrics,source))


def import_native(database, platform, since=0, job_id='',operation_id=None):
    init()
    path = Path(database)
    if not path.is_file():
        return {'data': [], 'comments': 0}
    conn = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    content_table, id_key, comment_table = TABLES[platform]
    # Some upstream releases use plural Reddit names / tieba_note_comment.
    if platform == 'reddit':
        content_table = next((t for t in ('reddit_post','reddit_submission') if t in tables),content_table)
    if platform == 'tieba':
        comment_table = next((t for t in ('tieba_comment','tieba_note_comment') if t in tables),comment_table)
    def recent(table):
        if table not in tables:
            return []
        if 'vox_crawl_observation' in tables and job_id and platform in {'dy','bili','xhs','reddit'}:
            kind='comment' if table==comment_table else 'topic'
            return [json.loads(r[0]) for r in conn.execute(
                'SELECT payload FROM vox_crawl_observation WHERE operation_id=? AND platform=? AND kind=?',
                (operation_id or job_id,platform,kind))]
        rows = [dict(r) for r in conn.execute('SELECT * FROM "'+table+'"')]
        def modified(row):
            value = number(row.get('last_modify_ts')) or 0
            return value if value > 1e11 else value * 1000
        return [r for r in rows if not since or modified(r) >= since]
    result=[]
    try:
        for raw in recent(content_table):
            row=normalize(raw,platform)
            if row:
                save_content(row,raw,'mcpro_python',job_id)
                result.append(row)
        comments=recent(comment_table)
        for raw in comments:
            cid=first(raw,'comment_id','id')
            if not cid:
                continue
            store.x('''INSERT INTO content_comments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(platform,comment_id) DO UPDATE SET content=excluded.content,like_count=excluded.like_count,
                fetched_at=excluded.fetched_at,job_id=excluded.job_id,raw_json=excluded.raw_json''',
                (platform,str(cid),str(raw.get(id_key) or raw.get('content_id') or ''),str(raw.get('parent_comment_id') or ''),
                 raw.get('content') or '',published(first(raw,'create_time','publish_time','created_utc')),
                 number(raw.get('like_count')),raw.get('creator_hash') or '',first(raw,'nickname','user_nickname') or '',
                 'mcpro_python',job_id,store.now_str(),json.dumps(raw,ensure_ascii=False)))
        checkpoint=None
        if 'crawler_checkpoint' in tables:
            found=conn.execute('SELECT * FROM crawler_checkpoint ORDER BY id DESC LIMIT 1').fetchone()
            checkpoint=dict(found) if found else None
        return {'data':result,'comments':len(comments),'checkpoint':checkpoint}
    finally:
        conn.close()


def contents(platform='', keyword='', limit=150):
    init()
    rows=store.q('''SELECT c.*,m.play_count,m.digg_count,m.comment_count,m.collect_count,m.share_count
        FROM content_library c LEFT JOIN content_metrics m ON m.rowid=(SELECT rowid FROM content_metrics
        WHERE platform=c.platform AND content_id=c.content_id ORDER BY fetched_at DESC,rowid DESC LIMIT 1)
        WHERE (?='' OR c.platform=?) AND (?='' OR c.title LIKE ?)
        ORDER BY c.fetched_at DESC LIMIT ?''',(platform,platform,keyword,'%'+keyword+'%',min(500,max(1,int(limit)))))
    for row in rows:
        row.pop('raw_json',None)
    return {'items':rows,'total':store.q('SELECT COUNT(*) n FROM content_library')[0]['n'],
            'comments':store.q('SELECT COUNT(*) n FROM content_comments')[0]['n']}


def comments(platform,content_id):
    init()
    if platform not in TABLES:
        raise ValueError('未知平台')
    return {'items':store.q('''SELECT comment_id,parent_id,content,published_at,like_count,author_name
        FROM content_comments WHERE platform=? AND content_id=? ORDER BY like_count DESC LIMIT 1000''',
        (platform,str(content_id)))}
