"""Web bridge to the four-platform Downloader's native parsing API and media files."""
import ipaddress
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
from urllib.parse import urlsplit, urljoin

import requests
import corpus
import integrations
import store

DOMAINS={'dy':('douyin.com',),'xhs':('xiaohongshu.com','xhslink.com'),
         'bili':('bilibili.com','b23.tv'),'ks':('kuaishou.com','gifshow.com')}
CDNS=('douyinvod.com','toutiaovod.com','byteimg.com','ibyteimg.com','douyinpic.com','xhscdn.com',
      'xiaohongshu.com','bilivideo.com','bilivideo.cn','hdslb.com','kwaicdn.com','kwimgs.com','ksyuncdn.com','kuaishouvod.com')
DNS_CACHE={}


def verified_public_cdn(host):
    if not any(host==d or host.endswith('.'+d) for d in CDNS):
        return False
    if DNS_CACHE.get(host,0)>time.monotonic():
        return True
    # TUN/Fake-IP DNS maps public CDNs into 198.18/15 on this workstation.
    # Only allow known platform CDN names after independent HTTPS DNS verification.
    response=requests.get('https://dns.google/resolve',params={'name':host,'type':'A'},
        headers={'Accept':'application/dns-json'},timeout=8,allow_redirects=False)
    if response.status_code!=200:
        return False
    body=response.json()
    addresses=[answer.get('data','') for answer in body.get('Answer',[]) if answer.get('type')==1]
    if body.get('Status')==0 and addresses and all(ipaddress.ip_address(a).is_global for a in addresses):
        DNS_CACHE[host]=time.monotonic()+300
        return True
    return False


def content_url(value,platform):
    parsed=urlsplit(str(value))
    host=(parsed.hostname or '').lower()
    if parsed.scheme not in {'http','https'} or parsed.username or not any(host==d or host.endswith('.'+d) for d in DOMAINS[platform]):
        raise ValueError('请填写所选平台的作品或账号链接')
    return str(value)


def login(platform):
    response=requests.get('http://127.0.0.1:8274/api/cookies/'+platform,timeout=15)
    response.raise_for_status()
    data=response.json()
    value=data.get('data',{}).get('cookies') if data.get('isok') else None
    if not value:
        raise ValueError('此平台尚未同步登录，请在引擎管理中连接 CookieBridge 或填写本机 Cookie')
    return value


def query(path,payload):
    response=requests.post('http://127.0.0.1:8205/api/v1/'+path,json=payload,timeout=(10,90))
    response.raise_for_status()
    body=response.json()
    if not body.get('isok'):
        raise ValueError('解析接口未返回内容：'+str(body.get('msg') or '')[:180])
    return body.get('data') or {}


def public_media(value):
    parsed=urlsplit(value)
    if parsed.scheme not in {'https','http'} or parsed.username or not parsed.hostname or parsed.port not in {None,80,443}:
        raise ValueError('无效的媒体地址')
    fake=False
    for info in socket.getaddrinfo(parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80),type=socket.SOCK_STREAM):
        address=ipaddress.ip_address(info[4][0])
        if address.version==4 and address in ipaddress.ip_network('198.18.0.0/15'):
            fake=True
        elif not address.is_global:
            raise ValueError('媒体地址不是公开网络地址')
    if fake and not verified_public_cdn(parsed.hostname.lower()):
        raise ValueError('本机代理返回虚拟地址，CDN 的公开 DNS 验证未通过')
    return value


def download_file(url,target,platform,cancelled,progress,cookies=''):
    """Stream to a sibling .part; existing finished files are preserved on failure."""
    headers={'User-Agent':'Mozilla/5.0','Accept-Encoding':'identity','Referer':{'dy':'https://www.douyin.com/','xhs':'https://www.xiaohongshu.com/',
             'bili':'https://www.bilibili.com/','ks':'https://www.kuaishou.com/'}[platform]}
    part=target.with_suffix(target.suffix+'.part')
    offset=part.stat().st_size if part.is_file() else 0
    if offset:
        headers['Range']=f'bytes={offset}-'
    target.parent.mkdir(parents=True,exist_ok=True)
    session=requests.Session()
    response=None
    try:
        for _ in range(5):
            host=(urlsplit(url).hostname or '').lower()
            if platform=='bili' and cookies and (host.endswith('.bilibili.com') or host.endswith('.bilivideo.com')):
                headers['Cookie']=cookies
            else:
                headers.pop('Cookie',None)
            response=session.get(public_media(url),headers=headers,stream=True,timeout=(10,40),allow_redirects=False)
            if response.is_redirect:
                url=urljoin(url,response.headers.get('Location',''))
                response.close()
                continue
            response.raise_for_status()
            if any(mime in response.headers.get('Content-Type','').lower() for mime in ('text/','application/json')):
                raise ValueError('媒体地址返回了网页或错误信息，未保存为视频')
            break
        else:
            raise ValueError('媒体重定向过多')
        if response.status_code!=206:
            offset=0
        size=offset
        with part.open('ab' if offset else 'wb') as handle:
            for chunk in response.iter_content(256*1024):
                if cancelled():
                    raise ValueError('任务已取消')
                size+=len(chunk)
                if size>2*1024**3:
                    raise ValueError('单文件超过 2 GB，请分批或使用上游桌面下载器')
                handle.write(chunk)
                progress(size, offset+int(response.headers.get('Content-Length') or 0))
        expected=offset+int(response.headers.get('Content-Length') or 0)
        if not size or (expected and size!=expected):
            raise ValueError('媒体文件不完整')
        part.replace(target)
        return size
    finally:
        if response is not None:
            response.close()
        session.close()


def download_dash(content,target,cookies,cancelled,progress,job_id):
    import douk
    ffmpeg=shutil.which('ffmpeg') or douk.settings(False).get('ffmpeg')
    if not ffmpeg:
        raise ValueError('B站 DASH 视频需要 FFmpeg 合并音轨，请先配置 FFmpeg')
    video=target.with_suffix('.video.m4s')
    audio=target.with_suffix('.audio.m4s')
    for url,path in ((content['video_download_url'],video),((content.get('extria_info') or {})['audio_url'],audio)):
        if not path.is_file():
            download_file(url,path,'bili',cancelled,progress,cookies)
    if cancelled():
        raise ValueError('任务已取消')
    merged=target.with_suffix('.merging.mp4')
    import os
    with (douk.metadata(job_id).parent/'engine.log').open('ab') as log:
        proc=subprocess.Popen([ffmpeg,'-nostdin','-y','-i',str(video),'-i',str(audio),'-map','0:v:0','-map','1:a:0',
            '-c','copy','-movflags','+faststart',str(merged)],stdout=log,stderr=log,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        with douk.LOCK:
            douk.ACTIVE[job_id]=proc
        try:
            code=proc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            douk.terminate(proc)
            raise ValueError('音视频合并超时，临时文件已保留')
    if code or not merged.is_file() or cancelled():
        raise ValueError('音视频合并未完成，临时文件已保留')
    merged.replace(target)
    video.unlink();audio.unlink()


def store_content(content,platform,job_id):
    raw={**content,'interaction':content.get('interaction') or {}}
    author=content.get('author') or {}
    stats=raw['interaction']
    row={'platform':platform,'content_id':str(content.get('id') or ''),'title':content.get('title') or content.get('desc') or '',
         'description':content.get('desc') or '', 'url':corpus.safe_url(content.get('url')),
         'cover_url':corpus.safe_url(content.get('cover_url')), 'author_name':author.get('nickname') or '',
         'creator_hash':'', 'published_at':corpus.published((content.get('extria_info') or {}).get('create_time')),
         'play_count':corpus.number(stats.get('play_count'),True),'digg_count':corpus.number(stats.get('liked_count')),
         'comment_count':corpus.number(stats.get('comment_count')),'collect_count':corpus.number(stats.get('collected_count')),
         'share_count':corpus.number(stats.get('share_count'))}
    if not row['content_id']:
        raise ValueError('解析结果缺少作品 ID')
    corpus.save_content(row,raw,'mcpro_downloader',job_id)
    row['download_url']=corpus.safe_url(content.get('video_download_url'))
    row['image_urls']=[corpus.safe_url(url) for url in content.get('image_urls') or []]
    if platform=='dy' and author.get('sec_uid'):
        import douk
        native={'aweme_id':row['content_id'],'desc':row['title'],
          'author':{'sec_uid':author['sec_uid'],'nickname':author.get('nickname'),'avatar_thumb':{'url_list':[author.get('avatar') or '']}},
          'video':{'cover':{'url_list':[row['cover_url']]}},
          'statistics':{k:row.get(k) for k in ('play_count','digg_count','comment_count','collect_count','share_count')}}
        douk.import_rows([native],known_only=True,source='mcpro_downloader')
    return row


def execute(request,job_id):
    import douk
    platform=request.get('platform','dy')
    mode=request.get('mode','detail')
    if platform not in DOMAINS or mode not in {'detail','creator','creator_contents'}:
        raise ValueError('不支持的解析平台或类型')
    integrations.start('mcpro-downloader');integrations.start('mcpro-cookie')
    if platform=='dy':
        integrations.sync_douyin()
    cookies=login(platform)
    payload={'platform':platform,'cookies':cookies}
    if mode=='creator':
        data=query('creator_query',{**payload,'creator_url':content_url(request.get('input','').strip(),platform)})
        return {'ok':True,'data':[{**data,'platform':platform,'name':data.get('nickname')}],'creator_id':data.get('user_id')}
    if mode=='creator_contents':
        creator=str(request.get('creator_id') or '').strip()
        if not re.fullmatch(r'[\w.-]{1,250}',creator):
            raise ValueError('请先解析账号，取得账号 ID')
        data=query('creator_contents',{**payload,'creator_id':creator,'cursor':str(request.get('cursor') or '')[:1000]})
        contents=data.get('contents') or []
    else:
        values=[v.strip() for v in str(request.get('input') or '').splitlines() if v.strip()]
        if not 1<=len(values)<=100:
            raise ValueError('每次填写 1 至 100 条作品链接')
        contents=[]
        for value in values:
            if douk.job(job_id)['status']=='cancelled':
                break
            parsed=query('content_detail',{**payload,'content_url':content_url(value,platform)})
            if parsed.get('content'):
                contents.append(parsed['content'])
        data={}
    result=[];errors=[]
    root=integrations.RUNTIME/'downloads'/platform
    cancelled=lambda:douk.job(job_id)['status']=='cancelled'
    for i,content in enumerate(contents):
        if cancelled():
            break
        try:
            row=store_content(content,platform,job_id)
            result.append(row)
            if request.get('download'):
                media=[]
                if content.get('video_download_url'):
                    media.append((content['video_download_url'],'.mp4'))
                else:
                    media.extend((url,'.jpg') for url in (content.get('image_urls') or []))
                if not media:
                    raise ValueError('解析未返回可下载的媒体地址')
                safe_id=re.sub(r'[^A-Za-z0-9_-]','_',row['content_id'])[:100]
                paths=[]
                for n,(url,suffix) in enumerate(media):
                    if suffix=='.jpg' and Path(urlsplit(url).path).suffix.lower() in {'.png','.jpg','.jpeg','.gif','.webp'}:
                        suffix=Path(urlsplit(url).path).suffix.lower()
                    target=root/(safe_id+('_'+str(n+1) if len(media)>1 else '')+suffix)
                    if not target.is_file() or target.stat().st_size==0:
                        progress=lambda done,total:douk.atomic(douk.metadata(job_id).parent/'progress.json',
                            {'done':i,'total':len(contents),'count':len(result),'bytes':done,'bytes_total':total})
                        if platform=='bili' and (content.get('extria_info') or {}).get('audio_url'):
                            download_dash(content,target,cookies,cancelled,progress,job_id)
                        else:
                            download_file(url,target,platform,cancelled,progress,cookies)
                    paths.append(str(target.relative_to(root)))
                row['files']=paths
        except (ValueError,requests.RequestException) as exc:
            errors.append({'index':i,'error':douk.clean(str(exc))[:200]})
        douk.atomic(douk.metadata(job_id).parent/'progress.json',{'done':i+1,'total':len(contents),'count':len(result)})
    return {'ok':bool(result),'data':result,'errors':errors,'next_cursor':data.get('next_cursor'),
            'has_more':bool(data.get('has_more')),'message':f'已解析 {len(result)} 条'+('，媒体已保存至素材库' if request.get('download') and not errors else '')}
