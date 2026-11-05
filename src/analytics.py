"""Account-scoped statistics from saved observations, without provider calls."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median

import insights
import intelligence
import metric_history as history
import store

METRICS=history.METRICS


def mid(rows):
    values=[v for v in rows if v is not None]
    return round(median(values),2) if values else None


def dashboard(account='',days=30,keyword=''):
    if str(days) not in {'7','30','90'}:raise ValueError('请选择近 7、30 或 90 天')
    keyword=str(keyword or '').strip()
    if len(keyword)>120:raise ValueError('关键词最多 120 字')
    accounts=[{k:a.get(k) for k in ('sec_uid','nickname','note','role','enabled','avatar_path','follower_count')}
              for a in intelligence.tracked_account_summary()]
    if not account and accounts:
        account=next((a['sec_uid'] for a in accounts if a.get('enabled') and a['role']=='own'),
                     next((a['sec_uid'] for a in accounts if a.get('enabled')),accounts[0]['sec_uid']))
    selected=next((a for a in accounts if a['sec_uid']==account),None)
    if account and not selected:raise ValueError('账号不存在，请重新选择')
    days=int(days);end=date.fromisoformat(store.today_str());start=end-timedelta(days=days-1)
    if not selected:
        return {'accounts':accounts,'account':None,'days':days,'start':str(start),'end':str(end),
                'series':[],'works':[],'publication':[],'hours':[],'weekdays':[],'interactions':{},'updated_at':None}
    data=insights.dashboard(days,account=account,query=keyword,include_archived=True)
    works=data['videos'];identifiers={v['aweme_id'] for v in works}
    rows=[r for r in history.account(account,str(start),str(end)) if r['aweme_id'] in identifiers]
    daily=defaultdict(dict);latest={}
    for row in rows:
        aid=row['aweme_id'];point=daily[row['fetch_date']].setdefault(aid,{})
        current=latest.setdefault(aid,{})
        for key in METRICS:
            if row[key] is not None:
                point[key]=row[key];point['_'+key]=(row['source'],row['scope'])
                current[key]=row[key]
        point['updated_at']=row['observed_at'];current['metrics_updated_at']=row['observed_at']
    series=[]
    for offset in range(days):
        day=start+timedelta(days=offset);current=daily.get(str(day),{});previous=daily.get(str(day-timedelta(days=1)),{})
        point={'date':str(day),'samples':{},'growth':{},'growth_samples':{},
               'updated_at':max((r['updated_at'] for r in current.values()),default=None)}
        for key in METRICS:
            valid=[r[key] for r in current.values() if r.get(key) is not None]
            point[key]=sum(valid) if valid else None;point['samples'][key]=len(valid)
            deltas=[]
            for aid,row in current.items():
                prior=previous.get(aid,{})
                if row.get(key) is not None and prior.get(key) is not None and row.get('_'+key)==prior.get('_'+key) and row[key]>=prior[key]:
                    deltas.append(row[key]-prior[key])
            point['growth'][key]=sum(deltas) if deltas else None;point['growth_samples'][key]=len(deltas)
        series.append(point)
    # Latest values are limited to observations in the selected period. Missing
    # fields stay unknown rather than inheriting an unrelated or old snapshot.
    works=[{**v,**{key:latest.get(v['aweme_id'],{}).get(key) for key in METRICS},
            'metrics_updated_at':latest.get(v['aweme_id'],{}).get('metrics_updated_at')} for v in works]
    bins=defaultdict(list);pub_hours=defaultdict(list);weekdays=defaultdict(list)
    for work in works:
        try:published=datetime.fromtimestamp(work.get('create_time'),history.TZ)
        except (TypeError,ValueError,OSError,OverflowError):continue
        if not start<=published.date()<=end:continue
        bins[(published.weekday(),published.hour)].append(work)
        pub_hours[published.hour].append(work);weekdays[published.weekday()].append(work)
    def publication(values,**labels):
        return {**labels,'count':len(values),'known':sum(v['play_count'] is not None for v in values),
                'median_play':mid(v['play_count'] for v in values)}
    return {'accounts':accounts,'account':selected,'days':days,'start':str(start),'end':str(end),'keyword':keyword,
            'updated_at':max((r['observed_at'] for r in rows),default=None),'series':series,'works':works,
            'publication':[publication(values,weekday=weekday,hour=hour) for (weekday,hour),values in sorted(bins.items())],
            'hours':[publication(pub_hours[hour],hour=hour) for hour in range(24)],
            'weekdays':[publication(weekdays[weekday],weekday=weekday) for weekday in range(7)],
            'interactions':{key:sum(v[key] for v in works if v[key] is not None) if any(v[key] is not None for v in works) else None for key in METRICS[1:]},
            'method':'仅统计当前账号已保存的观测；增长比较相邻日期、同作品及相同来源与口径。发布时间使用北京时间。'}
