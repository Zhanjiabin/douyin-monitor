"""Public creative discovery through MediaCrawlerPro, without TikHub transport."""
import json
import math
import re

import douk
import integrations
import intelligence
import store

PROFILES=[
    {'id':'ai','title':'AI 创意成片','keywords':['AI创意视频','AI短片','AI动画']},
    {'id':'tutorial','title':'AI 制作教程','keywords':['AI视频教程','AI运镜','AI视频制作']},
    {'id':'fun','title':'热点趣味','keywords':['热点二创','脑洞创意','反转搞笑']},
    {'id':'drama','title':'短剧漫剧','keywords':['AI漫剧','AI短剧','原创动画短剧']},
    {'id':'custom','title':'自定义','keywords':[]},
]


def words(text):
    result=list(dict.fromkeys(x.strip() for x in re.split(r'[,，;；\n]+',text) if x.strip()))
    if not 1<=len(result)<=5 or any(len(x)>40 for x in result):raise ValueError('填写 1 至 5 个关键词，用逗号分隔，每个词最多 40 字')
    return result


def ready():
    if not integrations.python('MediaCrawlerPro-Python').is_file():return 'MediaCrawlerPro 搜索运行环境未安装'
    if not douk.settings(False).get('cookie'):return '普通抖音登录尚未同步；不需要创作者后台授权'
    return ''


def verified(ids):
    rows=[]
    for aid in ids:
        found=store.q('SELECT value FROM discovery_works WHERE aweme_id=?',(aid,))
        value=json.loads(found[0]['value']) if found else {}
        if value.get('source')!='native' or not re.fullmatch(r'MS4w[\w-]+',value.get('sec_uid','')):return []
        rows.append(value)
    return rows


def monitor(rows):
    """Add already-verified identities; retain the original observation time."""
    for v in rows:
        aid,uid=v['aweme_id'],v['sec_uid']
        old=store.q('SELECT sec_uid FROM tracked_account_videos WHERE aweme_id=?',(aid,))
        if old and old[0]['sec_uid']!=uid:raise ValueError('缓存作者与监控身份不一致，未导入')
        existing=store.q('SELECT role FROM tracked_accounts WHERE sec_uid=?',(uid,))
        intelligence.upsert_account_identity(uid,v.get('account_name',''))
        if not existing:intelligence.set_account_role(uid,'peer')
        now=store.now_str();observed=v['fetched_at']
        store.x('''INSERT INTO tracked_account_videos(aweme_id,sec_uid,title,create_time,cover_path,first_seen,last_seen,metric_status)
            VALUES(?,?,?,?,?,?,?,'external') ON CONFLICT(aweme_id) DO UPDATE SET
            title=excluded.title,cover_path=COALESCE(tracked_account_videos.cover_path,excluded.cover_path),last_seen=excluded.last_seen''',
            (aid,uid,v['title'],v.get('create_time'),v.get('cover_path') or v.get('cover_url'),now,now))
        store.x('UPDATE tracked_accounts SET avatar_path=COALESCE(NULLIF(avatar_path,\'\'),?),nickname=COALESCE(NULLIF(?,\'\'),nickname) WHERE sec_uid=?',
                (v.get('avatar_path') or v.get('avatar_url'),v.get('account_name'),uid))
        metrics=[v.get(k) for k in ['play_count','digg_count','comment_count','collect_count','share_count']]
        store.x('INSERT OR REPLACE INTO external_video_observations VALUES(?,?,?,?,?,?,?,?,?)',
                (aid,observed[:10],observed,*metrics,'mcpro_discovery'))
    return [{'aweme_id':v['aweme_id'],'sec_uid':v['sec_uid'],'title':v['title']} for v in rows]


def work(raw):
    normalized=douk.normalize(raw)
    if not normalized:return None
    author=raw.get('author') or {};video=raw.get('video') or {}
    return {'id':normalized['aweme_id'],'aweme_id':normalized['aweme_id'],'title':normalized['title'],
        'account_name':normalized['author_name'],'sec_uid':normalized['sec_uid'],
        'cover_url':normalized['cover_url'],'avatar_url':normalized['avatar_url'],
        'create_time':normalized['create_time'],'fans':douk.integer(author.get('follower_count'),True),
        'duration':video.get('duration',0)/1000 if isinstance(video.get('duration'),(int,float)) else None,
        'play_count':normalized['play_count'],'board_play_count':None,
        **{k:normalized[k] for k in ['digg_count','comment_count','collect_count','share_count']},
        'index':None,'matched_keyword':raw.get('discovery_keyword',''),
        'is_ai_declared':raw.get('is_ai_generated') in (1,True),
        'url':'https://www.douyin.com/video/'+normalized['aweme_id'],'source':'native'}


def execute(q,job_id,cancelled=lambda:False):
    limit=q.get('limit',100);terms=words(q['keyword']) if q['source']=='native' else []
    options={'mode':'hot' if q['source']=='native_hot' else 'search','limit':limit,
             'per_keyword':math.ceil(limit/max(1,len(terms))),'days':q.get('days',7),'sort':q.get('native_sort',1)}
    result=integrations.crawl({'platform':'dy','mode':options['mode'] if options['mode']=='search' else 'hot_search',
        'input':','.join(terms),'count':options['per_keyword'],'comments':False,'max_comments':0,'discovery':options},job_id)
    capture=douk.read(douk.metadata(job_id).parent/'discovery-native.json',{})
    if q['source']=='native_hot':
        items=[{'id':str(r.get('group_id') or r['word']),'title':r['word'],'rank':r.get('position'),
                'heat':r.get('hot_value'),'rank_change':None,'topic_plays':None,'publish_count':None,
                'category':'抖音热榜','trend':[],'source':'native_hot'} for r in capture.get('hot',[])]
    else:items=[v for raw in capture.get('items',[]) if (v:=work(raw)) is not None][:limit]
    errors=[]
    if not items:errors.append({'error':result.get('message') or '未取得内容；未切换 TikHub，未产生接口费用'})
    elif not result.get('ok') and q['source']!='native_hot':
        errors=result.get('errors') or [{'error':'平台提前结束或部分关键词未完成，已保留取得的内容'}]
    return {'items':items,'errors':errors,'pages':capture.get('pages',0),'cancelled':cancelled()}
