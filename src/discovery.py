"""Curated discovery workflows. Cache reads are free; transport is explicit and metered."""
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid

from config import ROOT, DATA_DIR
import corpus
import douk
import intelligence
import playback
import store
import tikhub

TZ = timezone(timedelta(hours=8))
LOCK = threading.RLock()
BILL = '/api/v1/douyin/billboard/'
INDEX = '/api/v1/douyin/index/'
SOURCES = {
    'lowfan': ('低粉爆款', BILL+'fetch_hot_total_low_fan_list', 'works'),
    'search': ('关键词作品', '/api/v1/douyin/search/fetch_video_search_v3', 'works'),
    'completion': ('高完播率榜', BILL+'fetch_hot_total_high_play_list', 'works'),
    'likes': ('高点赞率榜', BILL+'fetch_hot_total_high_like_list', 'works'),
    'followers': ('高涨粉率榜', BILL+'fetch_hot_total_high_fan_list', 'works'),
    'rising': ('上升热点', BILL+'fetch_hot_rise_list', 'hot'),
    'topics': ('话题飙升', BILL+'fetch_hot_total_high_topic_list', 'hot'),
    'searches': ('搜索飙升', BILL+'fetch_hot_total_high_search_list', 'hot'),
    'keyword': ('关键词热度', INDEX+'fetch_multi_keyword_hot_trend', 'keywords'),
    'industry': ('行业消费与发布', INDEX+'fetch_content_consume_trend', 'industry'),
    'portrait': ('粉丝画像', BILL+'fetch_hot_account_fans_portrait_list', 'portrait'),
    'monitor': ('加入账号与作品监控', '/api/v1/douyin/app/v3/fetch_one_video_v3', 'monitor'),
}


def now():
    return datetime.now(TZ)


def init():
    intelligence.init_account_tables()
    for sql in (
        'CREATE TABLE IF NOT EXISTS discovery_snapshots(id TEXT PRIMARY KEY,query_key TEXT NOT NULL,source TEXT NOT NULL,query_json TEXT NOT NULL,payload_json TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL,job_id TEXT)',
        'CREATE INDEX IF NOT EXISTS discovery_query_time ON discovery_snapshots(query_key,created_at)',
        'CREATE TABLE IF NOT EXISTS discovery_settings(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS discovery_watches(id TEXT PRIMARY KEY,keyword TEXT NOT NULL UNIQUE,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS discovery_works(aweme_id TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS discovery_rules(id TEXT PRIMARY KEY,title TEXT NOT NULL,query_json TEXT NOT NULL,enabled INTEGER NOT NULL,interval_hours INTEGER NOT NULL,next_at TEXT NOT NULL,revision TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS discovery_slots(rule_id TEXT NOT NULL,slot TEXT NOT NULL,revision TEXT NOT NULL,status TEXT NOT NULL,job_id TEXT,error TEXT,PRIMARY KEY(rule_id,slot,revision))',
    ):
        store.x(sql)


def settings():
    init()
    rows = store.q('SELECT value FROM discovery_settings WHERE id=1')
    return {'allow_keyword_trends': False, **(json.loads(rows[0]['value']) if rows else {})}


def save_settings(data):
    if set(data) != {'allow_keyword_trends'} or not isinstance(data['allow_keyword_trends'], bool):
        raise ValueError('请选择关键词专用接口许可')
    init()
    store.x('INSERT OR REPLACE INTO discovery_settings VALUES(1,?)', (json.dumps(data),))
    return settings()


def categories():
    return json.loads((ROOT/'integrations/discovery-categories.json').read_text(encoding='utf-8'))['categories']


def integer(value,minimum,maximum,label):
    if isinstance(value,bool) or not re.fullmatch(r'\d+',str(value)) or not minimum <= int(value) <= maximum:
        raise ValueError(label+'超出范围')
    return int(value)


def query(data):
    if not isinstance(data,dict):raise ValueError('查询格式错误')
    source=data.get('source','lowfan')
    if source not in SOURCES:raise ValueError('请选择有效的数据类型')
    keyword=str(data.get('keyword') or '').strip()
    if len(keyword)>80:raise ValueError('关键词最多 80 个字符')
    category=str(data.get('category') or '')
    if category and category not in {str(c['value']) for c in categories()}:raise ValueError('请选择接口支持的平台分类')
    result={'source':source}
    section=SOURCES[source][2]
    if section in {'works','hot'}:
        result.update(keyword=keyword)
        if source not in {'rising','searches','search'}:result['category']=category
        if source=='rising':
            result['order']=data.get('order','rank_diff')
            if result['order'] not in {'rank','rank_diff'}:raise ValueError('热点排序无效')
        elif source=='search':
            if not keyword:raise ValueError('请输入搜索关键词')
            result['days']=integer(data.get('days',7),0,30,'发布时间')
            if result['days'] not in {0,3,7,30}:raise ValueError('发布时间选项无效')
            result['duration']=integer(data.get('duration',0),0,9,'时长')
            if result['duration'] not in {0,1,6,7,8,9}:raise ValueError('时长选项无效')
        else:
            result['window']=integer(data.get('window',168),1,168,'榜单周期')
            if result['window'] not in {1,24,72,168}:raise ValueError('榜单周期无效')
    if section in {'keywords','industry'}:
        if section=='keywords':
            if not keyword or re.search(r'[,，;；\n]',keyword):raise ValueError('每次只查询一个关键词')
            result['keyword']=keyword
        else:
            if not category:raise ValueError('行业趋势需要选择具体平台分类')
            result['category']=category
        end=now().date()-timedelta(days=1)
        try:
            result['start_date']=datetime.strptime(data.get('start_date') or (end-timedelta(days=6)).isoformat(),'%Y-%m-%d').date().isoformat()
            result['end_date']=datetime.strptime(data.get('end_date') or end.isoformat(),'%Y-%m-%d').date().isoformat()
        except (ValueError,TypeError):raise ValueError('日期请使用 YYYY-MM-DD') from None
        a,b=datetime.fromisoformat(result['start_date']).date(),datetime.fromisoformat(result['end_date']).date()
        if not 0 <= (b-a).days <= 90 or b>now().date():raise ValueError('请选择最多 91 天的有效日期范围')
    if section=='portrait':
        uid=str(data.get('sec_uid',''))
        if not store.q('SELECT 1 FROM tracked_accounts WHERE sec_uid=?',(uid,)):raise ValueError('请先将账号加入工作台')
        result.update(sec_uid=uid,dimension=integer(data.get('dimension',2),1,8,'画像维度'))
    if section=='monitor':
        result['aweme_ids']=playback.ids(data.get('aweme_ids') or [data.get('aweme_id','')])
        if len(result['aweme_ids'])>20:raise ValueError('每次最多选择 20 条作品')
        if len(result['aweme_ids'])==1:result['aweme_id']=result['aweme_ids'][0]
    return result


def key(q):
    return hashlib.sha256(json.dumps(q,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def latest(q):
    init()
    rows=store.q('SELECT * FROM discovery_snapshots WHERE query_key=? AND status IN (\'completed\',\'partial\') ORDER BY created_at DESC,id DESC LIMIT 1',(key(q),))
    if not rows:return None
    row=rows[0]
    return {**json.loads(row['payload_json']),'snapshot_id':row['id'],'created_at':row['created_at'],'status':row['status']}


def cached(data):
    init();q=query(data)
    snap=latest(q)
    return {'query':q,'source_title':SOURCES[q['source']][0], 'snapshot':snap,'settings':settings()}


def api_request(path,params=None,body=None):
    op=next((o for o in tikhub.operations().values() if o['path']==path),None)
    if op is None:raise ValueError('固定版本中未找到此接口')
    result={'operation_id':op['operationId'],'params':params or {},'page_limit':1,'import_results':False}
    if body is not None:result['body_json']=json.dumps(body,ensure_ascii=False)
    return result


def prepare(data):
    init();q=query(data);source=q['source'];section=SOURCES[source][2]
    pages=integer(data.get('pages',1),1,3,'抓取页数')
    start=integer(data.get('page_start',1),1,50,'起始页数')
    if section not in {'works','hot'} or source=='search':
        if pages!=1 or start!=1:raise ValueError('该查询只支持单次抓取')
    previous=latest(q) if start>1 else None
    if start>1 and (not previous or previous.get('next_page')!=start):raise ValueError('缓存分页已变化，请重新搜索并预览')
    requests=[];reason=''
    if section in {'works','hot'}:
        for page in range(start,start+pages):
            if source=='search':
                requests.append(api_request(SOURCES[source][1],body={'query':q['keyword'],'date_type':q['days'],'duration_type':q['duration'],'label_type':0}))
            elif source=='rising':
                requests.append(api_request(SOURCES[source][1],{'page':page,'page_size':20,'order':q['order'],'keyword':q['keyword']}))
            elif source=='searches':
                requests.append(api_request(SOURCES[source][1],body={'page_num':page,'page_size':20,'date_window':q['window'],'keyword':q['keyword']}))
            else:
                requests.append(api_request(SOURCES[source][1],body={'page':page,'page_size':20,'date_window':q['window'],'keyword':q['keyword'],'tags':[{'value':int(q['category'])}] if q['category'] else []}))
    elif source=='keyword':
        if not settings()['allow_keyword_trends']:reason='关键词趋势专用接口尚未许可，请在设置开启；每次仅查询 1 个词'
        requests.append(api_request(SOURCES[source][1],{'keyword_list':q['keyword'],'start_date':q['start_date'].replace('-',''),'end_date':q['end_date'].replace('-',''),'app_name':'aweme','region':''}))
    elif source=='industry':
        params={'tag_id':q['category'],'start_date':q['start_date'].replace('-',''),'end_date':q['end_date'].replace('-','')}
        requests=[api_request(SOURCES[source][1],params),api_request(INDEX+'fetch_content_publish_trend',params)]
    elif source=='portrait':requests=[api_request(SOURCES[source][1],{'sec_uid':q['sec_uid'],'option':q['dimension']})]
    elif source=='monitor':requests=[api_request(SOURCES[source][1],{'aweme_id':aid}) for aid in q['aweme_ids']]
    previews=[tikhub.plan(r) for r in requests]
    calls=sum(p['calls'] for p in previews)
    cfg=playback.settings()
    reason=reason or next((p['reason'] for p in previews if not p['allowed']),'')
    if calls>cfg['max_calls_per_job']:reason='预计请求数超过单次任务上限'
    if calls+tikhub.usage()>cfg['daily_call_limit']:reason='预计请求数超过今日剩余额度'
    canonical={'query':q,'pages':pages,'page_start':start,'base_snapshot':previous.get('snapshot_id') if previous else None,
               'spec_version':tikhub.spec()['info']['version']}
    fingerprint=key(canonical)
    return {'query':q,'pages':pages,'page_start':start,'requests':requests,'fingerprint':fingerprint,
            'calls':calls,'allowed':not reason,'reason':reason,'title':SOURCES[source][0],
            'today_calls':tikhub.usage(),'endpoints':[tikhub.operation(r['operation_id'])['path'] for r in requests],
            'base_snapshot':canonical['base_snapshot']}


def plan(data):
    return {k:v for k,v in prepare(data).items() if k!='requests'}


def submit(data):
    p=prepare(data)
    if data.get('confirmed') is not True or data.get('fingerprint')!=p['fingerprint']:raise ValueError('条件已变化，请重新预览并确认')
    if not p['allowed']:raise ValueError(p['reason'])
    return douk.submit({'endpoint':'discovery','query':p['query'],'pages':p['pages'],'page_start':p['page_start'],
                        'fingerprint':p['fingerprint'],'confirmed':True,'title':p['title']})


def unwrap(body):
    node=body
    for _ in range(5):
        if not isinstance(node,dict):break
        for code in ('code','status_code'):
            if code in node and node[code] not in (0,200,'0','200',None):raise ValueError('供应商内部返回错误，原始回执已保存')
        if node.get('success') is False:raise ValueError('供应商内部返回失败，原始回执已保存')
        if 'data' not in node:break
        node=node['data']
    return node


def value(row,*names):
    for name in names:
        v=row.get(name)
        if v is not None and v!='':return v
    return None


def number(v):
    if isinstance(v,bool):return None
    try:
        n=float(v)
        return n if math.isfinite(n) and n>=0 else None
    except (ValueError,TypeError):return None


def published(v):
    if isinstance(v,(int,float)) and v>0:return datetime.fromtimestamp(v,TZ).isoformat()
    if isinstance(v,str):
        try:return datetime.fromisoformat(v).replace(tzinfo=TZ).isoformat()
        except ValueError:pass
    return None


def public_url(v):
    from urllib.parse import urlsplit
    if not isinstance(v,str):return ''
    p=urlsplit(v)
    return v if p.scheme=='https' and not p.username and not p.password else ''


def parse_works(node,source):
    rows=node if isinstance(node,list) else node.get('objs',node.get('items',[])) if isinstance(node,dict) else []
    result=[]
    for r in rows:
        if not isinstance(r,dict):continue
        aid=str(value(r,'item_id','itemId','aweme_id') or '')
        if not re.fullmatch(r'\d{10,25}',aid):continue
        result.append({'id':aid,'aweme_id':aid,'title':str(value(r,'item_title','itemTitle','title','desc') or aid),
            'account_name':str(value(r,'nick_name','nickname','author_name') or ''),
            'sec_uid':str(value(r,'sec_uid','sec_user_id') or ''),
            'fans':number(value(r,'fans_cnt','fans','follower_count')),
            'cover_url':public_url(value(r,'item_cover_url','thumbnail','cover_url')),
            'avatar_url':public_url(value(r,'avatar_url','avatarUrl')),
            'published_at':published(value(r,'publish_time','createTime','create_time')),
            'duration':(number(r.get('item_duration'))/1000 if number(r.get('item_duration')) is not None else number(r.get('duration'))),
            'image_count':number(r.get('image_cnt')),
            'play_count':number(value(r,'play_count','total_play_count')),
            'board_play_count':number(r.get('play_cnt')),
            'digg_count':number(value(r,'like_cnt','likes','digg_count')),
            'comment_count':number(value(r,'comment_cnt','comments','comment_count')),
            'share_count':number(value(r,'share_cnt','shares','share_count')),
            'index':number(value(r,'index','score')),
            'completion_rate':number(value(r,'play_over_rate','finish_rate')),
            'like_rate':number(r.get('like_rate')),'follow_rate':number(r.get('follow_rate')),
            'url':'https://www.douyin.com/video/'+aid,'source':source})
    return result


def parse_hot(node,source):
    rows=node if isinstance(node,list) else node.get('objs',node.get('items',[])) if isinstance(node,dict) else []
    result=[]
    for index,r in enumerate(rows):
        if not isinstance(r,dict):continue
        word=value(r,'sentence','challenge_name','key_word','topic_name','keyword','word','name','topic','search_word')
        if not word:continue
        result.append({'id':str(value(r,'sentence_id','challenge_id','topic_id','id') or word),'title':str(word),
          'rank':number(value(r,'rank','real_rank','origin_rank','index')) or index+1,'rank_change':r.get('rank_diff'),
          'heat':number(value(r,'hot_score','search_score','hot_value','search_count','score','heat')),
          'topic_plays':number(r.get('play_cnt')),'publish_count':number(r.get('publish_cnt')),
          'category':str(value(r,'sentence_tag_name','tag_name') or ''),'trend':r.get('trends') or [],'source':source})
    return result


def leaf_series(node):
    """Keep each returned metric and its unit separate; never merge unlike axes."""
    found=[]
    def walk(n,path):
        if isinstance(n,list) and n and all(isinstance(v,dict) for v in n):
            points=[]
            for r in n:
                x=value(r,'date','datetime','time','key','timestamp','stat_date','name')
                if x is None:continue
                metrics={k:number(v) for k,v in r.items() if k not in {'date','datetime','time','key','timestamp','stat_date','name'} and number(v) is not None}
                if metrics:points.append({'x':str(x),'metrics':metrics})
            if points:
                fields={k for point in points for k in point['metrics']}
                points=[{'x':str(value(r,'date','datetime','time','key','timestamp','stat_date','name')),
                         'metrics':{k:number(r.get(k)) for k in fields}} for r in n
                        if value(r,'date','datetime','time','key','timestamp','stat_date','name') is not None]
                found.append({'name':path or '趋势','points':points})
            else:
                for i,v in enumerate(n):walk(v,path+'/'+str(i))
        elif isinstance(n,dict):
            # Date/value mappings also occur in provider responses.
            dated=[{'x':str(k),'metrics':{'value':number(v)}} for k,v in n.items() if re.fullmatch(r'\d{4}[-/]?\d{2}[-/]?\d{2}',str(k)) and number(v) is not None]
            if dated:found.append({'name':path or '趋势','points':dated})
            for k,v in n.items():
                if isinstance(v,(list,dict)):walk(v,path+'/'+str(k))
    walk(node,'')
    return found


def normalize(body,q):
    node=unwrap(body);section=SOURCES[q['source']][2]
    if isinstance(node,dict) and (node.get('BaseResp') or {}).get('StatusCode',0) not in (0,'0',None):
        raise ValueError('供应商返回内部错误，已保存回执且未重试')
    result={'items':[],'series':[],'raw_data':node}
    if section=='works':result['items']=parse_works(node,q['source'])
    elif section=='hot':result['items']=parse_hot(node,q['source'])
    else:
        result['series']=leaf_series(node)
        for series in result['series']:
            series['unit']='index' if 'tgi' in series['name'].lower() else 'ratio' if section=='portrait' else 'provider_rate' if section=='industry' else 'index'
    if isinstance(node,dict) and isinstance(node.get('page'),dict):result['provider_total']=number(node['page'].get('total'))
    elif isinstance(node,dict):result['provider_total']=number(value(node,'total_count','total'))
    if isinstance(node,dict):result['provider_updated_at']=node.get('last_update_time')
    return result


def execute(request,progress=lambda _:None,cancelled=lambda:False):
    data={**request.get('query',{}),'pages':request.get('pages',1),'page_start':request.get('page_start',1)}
    p=prepare(data)
    if not p['allowed'] or request.get('confirmed') is not True or request.get('fingerprint')!=p['fingerprint']:
        raise ValueError(p['reason'] or '条件、授权或缓存已变化，请重新预览')
    q=p['query'];items=[];series=[];receipts=[];errors=[];raw=[];attempted=0;total=None;provider_updated=None
    for req in p['requests']:
        if cancelled():break
        if request.get('discovery_rule') and not rule_allowed(request):
            errors.append({'error':'发现计划已暂停或修改，未继续请求'});break
        # Revalidate the special permission before every possible paid request.
        if q['source']=='keyword' and not settings()['allow_keyword_trends']:
            errors.append({'error':'关键词专用接口许可已关闭'});break
        current=prepare(data)
        if current['fingerprint']!=p['fingerprint']:
            errors.append({'error':'授权或条件已变化，已停止请求'});break
        if attempted>=playback.settings()['max_calls_per_job']:
            errors.append({'error':'已达到当前单次请求上限，停止后续请求'});break
        preview=tikhub.plan(req)
        if not preview['allowed']:
            errors.append({'error':preview['reason']});break
        response=tikhub.execute({**req,'allow_charges':True,'fingerprint':preview['fingerprint']},cancelled=cancelled)
        attempted+=response['attempted'];receipts.extend(response['receipts']);errors.extend(response['errors'])
        for receipt in response['receipts']:
            if not receipt.get('ok'):continue
            try:
                parsed=normalize(receipt['response'],q)
                if q['source']=='monitor':
                    detail=unwrap(receipt['response'])
                    if isinstance(detail,dict):detail=detail.get('aweme_detail',detail)
                    aid=req['params']['aweme_id']
                    if not isinstance(detail,dict) or str(detail.get('aweme_id'))!=aid or not (detail.get('author') or {}).get('sec_uid'):
                        raise ValueError('作品或作者身份未核实，未加入监控')
                    uid=str(detail['author']['sec_uid'])
                    existing=store.q('SELECT role FROM tracked_accounts WHERE sec_uid=?',(uid,))
                    douk.import_rows([detail],source='tikhub_app_v3')
                    if not existing:intelligence.set_account_role(uid,'peer')
                    items.append({'aweme_id':aid,'sec_uid':uid,'title':detail.get('desc',''),'account_name':detail['author'].get('nickname','')})
                else:items.extend(parsed['items'])
                label='发布趋势' if req['operation_id'].startswith('fetch_content_publish_trend') else '消费趋势' if q['source']=='industry' else ''
                series.extend([{**s,'name':label+s['name']} for s in parsed['series']])
                raw.append({'endpoint':tikhub.operation(req['operation_id'])['path'],'data':parsed['raw_data']})
                total=parsed.get('provider_total',total);provider_updated=parsed.get('provider_updated_at',provider_updated)
            except (ValueError,TypeError,KeyError) as exc:
                errors.append({'error':str(exc)});break
        progress({'done':attempted,'total':p['calls'],'count':len(items)})
        if errors:break
    timestamp=now().isoformat()
    for item in items:item['fetched_at']=timestamp
    if SOURCES[q['source']][2]=='works' and items:
        import shutil
        if shutil.disk_usage(DATA_DIR).free>256*1024**2:
            from media_cache import cache_images
            images=cache_images([i.get(field) for i in items for field in ('cover_url','avatar_url')])
        else:images={}
        for item in items:
            item['cover_path']=images.get(item.get('cover_url')) or item.get('cover_url')
            item['avatar_path']=images.get(item.get('avatar_url')) or item.get('avatar_url')
            store.x('INSERT OR REPLACE INTO discovery_works VALUES(?,?,?)',(item['aweme_id'],json.dumps(item,ensure_ascii=False),timestamp))
    fresh_count=len(items)
    previous=latest(q) if p['page_start']>1 else None
    if previous and previous['snapshot_id']!=p['base_snapshot']:errors.append({'error':'分页期间缓存变化，已单独保存当前回执'})
    merged={i['id']:i for i in (previous.get('items',[]) if previous else [])}
    for item in items:merged[item.get('id',item.get('aweme_id'))]=item
    items=list(merged.values())
    was_cancelled=cancelled()
    if was_cancelled:errors.append({'error':'任务已取消；已完成的请求仍已保存'})
    next_page=None
    if SOURCES[q['source']][2] in {'works','hot'} and q['source']!='search' and raw and not errors:
        next_page=p['page_start']+len(raw)
        if (total is not None and next_page>math.ceil(total/20)) or not fresh_count:next_page=None
    payload={'items':items,'series':series,'raw':raw,'receipts':receipts,'errors':errors,'next_page':next_page,
             'provider_total':total,'provider_updated_at':provider_updated,'attempted':attempted,'source_title':p['title']}
    ok=bool(raw or (q['source']=='monitor' and items))
    if ok:
        store.x('INSERT INTO discovery_snapshots VALUES(?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,key(q),q['source'],json.dumps(q,ensure_ascii=False),json.dumps(payload,ensure_ascii=False),timestamp,'partial' if errors else 'completed',request.get('job_id','')))
    saved=f'{len(series)} 组指标' if series else f'{len(items)} 条结果'
    return {'ok':ok,'data':items,'errors':errors,'receipts':receipts,'attempted':attempted,'requested':p['calls'],
            'query':q,'cancelled':was_cancelled,'message':('已取消；已完成的请求仍已保存' if was_cancelled else f'完成 {attempted} 次请求，保存 {saved}')}


def watches(data=None):
    init()
    if data is not None:
        if data.get('delete'):
            previous=store.q('SELECT keyword FROM discovery_watches WHERE id=?',(str(data['delete']),))
            if previous:
                for rule in store.q('SELECT id,query_json FROM discovery_rules'):
                    q=json.loads(rule['query_json'])
                    if q.get('source')=='keyword' and q.get('keyword')==previous[0]['keyword']:
                        store.x('UPDATE discovery_rules SET enabled=0,revision=? WHERE id=?',(uuid.uuid4().hex,rule['id']))
            store.x('DELETE FROM discovery_watches WHERE id=?',(str(data['delete']),))
        elif data.get('id'):
            if not isinstance(data.get('enabled'),bool):raise ValueError('追踪状态无效')
            store.x('UPDATE discovery_watches SET enabled=? WHERE id=?',(int(data['enabled']),str(data['id'])))
        else:
            word=str(data.get('keyword','')).strip()
            if not 1<=len(word)<=80 or re.search(r'[,，;；\n]',word):raise ValueError('请输入一个追踪关键词')
            store.x('INSERT OR IGNORE INTO discovery_watches VALUES(?,?,1,?)',(uuid.uuid4().hex,word,now().isoformat()))
    rows=store.q('SELECT * FROM discovery_watches ORDER BY created_at DESC')
    for row in rows:
        snapshots=store.q("SELECT query_json,created_at FROM discovery_snapshots WHERE source='keyword' AND json_extract(query_json,'$.keyword')=? ORDER BY created_at DESC LIMIT 1",(row['keyword'],))
        row['last_query']=json.loads(snapshots[0]['query_json']) if snapshots else None
        row['last_fetched_at']=snapshots[0]['created_at'] if snapshots else None
    return {'items':rows}


def rules():
    init();rows=store.q('SELECT * FROM discovery_rules ORDER BY title')
    statuses={j['id']:j['status'] for j in douk.jobs()}
    for row in rows:
        row['query']=json.loads(row.pop('query_json'))
        row['recent_runs']=store.q('SELECT slot,status,job_id,error FROM discovery_slots WHERE rule_id=? ORDER BY slot DESC LIMIT 5',(row['id'],))
        for run in row['recent_runs']:
            if run['job_id'] in statuses:
                run['status']=statuses[run['job_id']]
    return {'items':rows}


def save_rule(data):
    init()
    if data.get('delete'):
        store.x('DELETE FROM discovery_rules WHERE id=?',(str(data['delete']),));return rules()
    if data.get('id') and set(data)<= {'id','enabled'}:
        if not isinstance(data.get('enabled'),bool):raise ValueError('计划开关无效')
        if data['enabled']:
            rows=store.q('SELECT query_json FROM discovery_rules WHERE id=?',(str(data['id']),))
            if not rows:raise ValueError('计划不存在')
            p=plan(json.loads(rows[0]['query_json']))
            if not p['allowed']:raise ValueError(p['reason'])
        store.x('UPDATE discovery_rules SET enabled=?,revision=? WHERE id=?',(int(data['enabled']),uuid.uuid4().hex,str(data['id'])));return rules()
    q=query(data.get('query',{}))
    if SOURCES[q['source']][2] not in {'works','hot','keywords','industry'}:raise ValueError('粉丝画像仅按需查询')
    hours=integer(data.get('interval_hours',24),1,168,'更新间隔')
    enabled=data.get('enabled',False)
    if not isinstance(enabled,bool):raise ValueError('计划开关无效')
    if enabled:
        p=plan(q)
        if not p['allowed']:raise ValueError(p['reason'])
    same=store.q('SELECT id FROM discovery_rules WHERE query_json=?',(json.dumps(q,ensure_ascii=False),))
    identity=str(data.get('id') or (same[0]['id'] if same else uuid.uuid4().hex))
    title=(str(data.get('title','')).strip() or SOURCES[q['source']][0]+' '+q.get('keyword',''))[:80]
    store.x('INSERT OR REPLACE INTO discovery_rules VALUES(?,?,?,?,?,?,?)',(identity,title,json.dumps(q,ensure_ascii=False),int(enabled),hours,(now()+timedelta(hours=hours)).isoformat(),uuid.uuid4().hex))
    return rules()


def rule_allowed(request):
    rows=store.q('SELECT * FROM discovery_rules WHERE id=?',(request.get('discovery_rule'),))
    return bool(rows and rows[0]['enabled'] and rows[0]['revision']==request.get('discovery_revision'))


def tick(at=None):
    init();at=at or now()
    # Claim the latest due slot once; restarting never replays old paid queries.
    with LOCK:
        for rule in store.q('SELECT * FROM discovery_rules WHERE enabled=1 AND next_at<=?',(at.isoformat(),)):
            slot=rule['next_at']
            try:store.x('INSERT INTO discovery_slots VALUES(?,?,?,\'claimed\',NULL,NULL)',(rule['id'],slot,rule['revision']))
            except sqlite3.IntegrityError:continue
            store.x('UPDATE discovery_rules SET next_at=? WHERE id=?',((at+timedelta(hours=rule['interval_hours'])).isoformat(),rule['id']))
            try:
                q=json.loads(rule['query_json'])
                # Keyword and industry schedules always roll forward a seven-day window.
                if q['source'] in {'keyword','industry'}:
                    end=at.date()-timedelta(days=1);q.update(start_date=(end-timedelta(days=6)).isoformat(),end_date=end.isoformat())
                p=plan(q)
                if not p['allowed']:raise ValueError(p['reason'])
                job=douk.submit({'endpoint':'discovery','query':q,'pages':1,'page_start':1,'confirmed':True,'fingerprint':p['fingerprint'],
                    'title':rule['title'],'discovery_rule':rule['id'],'discovery_revision':rule['revision']})
                store.x('UPDATE discovery_slots SET status=\'queued\',job_id=? WHERE rule_id=? AND slot=? AND revision=?',(job['id'],rule['id'],slot,rule['revision']))
            except Exception:
                store.x('UPDATE discovery_slots SET status=\'blocked\',error=? WHERE rule_id=? AND slot=? AND revision=?',('计划本轮未提交：权限或额度不足；不自动重试',rule['id'],slot,rule['revision']))


def catalog():
    init()
    return {'sources':[{'id':s,'title':v[0],'section':v[2]} for s,v in SOURCES.items() if v[2]!='monitor'],
            'categories':categories(),'settings':settings(),
            'accounts':[{'sec_uid':a['sec_uid'],'name':a.get('nickname') or a.get('note'),'role':a.get('role')} for a in intelligence.list_tracked_accounts()]}


def work(aid):
    init()
    rows=store.q('SELECT value FROM discovery_works WHERE aweme_id=?',(aid,))
    if not rows:return None
    v=json.loads(rows[0]['value'])
    v.update(create_time=int(datetime.fromisoformat(v['published_at']).timestamp()) if v.get('published_at') else None,
             play_source='discovery',play_date=v['fetched_at'][:10],topic='',monitored=False)
    return v


def comparison(aid,videos=None):
    """A checkable peer set; no claim that title/category caused distribution."""
    import statistics
    if videos is None:
        import insights
        videos=insights.dashboard(90,include_archived=True)['videos']
    current=next((v for v in videos if v['aweme_id']==aid),None)
    if not current or current.get('stale') or not current.get('play_count') or not current.get('age_band'):
        return {'available':False,'reason':'需要已监控作品的新鲜播放记录和发布时间，才能进行同口径比较。','samples':[]}
    fields=('sec_uid','age_band','play_source','play_scope','play_date')
    rows=[v for v in videos if v['aweme_id']!=aid and not v.get('stale') and v.get('play_count') and all(v.get(k)==current.get(k) for k in fields)]
    rows.sort(key=lambda v:v['play_count'],reverse=True)
    baseline=statistics.median(v['play_count'] for v in rows) if rows else None
    evidence=[{k:v.get(k) for k in ('aweme_id','title','play_count','create_time','play_date','play_source','play_scope','age_band')} for v in rows]
    return {'available':len(rows)>=5,'reason':'' if len(rows)>=5 else '可比作品不足 5 条，暂不计算表现倍数。',
            'scope':'同账号、同发布年龄区间、同播放来源与口径、同采集日',
            'sample_count':len(rows),'median_play':baseline,'current_play':current['play_count'],
            'ratio':round(current['play_count']/baseline,2) if len(rows)>=5 and baseline else None,
            'samples':evidence,'note':'表现差异是观测结果，不能证明标题、题材导致推荐，也不代表平台算法权重。'}


def export(data,fmt='csv'):
    snapshot=cached(data)
    if fmt=='json':return json.dumps(snapshot,ensure_ascii=False,indent=2).encode(), 'application/json; charset=utf-8'
    import csv,io
    output=io.StringIO()
    writer=csv.writer(output)
    payload=snapshot.get('snapshot') or {}
    def safe(v):
        return "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v
    if SOURCES[snapshot['query']['source']][2] in {'keywords','industry','portrait'}:
        writer.writerow(['序列','日期或分类','指标','数值','单位','采集时间'])
        for series in payload.get('series',[]):
            for point in series.get('points',[]):
                for metric,v in point['metrics'].items():
                    writer.writerow([safe(series['name']),safe(point['x']),safe(metric),v,series.get('unit',''),payload.get('created_at','')])
        return output.getvalue().encode('utf-8-sig'),'text/csv; charset=utf-8'
    items=payload.get('items',[])
    fields=['title','account_name','published_at','duration','play_count','board_play_count','digg_count','fans','index','like_rate','follow_rate','completion_rate','heat','rank','rank_change','topic_plays','publish_count','category','source','fetched_at','url']
    writer.writerow(['标题','作者','发布时间','时长秒','累计播放','榜单播放','点赞','作者粉丝','榜单分值或搜索指数','点赞率原值','涨粉率原值','完播率原值','热度','排名','排名变化','话题播放','参与作品数','分类','来源','采集时间','原视频'])
    for row in items:
        cells=[]
        for field in fields:
            v=row.get(field,'')
            cells.append(safe(v))
        writer.writerow(cells)
    return output.getvalue().encode('utf-8-sig'),'text/csv; charset=utf-8'
