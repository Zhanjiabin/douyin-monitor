"""Bounded public search capture for the isolated MediaCrawlerPro interpreter."""
import json
from pathlib import Path


class Capture:
    def __init__(self,path,options):
        self.path=Path(path)
        self.limit=int(options['limit'])
        self.per_keyword=int(options.get('per_keyword',self.limit))
        self.items={}
        self.counts={}
        self.hot=[]
        self.pages=0
        self.cursors={}
        self.finished=set()

    def save(self):
        pending=self.path.with_suffix('.tmp')
        pending.write_text(json.dumps({'items':list(self.items.values()),'hot':self.hot,'pages':self.pages},ensure_ascii=False),encoding='utf-8')
        pending.replace(self.path)

    def remaining(self,keyword):
        if keyword in self.finished:return 0
        return min(self.limit-len(self.items),self.per_keyword-self.counts.get(keyword,0))

    def page(self,keyword,response):
        if not isinstance(response,dict) or not isinstance(response.get('data'),list):
            raise ValueError('抖音未返回搜索结果，可能需要验证或登录已失效；未切换付费接口')
        self.pages+=1
        current=self.cursors.get(keyword,0)
        cursor=response.get('cursor')
        self.cursors[keyword]=cursor if isinstance(cursor,int) and cursor>current else current+max(1,len(response['data']))
        accepted=[]
        for row in response['data']:
            if self.remaining(keyword)<=0:break
            work=row.get('aweme_info') or next(iter((row.get('aweme_mix_info') or {}).get('mix_items') or []),None)
            if not isinstance(work,dict) or not work.get('aweme_id'):continue
            aid=str(work['aweme_id'])
            if aid in self.items:continue
            self.items[aid]={**work,'discovery_keyword':keyword}
            self.counts[keyword]=self.counts.get(keyword,0)+1
            accepted.append({'aweme_info':work})
        self.save()
        if response.get('has_more') in (0,False):self.finished.add(keyword)
        return {**response,'data':accepted}


def install(client_class,options,path,sort_factory,publish_factory,hot_handler=None,video_channel=None):
    capture=Capture(path,options)
    if options.get('mode')=='hot':
        async def hot_only(self):
            capture.hot=await self.dy_client.get_hot_search_items()
            capture.save()
        hot_handler.handle=hot_only
        return capture
    original=client_class.search_info_by_keyword
    async def bounded(self,keyword,*args,**kwargs):
        if capture.remaining(keyword)<=0:return {'data':[],'has_more':0}
        kwargs['sort_type']=sort_factory(int(options.get('sort',1)))
        kwargs['publish_time']=publish_factory(int(options.get('days',7)))
        kwargs['offset']=capture.cursors.get(keyword,0)
        if video_channel is not None:kwargs['search_channel']=video_channel
        return capture.page(keyword,await original(self,keyword,*args,**kwargs))
    client_class.search_info_by_keyword=bounded
    return capture
