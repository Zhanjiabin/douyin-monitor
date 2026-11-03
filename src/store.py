"""SQLite 存储层。快照表只追加不覆盖，保留时间序列以支持日增与趋势分析。"""
import sqlite3
import threading
from datetime import datetime

from config import DB_PATH, ensure_dirs

_LOCK = threading.RLock()
_CONN = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  url           TEXT NOT NULL UNIQUE,
  note          TEXT DEFAULT '',
  enabled       INTEGER DEFAULT 1,
  added_at      TEXT,
  aweme_id      TEXT,
  mix_id        TEXT,
  sec_uid       TEXT,
  author_name   TEXT,
  mix_name      TEXT,
  episode_count INTEGER,
  resolve_state TEXT DEFAULT 'pending',
  last_error    TEXT,
  last_run_at   TEXT
);

CREATE TABLE IF NOT EXISTS mixes (
  mix_id             TEXT PRIMARY KEY,
  sec_uid            TEXT,
  author_name        TEXT,
  mix_name           TEXT,
  cover              TEXT,
  first_seen         TEXT,
  last_seen          TEXT,
  play_vv            INTEGER,
  collect_vv         INTEGER,
  updated_to_episode INTEGER,
  episode_count      INTEGER,
  first_publish_at   TEXT
);

CREATE TABLE IF NOT EXISTS video_snapshots (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  aweme_id      TEXT NOT NULL,
  mix_id        TEXT,
  fetch_date    TEXT NOT NULL,
  fetched_at    TEXT NOT NULL,
  episode_no    INTEGER,
  title         TEXT,
  play_count    INTEGER,
  digg_count    INTEGER,
  comment_count INTEGER,
  share_count   INTEGER,
  collect_count INTEGER,
  create_time   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_vs_aweme ON video_snapshots(aweme_id, fetch_date);
CREATE INDEX IF NOT EXISTS idx_vs_mix   ON video_snapshots(mix_id, fetch_date);
CREATE UNIQUE INDEX IF NOT EXISTS uq_vs ON video_snapshots(aweme_id, fetch_date);

CREATE TABLE IF NOT EXISTS mix_snapshots (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  mix_id             TEXT NOT NULL,
  fetch_date         TEXT NOT NULL,
  fetched_at         TEXT NOT NULL,
  play_vv            INTEGER,
  collect_vv         INTEGER,
  updated_to_episode INTEGER,
  episode_count      INTEGER,
  play_sum_calc      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ms_mix ON mix_snapshots(mix_id, fetch_date);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ms ON mix_snapshots(mix_id, fetch_date);

CREATE TABLE IF NOT EXISTS runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at  TEXT,
  finished_at TEXT,
  total       INTEGER DEFAULT 0,
  ok          INTEGER DEFAULT 0,
  failed      INTEGER DEFAULT 0,
  skipped     INTEGER DEFAULT 0,
  status      TEXT DEFAULT 'running',
  message     TEXT
);
"""


def _ts_to_date(ts):
    """Unix 秒 -> YYYY-MM-DD。非法值返回 None。"""
    try:
        ts = int(ts)
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return None


def _migrate(conn):
    """给老库补新字段。CREATE TABLE IF NOT EXISTS 不会改已存在的表，
    所以新增列必须走 ALTER TABLE，且要能重复执行不报错。"""
    def _cols(table):
        return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]

    vs_cols = _cols("video_snapshots")
    if "create_time" not in vs_cols:
        conn.execute("ALTER TABLE video_snapshots ADD COLUMN create_time INTEGER")

    m_cols = _cols("mixes")
    if "first_publish_at" not in m_cols:
        conn.execute("ALTER TABLE mixes ADD COLUMN first_publish_at TEXT")

    conn.commit()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def get_conn():
    global _CONN
    with _LOCK:
        if _CONN is None:
            ensure_dirs()
            _CONN = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            _CONN.row_factory = sqlite3.Row
            _CONN.executescript(SCHEMA)
            _CONN.commit()
            _migrate(_CONN)
        return _CONN


def init_db():
    get_conn()


def q(sql, args=()):
    with _LOCK:
        cur = get_conn().execute(sql, args)
        rows = [dict(r) for r in cur.fetchall()]
        return rows


def x(sql, args=()):
    with _LOCK:
        conn = get_conn()
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid


# ---------------- 清单 ----------------
def add_watch_items(urls, note=""):
    init_db()
    added, dup = 0, 0
    for u in urls:
        u = (u or "").strip().rstrip("/")
        if not u:
            continue
        if q("SELECT id FROM watchlist WHERE url=?", (u,)):
            dup += 1
            continue
        x("INSERT INTO watchlist(url,note,enabled,added_at) VALUES(?,?,1,?)", (u, note, now_str()))
        added += 1
    return added, dup


def list_watch_items():
    return q("SELECT * FROM watchlist ORDER BY id")


def active_watch_items():
    return q("SELECT * FROM watchlist WHERE enabled=1 ORDER BY id")


def set_watch_enabled(item_id, enabled):
    x("UPDATE watchlist SET enabled=? WHERE id=?", (1 if enabled else 0, item_id))


def delete_watch_item(item_id):
    x("DELETE FROM watchlist WHERE id=?", (item_id,))


def update_watch_resolved(item_id, info, error=None):
    x(
        """UPDATE watchlist SET aweme_id=?, mix_id=?, sec_uid=?, author_name=?, mix_name=?,
           episode_count=?, resolve_state=?, last_error=?, last_run_at=? WHERE id=?""",
        (
            info.get("aweme_id"), info.get("mix_id"), info.get("sec_uid"),
            info.get("author_name"), info.get("mix_name"), info.get("episode_count"),
            "failed" if error else ("no_mix" if not info.get("mix_id") else "ok"),
            error, now_str(), item_id,
        ),
    )


def mark_all_pending():
    x("UPDATE watchlist SET resolve_state='pending' WHERE enabled=1")


# ---------------- 合集 ----------------
def upsert_mix(m):
    init_db()
    exist = q("SELECT mix_id FROM mixes WHERE mix_id=?", (m["mix_id"],))
    if exist:
        x(
            """UPDATE mixes SET mix_name=COALESCE(?,mix_name), sec_uid=COALESCE(?,sec_uid),
               author_name=COALESCE(?,author_name), last_seen=?,
               play_vv=COALESCE(?,play_vv), collect_vv=COALESCE(?,collect_vv),
               updated_to_episode=COALESCE(?,updated_to_episode),
               episode_count=COALESCE(?,episode_count) WHERE mix_id=?""",
            (m.get("mix_name"), m.get("sec_uid"), m.get("author_name"), now_str(),
             m.get("play_vv"), m.get("collect_vv"), m.get("updated_to_episode"),
             m.get("episode_count"), m["mix_id"]),
        )
    else:
        x(
            """INSERT INTO mixes(mix_id,sec_uid,author_name,mix_name,first_seen,last_seen,
               play_vv,collect_vv,updated_to_episode,episode_count)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (m["mix_id"], m.get("sec_uid"), m.get("author_name"), m.get("mix_name"),
             now_str(), now_str(), m.get("play_vv"), m.get("collect_vv"),
             m.get("updated_to_episode"), m.get("episode_count")),
        )


def save_mix_snapshot(snap):
    init_db()
    x(
        """INSERT OR REPLACE INTO mix_snapshots
           (mix_id,fetch_date,fetched_at,play_vv,collect_vv,
            updated_to_episode,episode_count,play_sum_calc) VALUES(?,?,?,?,?,?,?,?)""",
        (snap["mix_id"], snap.get("fetch_date") or today_str(), now_str(),
         snap.get("play_vv"), snap.get("collect_vv"), snap.get("updated_to_episode"),
         snap.get("episode_count"), snap.get("play_sum_calc")),
    )


def save_mix_partial(mix_id, fetch_date, episode_count, play_sum_calc,
                     updated_to_episode=None):
    """采集到分集数据后【立即】写入，防止中途崩溃导致整批丢失。
    只更新分集相关字段，保留已有的 play_vv（官方总量要等作者阶段才有）。"""
    init_db()
    x(
        """INSERT INTO mix_snapshots
           (mix_id,fetch_date,fetched_at,updated_to_episode,episode_count,play_sum_calc)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(mix_id,fetch_date) DO UPDATE SET
             fetched_at         = excluded.fetched_at,
             episode_count      = excluded.episode_count,
             play_sum_calc      = excluded.play_sum_calc,
             updated_to_episode = COALESCE(excluded.updated_to_episode,
                                           mix_snapshots.updated_to_episode)""",
        (mix_id, fetch_date or today_str(), now_str(), updated_to_episode,
         episode_count, play_sum_calc),
    )


def update_mix_official(mix_id, fetch_date, play_vv=None, collect_vv=None,
                        updated_to_episode=None):
    """作者阶段拿到官方合集总量后回填，不覆盖分集数据。"""
    init_db()
    x(
        """UPDATE mix_snapshots SET
             play_vv            = COALESCE(?, play_vv),
             collect_vv         = COALESCE(?, collect_vv),
             updated_to_episode = COALESCE(?, updated_to_episode)
           WHERE mix_id=? AND fetch_date=?""",
        (play_vv, collect_vv, updated_to_episode, mix_id, fetch_date or today_str()),
    )


def save_video_snapshots(mix_id, episodes, fetch_date=None):
    init_db()
    fd = fetch_date or today_str()
    ts = now_str()
    for e in episodes or []:
        x(
            """INSERT OR REPLACE INTO video_snapshots
               (aweme_id,mix_id,fetch_date,fetched_at,episode_no,
                title,play_count,digg_count,comment_count,share_count,collect_count,
                create_time)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (e.get("aweme_id"), mix_id, fd, ts, e.get("episode_no"), e.get("title"),
             e.get("play_count"), e.get("digg_count"), e.get("comment_count"),
             e.get("share_count"), e.get("collect_count"), e.get("create_time")),
        )
    refresh_mix_first_publish(mix_id)


def refresh_mix_first_publish(mix_id):
    """合集首播日 = 该合集所有分集中最早的 create_time。

    不用 episode_no=1，因为作者可能把合集顺序调乱，第 1 集未必是最早发的。
    取历史全部快照的最小值：即使某天某集没采到，也不会把首播日算晚。
    只在该合集还没有首播日、或新值更早时才写入。
    """
    init_db()
    r = q(
        """SELECT MIN(create_time) ct FROM video_snapshots
           WHERE mix_id=? AND create_time IS NOT NULL AND create_time > 0""",
        (mix_id,),
    )
    d = _ts_to_date(r[0]["ct"]) if r and r[0]["ct"] else None
    if not d:
        return None
    x(
        """UPDATE mixes SET first_publish_at=?
           WHERE mix_id=? AND (first_publish_at IS NULL OR first_publish_at=''
                               OR first_publish_at > ?)""",
        (d, mix_id, d),
    )
    return d


def backfill_first_publish():
    """给库里所有还缺首播日的合集补算一次（升级老数据用）。"""
    init_db()
    rows = q("""SELECT mix_id FROM mixes
                WHERE first_publish_at IS NULL OR first_publish_at=''""")
    n = 0
    for r in rows:
        if refresh_mix_first_publish(r["mix_id"]):
            n += 1
    return n


def delete_snapshots_of_date(fetch_date):
    """同一天重复抓取时，先清掉当天旧快照，避免重复计数。"""
    init_db()
    x("DELETE FROM video_snapshots WHERE fetch_date=?", (fetch_date,))
    x("DELETE FROM mix_snapshots WHERE fetch_date=?", (fetch_date,))


# ---------------- 运行记录 ----------------
def run_start(total):
    return x("INSERT INTO runs(started_at,total,status) VALUES(?,?,'running')", (now_str(), total))


def run_finish(run_id, ok, failed, skipped, status="done", message=""):
    x("UPDATE runs SET finished_at=?,ok=?,failed=?,skipped=?,status=?,message=? WHERE id=?",
      (now_str(), ok, failed, skipped, status, message, run_id))


def reap_stale_runs():
    """启动时清理上次异常退出留下的僵尸运行记录（避免一直显示"运行中"）。"""
    init_db()
    rows = q("SELECT id FROM runs WHERE status='running' OR finished_at IS NULL")
    for r in rows:
        x("UPDATE runs SET status='interrupted', finished_at=?, "
          "message=COALESCE(NULLIF(message,''),'程序异常退出，本次未正常结束') WHERE id=?",
          (now_str(), r["id"]))
    return len(rows)


def last_runs(limit=20):
    return q("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))


# ---------------- 查询（报表 / 看板用） ----------------
def latest_date():
    r = q("SELECT MAX(fetch_date) d FROM mix_snapshots")
    return r[0]["d"] if r and r[0]["d"] else None


def has_data_on(fetch_date):
    """指定日期是否已有采集数据（用于判断是否需要补跑）。"""
    r = q("SELECT COUNT(*) c FROM mix_snapshots WHERE fetch_date=?", (fetch_date,))
    return bool(r and r[0]["c"] > 0)


def prev_date(of_date):
    r = q("SELECT MAX(fetch_date) d FROM mix_snapshots WHERE fetch_date<?", (of_date,))
    return r[0]["d"] if r and r[0]["d"] else None


def mix_overview(fetch_date=None):
    """各合集快照 + 增量。

    以 mixes 表为主表：即使某个合集在 fetch_date 当天采集失败/未采到，
    也会回退显示它最近一次的数值，并标记 stale=True，绝不会凭空消失。
    增量按「该合集自己的上一次快照」计算，而不是全局上一个采集日。
    """
    fd = fetch_date or latest_date()
    if not fd:
        return []
    mixes = q("""SELECT mix_id, mix_name, author_name, sec_uid, episode_count, cover,
                        first_publish_at
                 FROM mixes""")
    snaps = q("""SELECT mix_id, fetch_date, play_vv, collect_vv,
                        updated_to_episode, play_sum_calc
                 FROM mix_snapshots WHERE fetch_date <= ?
                 ORDER BY mix_id, fetch_date""", (fd,))
    by = {}
    for s in snaps:
        by.setdefault(s["mix_id"], []).append(s)

    out = []
    for m in mixes:
        arr = by.get(m["mix_id"]) or []
        if not arr:
            continue                      # 从没采到过任何快照，才不展示
        cur = arr[-1]
        prev = arr[-2] if len(arr) >= 2 else None
        delta = None
        if prev and cur.get("play_vv") is not None and prev.get("play_vv") is not None:
            delta = cur["play_vv"] - prev["play_vv"]
        out.append({
            "mix_id": m["mix_id"], "mix_name": m["mix_name"],
            "author_name": m["author_name"], "sec_uid": m["sec_uid"],
            "episode_count": m["episode_count"], "cover": m["cover"],
            "first_publish_at": m.get("first_publish_at") or None,
            "play_vv": cur.get("play_vv"), "collect_vv": cur.get("collect_vv"),
            "updated_to_episode": cur.get("updated_to_episode"),
            "play_sum_calc": cur.get("play_sum_calc"),
            "delta": delta,
            "fetch_date": cur.get("fetch_date"),
            "stale": cur.get("fetch_date") != fd,
        })
    out.sort(key=lambda a: (a.get("play_vv") or 0), reverse=True)
    return out


def episodes_of(mix_id, fetch_date=None):
    fd = fetch_date or latest_date()
    return q(
        """SELECT * FROM video_snapshots WHERE mix_id=? AND fetch_date=?
           ORDER BY episode_no""",
        (mix_id, fd),
    )


def mix_trend(mix_id):
    return q(
        """SELECT fetch_date, play_vv, play_sum_calc, updated_to_episode
           FROM mix_snapshots WHERE mix_id=? ORDER BY fetch_date""",
        (mix_id,),
    )


def watchlist_stats():
    return q(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) active,
                  SUM(CASE WHEN enabled=0 THEN 1 ELSE 0 END) disabled,
                  SUM(CASE WHEN resolve_state='no_mix' THEN 1 ELSE 0 END) no_mix
           FROM watchlist"""
    )[0]
