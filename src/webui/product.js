(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt=n=>n==null?'—':n>=1e8?(n/1e8).toFixed(2)+'亿':n>=1e4?(n/1e4).toFixed(1)+'万':Number(n).toLocaleString('zh-CN');
  const date=n=>n?new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(n*1000)):'—';
  const picture=(src,cls='')=>src?`<img class="${cls}" src="${esc(src)}" alt="" loading="lazy">`:`<span class="image-fallback ${cls}">影</span>`;
  const sources={discovery:'发现榜单',tikhub_app:'TikHub · 单作品统计',tikhub_app_batch:'TikHub · 批量统计',tikhub_app_v3:'TikHub · 作品详情',tikhub_xingtu:'星图统计',public_mix_metric:'合集统计',public:'主页作品',not_exposed:'主页作品',author_post:'主页作品',douk_detail:'视频详情',mcpro_python:'平台采集'};
  const roles={own:'自有',peer:'同行',unclassified:'未分组'};
  const statusNames={queued:'排队中',running:'进行中',completed:'已完成',partial:'部分完成',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
  const actionNames={account:'同步账号作品',video:'更新作品详情',playback:'更新作品数据',comments:'采集评论',download:'下载视频'};
  async function api(path,payload){const r=await fetch(path,payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await r.json();if(!r.ok||data.error)throw Error(data.error||'请求失败');return data;}
  const download=url=>url+(url.includes('?')?'&':'?')+'download=1';
  const pagers=new WeakMap();
  function pagination(container,options){
    let entry=pagers.get(container);
    if(!entry){
      container.classList.add('pagination');
      container.innerHTML='<span data-pager-total></span><div class="pagination-controls"><select data-pager-size aria-label="每页条数"></select><button type="button" class="btn" data-pager-prev aria-label="上一页">上一页</button><span data-pager-position></span><button type="button" class="btn" data-pager-next aria-label="下一页">下一页</button><label class="pager-jump-label"><span>到第</span><input type="number" min="1" step="1" inputmode="numeric" data-pager-jump aria-label="跳转页码"><span>页</span></label><button type="button" class="btn" data-pager-go>跳转</button></div>';
      const refs=Object.fromEntries(['total','size','prev','position','next','jump','go'].map(k=>[k,container.querySelector('[data-pager-'+k+']')]));
      entry={refs,options:null};pagers.set(container,entry);
      const go=(page,size=entry.options.pageSize)=>{const o=entry.options,max=Math.max(1,Math.ceil(o.total/size));o.onChange(Math.max(0,Math.min(page,max-1)),size);};
      refs.prev.onclick=()=>go(entry.options.page-1);refs.next.onclick=()=>go(entry.options.page+1);
      refs.size.onchange=()=>go(0,Number(refs.size.value));
      const jump=()=>{const max=Math.max(1,Math.ceil(entry.options.total/entry.options.pageSize)),value=refs.jump.value.trim();if(!/^\d+$/.test(value)||Number(value)<1||Number(value)>max){refs.jump.setCustomValidity(`请输入 1 到 ${max} 之间的整数页码`);refs.jump.reportValidity();return;}refs.jump.setCustomValidity('');go(Number(value)-1);};
      refs.jump.oninput=()=>refs.jump.setCustomValidity('');refs.go.onclick=jump;refs.jump.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();jump();}};
    }
    const max=Math.max(1,Math.ceil(options.total/options.pageSize));entry.options={...options,page:Math.max(0,Math.min(options.page,max-1))};
    const {refs}=entry,o=entry.options,sizes=o.sizes||[20,25,50,100],signature=sizes.join(',');
    if(refs.size.dataset.sizes!==signature){refs.size.innerHTML=sizes.map(n=>`<option value="${n}">${n} ${esc(o.unit||'条')}/页</option>`).join('');refs.size.dataset.sizes=signature;}
    if(o.ids)Object.entries(o.ids).forEach(([key,id])=>refs[key].id=id);
    container.classList.toggle('pagination-compact',!!o.compact);refs.prev.textContent=o.compact?'‹':'上一页';refs.next.textContent=o.compact?'›':'下一页';
    refs.total.textContent=o.totalText||`共 ${o.total} ${o.unit||'条'}`;refs.size.value=String(o.pageSize);refs.position.textContent=`${o.page+1} / ${max}`;
    refs.prev.disabled=o.page===0;refs.next.disabled=o.page+1>=max;refs.jump.disabled=refs.go.disabled=max<=1;refs.jump.max=String(max);
    if(document.activeElement!==refs.jump){refs.jump.value=String(o.page+1);refs.jump.setCustomValidity('');}
  }
  function initFilters(root=document){
    root.querySelectorAll('.filter-bar').forEach(form=>{
      if(form.dataset.filterReady)return;form.dataset.filterReady='true';
      form.querySelectorAll('label').forEach(label=>{const caption=label.querySelector('.filter-label')?.textContent;if(caption)label.querySelector('input,select')?.setAttribute('title',caption);});
      const extra=[...form.querySelectorAll('[data-filter-extra]')];if(!extra.length)return;
      const divider=document.createElement('span');divider.className='filter-extra-break';divider.dataset.filterExtra='';divider.hidden=true;divider.setAttribute('aria-hidden','true');form.insertBefore(divider,extra[0]);
      extra.unshift(divider);
      const toggle=document.createElement('button');toggle.type='button';toggle.className='btn filter-more';toggle.setAttribute('aria-expanded','false');toggle.textContent='＋ 展开筛选';
      const ids=extra.filter(el=>el!==divider).map((el,i)=>{el.id=form.id+'-extra-'+i;return el.id;});toggle.setAttribute('aria-controls',ids.join(' '));
      toggle.onclick=()=>{const open=toggle.getAttribute('aria-expanded')!=='true';toggle.setAttribute('aria-expanded',String(open));toggle.textContent=open?'－ 收起筛选':'＋ 展开筛选';extra.forEach(el=>el.hidden=!open);};form.append(toggle);
    });
  }
  const modalParents=new WeakMap(),modalFocus=new WeakMap();
  function openDialog(d){
    if(d.open)return;
    const current=document.querySelector('dialog[open]');
    if(current&&current!==d){modalFocus.set(d,document.activeElement);current.dataset.suspended='true';current.close();modalParents.set(d,current);}
    if(!d.dataset.resumeReady){d.dataset.resumeReady='true';d.addEventListener('close',()=>{
      if(d.dataset.suspended)return;
      const parent=modalParents.get(d);modalParents.delete(d);
      if(parent&&!parent.open){delete parent.dataset.suspended;parent.showModal();const focus=modalFocus.get(d);modalFocus.delete(d);if(focus?.isConnected)focus.focus({preventScroll:true});if(parent.dataset.needsRefresh){delete parent.dataset.needsRefresh;window.dispatchEvent(new Event('gc-data-updated'));}}
    });}
    d.showModal();
  }
  function dialog(id,title,cls=''){
    let d=document.getElementById(id);if(d)return d;
    d=document.createElement('dialog');d.id=id;d.className='product-dialog '+cls;
    d.innerHTML=`<header class="product-dialog-head"><h2 id="${id}Title">${esc(title)}</h2><button class="btn" data-dismiss>关闭</button></header><div class="product-dialog-body"></div>`;
    d.setAttribute('aria-labelledby',id+'Title');d.addEventListener('click',e=>{if(e.target.closest('[data-dismiss]'))d.close();if(e.target===d){const b=d.getBoundingClientRect();if(e.clientX<b.left||e.clientX>b.right||e.clientY<b.top||e.clientY>b.bottom)d.close();}});
    document.body.append(d);return d;
  }
  const busy=message=>`<div class="product-empty" role="status">${esc(message)}</div>`;
  function metrics(v){if(v.monitored===false)return `<div class="discovered-metrics">${[['榜单播放',v.board_play_count],['指数',v.index],['点赞',v.digg_count],['作者粉丝',v.fans]].filter(([,n])=>n!=null).map(([k,n])=>`<span>${k}<b>${fmt(n)}</b></span>`).join('')}</div>`;return `<div class="product-metrics">${[['播放',v.play_count],['点赞',v.digg_count],['评论',v.comment_count],['收藏',v.collect_count],['分享',v.share_count]].map(([k,n])=>`<div><small>${k}</small><b>${fmt(n)}</b></div>`).join('')}</div>`;}
  let actionVersion=0;
  async function openAction(values){
    const d=dialog('inlineActionDialog','确认采集','action-dialog'),body=d.querySelector('.product-dialog-body'),version=++actionVersion;
    d.querySelector('h2').textContent=actionNames[values.action]||'采集';body.innerHTML=busy('正在预览本次请求');openDialog(d);
    const request={pages:1,playback_method:'single',missing_only:false,...values};
    if(request.video_ids&&request.action==='download')request.input=request.video_ids.join('\n');
    let plan=null,jobId=null,timer=null,submitting=false;
    async function quote(){
      plan=null;const btn=body.querySelector('[data-confirm-action]');if(btn)btn.disabled=true;
      try{const signature=JSON.stringify(request),p=await api('/api/workbench/plan',request);if(version!==actionVersion||signature!==JSON.stringify(request))return;plan=p;
        body.querySelector('[data-action-quote]').innerHTML=`<b>${p.units} ${request.action==='account'?'个账号':'条作品'} · 最多 ${p.calls} 次请求</b><span>${p.paid?'使用 TikHub 额度'+(request.action==='playback'?' · 单作品接口':''):'本机解析并保存到素材库'}</span>${p.reason?`<span class="error-text">${esc(p.reason)}</span>`:''}`;
        body.querySelector('[data-confirm-action]').disabled=!p.allowed;
      }catch(e){if(version===actionVersion){body.querySelector('[data-action-error]').textContent=e.message;}}
    }
    try{
      const snapshot=await api('/api/workbench/state');if(version!==actionVersion)return;
      const ids=new Set(request.video_ids||[]),works=snapshot.videos.filter(v=>ids.has(v.aweme_id));
      const account=snapshot.accounts.find(a=>a.sec_uid===request.account);
      body.innerHTML=`${works.length?`<div class="action-work-list">${works.map(v=>`<div>${picture(v.cover_path)}<span>${esc(v.title)}<small>${esc(v.account_name)}</small></span></div>`).join('')}</div>`:account?`<p class="action-account">${esc(account.nickname||account.note)}</p>`:''}${['comments','account'].includes(request.action)?'<label class="action-pages">最多采集页数<select data-action-pages><option value="1">1 页 · 每页 20 条</option><option value="2">2 页</option><option value="3">3 页</option><option value="5">5 页</option></select></label>':''}<div class="action-quote" data-action-quote>正在预览</div><div class="action-progress" data-action-progress role="status"></div><p class="error-text" data-action-error role="alert"></p><div class="product-dialog-actions"><button class="btn" data-dismiss-action>取消</button><button class="btn primary" data-confirm-action disabled>${request.action==='download'?'确认下载':request.action==='playback'?'确认更新':'确认采集'}</button></div>`;
      body.querySelector('[data-dismiss-action]').onclick=()=>d.close();
      const pages=body.querySelector('[data-action-pages]');if(pages)pages.onchange=()=>{request.pages=Number(pages.value);quote();};
      body.querySelector('[data-confirm-action]').onclick=async()=>{
        if(submitting||!plan?.allowed)return;submitting=true;body.querySelector('[data-confirm-action]').disabled=true;if(pages)pages.disabled=true;
        body.querySelector('[data-action-error]').textContent='';
        try{const j=await api('/api/workbench/submit',{...request,fingerprint:plan.fingerprint,confirmed:true});jobId=j.id;body.querySelector('[data-confirm-action]').hidden=true;body.querySelector('[data-dismiss-action]').textContent='关闭';poll();}
        catch(e){body.querySelector('[data-action-error]').textContent=e.message;submitting=false;if(pages)pages.disabled=false;await quote();}
      };
      async function poll(){
        try{const j=await api('/api/douk/jobs/'+jobId);
          if(version===actionVersion)body.querySelector('[data-action-progress]').textContent=(statusNames[j.status]||j.status)+(j.message?' · '+j.message:'');
          if(['queued','running'].includes(j.status)){timer=setTimeout(poll,1500);return;}
          if(['completed','partial'].includes(j.status))window.dispatchEvent(new CustomEvent('gc-data-updated',{detail:{...request,jobId}}));
          if(version!==actionVersion)return;
          if(j.result?.errors?.length)body.querySelector('[data-action-error]').textContent=j.result.errors.map(e=>e.error).join('；');
          if(j.status==='completed')body.querySelector('[data-action-progress]').classList.add('done');
        }catch(e){if(version===actionVersion)body.querySelector('[data-action-error]').textContent=e.message;}
      }
      await quote();
    }catch(e){if(version===actionVersion)body.innerHTML=busy(e.message);}
  }
  let accountVersion=0,activeAccount=null,accountData=null,accountFilter={q:'',topic:'',period:'',sort:'new'},accountPage=0;
  let pageCount=50;
  function accountWorks(){return accountData.videos.filter(v=>v.sec_uid===activeAccount&&(!accountFilter.q||(v.title+' '+v.aweme_id).toLowerCase().includes(accountFilter.q))&&(!accountFilter.topic||v.topic===accountFilter.topic)&&(!accountFilter.period||v.create_time>=Date.now()/1000-Number(accountFilter.period)*86400)).sort((a,b)=>accountFilter.sort==='play'?(b.play_count??-1)-(a.play_count??-1):(b.create_time||0)-(a.create_time||0));}
  function accountRows(){
    const d=document.getElementById('accountDetailDialog'),all=accountWorks();accountPage=Math.min(accountPage,Math.max(0,Math.ceil(all.length/pageCount)-1));
    d.querySelector('[data-account-rows]').innerHTML=all.slice(accountPage*pageCount,(accountPage+1)*pageCount).map(v=>`<tr><td><button class="modal-work" data-open-work="${v.aweme_id}">${picture(v.cover_path)}<span><strong>${esc(v.title||v.aweme_id)}</strong><small>${date(v.create_time)} · ${esc(v.topic||'未识别')}</small></span></button></td><td>${fmt(v.play_count)}<small class="cell-note">${esc(v.play_date||'未取得')}</small></td><td>${fmt(v.digg_count)}</td><td>${fmt(v.comment_count)}</td><td>${fmt(v.collect_count)}</td><td>${fmt(v.share_count)}</td><td><div class="row-work-actions"><button class="btn row-update" data-inline-action="playback" data-aid="${v.aweme_id}">更新数据</button><div><button class="text-link" data-open-work="${v.aweme_id}">详情</button><button class="text-link" data-inline-action="download" data-aid="${v.aweme_id}">下载</button></div></div></td></tr>`).join('')||'<tr><td colspan="7">没有符合条件的作品</td></tr>';
    pagination(d.querySelector('[data-account-pagination]'),{total:all.length,page:accountPage,pageSize:pageCount,unit:'条作品',onChange:(page,size)=>{accountPage=page;pageCount=size;accountRows();d.querySelector('.account-works-scroll').scrollTop=0;}});
  }
  async function openAccount(id,keep=false){
    const d=dialog('accountDetailDialog','账号详情','account-detail-dialog'),body=d.querySelector('.product-dialog-body'),version=++accountVersion;
    const previousForm=keep?d.querySelector('[data-account-form]'):null;
    const draft=previousForm?Object.fromEntries(new FormData(previousForm)):null;
    const previousScroll=keep?(d.querySelector('.account-works-scroll')?.scrollTop||0):0;
    if(!keep){accountFilter={q:'',topic:'',period:'',sort:'new'};accountPage=0;}activeAccount=id;body.innerHTML=busy('正在读取账号明细');openDialog(d);
    try{const s=await api('/api/workbench/state');if(version!==accountVersion)return;accountData=s;const a=s.accounts.find(x=>x.sec_uid===id);if(!a)throw Error('账号已移除');
      const works=s.videos.filter(v=>v.sec_uid===id),topics=[...new Set(works.map(v=>v.topic).filter(Boolean))],latest=[...works].sort((x,y)=>(y.create_time||0)-(x.create_time||0))[0];
      d.querySelector('.product-dialog-head').innerHTML=`<div class="compact-account-heading">${picture(a.avatar_path,'account-detail-avatar')}<div><h2 id="accountDetailDialogTitle">${esc(a.nickname||a.note||'未命名账号')}<span class="role-pill">${roles[a.role]||'未分组'}</span><span class="monitor-pill ${a.enabled?'on':''}">${a.enabled?'监控中':'已暂停'}</span></h2><p>最近发布 ${date(latest?.create_time)}<span>同步 ${esc(a.last_scan_at||'尚未同步')}</span></p></div></div><div class="compact-account-actions"><a class="btn" target="_blank" rel="noreferrer" href="https://www.douyin.com/user/${encodeURIComponent(id)}">抖音主页 ↗</a><button class="btn primary" data-inline-action="account" data-inline-account="${esc(id)}">同步作品</button><button class="btn" data-edit-account="${esc(id)}">管理</button><button class="btn" data-dismiss>关闭</button></div>`;
      body.innerHTML=`        <div class="filter-layout modal-filter-layout"><form class="filter-panel filter-bar" id="accountWorkFilters" data-account-form><label><span class="filter-label">作品 / ID</span><input name="q" placeholder="搜索标题或作品 ID" value="${esc(accountFilter.q)}"></label><label><span class="filter-label">题材</span><select name="topic"><option value="">全部题材</option>${topics.map(t=>`<option ${accountFilter.topic===t?'selected':''}>${esc(t)}</option>`).join('')}</select></label><label><span class="filter-label">发布时间</span><select name="period"><option value="">全部时间</option>${[7,30,90].map(n=>`<option value="${n}" ${accountFilter.period===String(n)?'selected':''}>最近 ${n} 天</option>`).join('')}</select></label><label><span class="filter-label">排序</span><select name="sort"><option value="new">最新发布</option><option value="play" ${accountFilter.sort==='play'?'selected':''}>播放量最高</option></select></label><div class="filter-buttons"><button class="btn primary" type="submit">搜索</button><button class="btn" type="reset">重置</button></div></form><div class="filter-results"><div class="table-scroll account-works-scroll"><table><thead><tr><th>作品 / 发布时间</th><th>播放</th><th>点赞</th><th>评论</th><th>收藏</th><th>分享</th><th>操作</th></tr></thead><tbody data-account-rows></tbody></table></div><div data-account-pagination></div></div></div>`;
      const portrait=document.createElement('button');portrait.className='btn';portrait.textContent='粉丝画像';portrait.onclick=()=>GuanchaoAudience.open({source:'portrait',sec_uid:id},(a.nickname||a.note||'账号')+' · 粉丝画像');d.querySelector('.compact-account-actions').prepend(portrait);
      initFilters(d);const form=d.querySelector('[data-account-form]');if(draft)Object.entries(draft).forEach(([key,value])=>{form.elements[key].value=value;});form.onsubmit=e=>{e.preventDefault();accountFilter=Object.fromEntries(new FormData(form));accountFilter.q=accountFilter.q.trim().toLowerCase();accountPage=0;accountRows();};form.onreset=e=>{e.preventDefault();form.elements.q.value='';form.elements.topic.value='';form.elements.period.value='';form.elements.sort.value='new';};
      accountRows();d.querySelector('.account-works-scroll').scrollTop=previousScroll;
    }catch(e){if(version===accountVersion)body.innerHTML=busy(e.message);}
  }
  let workVersion=0,activeWork=null,workTab='caption',workDetail=null;
  function workPane(){
    const d=document.getElementById('workDetailDialog'),v=workDetail.video;
    d.querySelectorAll('[data-work-tab]').forEach(b=>b.classList.toggle('active',b.dataset.workTab===workTab));
    let html='';
    if(workTab==='caption')html=`<div class="copy-toolbar"><b>发布文案</b><button class="text-link" data-copy-work>复制文案</button></div><p class="caption-text">${esc(workDetail.description||v.title)}</p><div class="work-metadata"><span>作品 ID ${v.aweme_id}</span><span>题材 ${esc(v.topic||'未识别')}</span><span>播放来源 ${esc(sources[v.play_source]||v.play_source||'未取得')}</span><span>采集日期 ${esc(v.play_date||'—')}</span></div>`;
    if(workTab==='comments')html=`<div class="copy-toolbar"><b>${workDetail.comments.length} 条已采集评论</b><button class="btn primary" data-inline-action="comments" data-aid="${v.aweme_id}">采集评论</button></div>${workDetail.comments.map(c=>`<article class="comment-row"><div class="comment-meta"><b>${esc(c.author_name||'用户')}</b><span>${esc(c.published_at||'')}</span><span>${fmt(c.like_count)} 赞</span></div><p>${esc(c.content)}</p></article>`).join('')||busy('尚未采集评论')}`;
    if(workTab==='media')html=`<div class="copy-toolbar"><b>已保存素材</b><button class="btn primary" data-inline-action="download" data-aid="${v.aweme_id}">下载视频到素材库</button></div>${workDetail.files.map(f=>`<div class="file-link"><span>${esc(f.name.split('/').pop())}</span><a class="text-link" target="_blank" rel="noreferrer" href="${esc(f.url)}">预览</a><a class="btn" href="${esc(download(f.url))}" download>下载文件</a></div>`).join('')||busy('尚无已保存的素材')}`;
    if(workTab==='history')html=`<div class="table-scroll"><table><thead><tr><th>采集时间</th><th>播放</th><th>点赞</th><th>评论</th><th>来源</th></tr></thead><tbody>${workDetail.history.map(r=>`<tr><td>${esc(r.fetched_at||r.fetch_date)}</td><td>${fmt(r.play_count)}</td><td>${fmt(r.digg_count)}</td><td>${fmt(r.comment_count)}</td><td>${esc(sources[r.metric_status||r.source]||r.metric_status||r.source||'主页作品')}</td></tr>`).join('')||'<tr><td colspan="5">暂无快照</td></tr>'}</tbody></table></div>`;
    if(workTab==='comparison')html='<div data-comparison-host></div>';
    d.querySelector('[data-work-pane]').innerHTML=html;
    if(workTab==='comparison')GuanchaoAudience.comparison(d.querySelector('[data-comparison-host]'),workDetail.comparison);
    const copy=d.querySelector('[data-copy-work]');if(copy)copy.onclick=async()=>{try{await navigator.clipboard.writeText(workDetail.description);copy.textContent='已复制';}catch{copy.textContent='复制失败';}};
  }
  async function openWork(id,keep=false){
    const d=dialog('workDetailDialog','作品详情','work-detail-dialog'),body=d.querySelector('.product-dialog-body'),version=++workVersion;activeWork=id;if(!keep)workTab='caption';body.innerHTML=busy('正在读取作品明细');openDialog(d);
    try{const detail=await api('/api/workbench/research?video='+id);if(version!==workVersion)return;workDetail=detail;const v=detail.video;
      body.innerHTML=`<div class="modal-work-header">${picture(v.cover_path,'modal-work-cover')}<div><h3>${esc(v.title||v.aweme_id)}</h3><p>${esc(v.account_name)} · ${date(v.create_time)} 发布</p><div class="work-actions"><a class="btn" href="https://www.douyin.com/video/${v.aweme_id}" target="_blank" rel="noreferrer">原视频 ↗</a><button class="btn" data-inline-action="download" data-aid="${v.aweme_id}">下载视频</button><button class="btn" data-inline-action="comments" data-aid="${v.aweme_id}">采集评论</button><button class="btn primary" data-inline-action="playback" data-aid="${v.aweme_id}">更新数据</button></div></div></div>${metrics(v)}<nav class="research-tabs" aria-label="作品详情">${[['caption','发布文案'],['comments','评论 '+detail.comments.length],['media','视频素材'],['history','数据记录'],['comparison','样本对比']].map(([k,t])=>`<button data-work-tab="${k}">${t}</button>`).join('')}</nav><div class="modal-work-pane" data-work-pane></div>`;
      if(detail.monitored===false){d.querySelector('[data-inline-action=playback]')?.remove();const b=document.createElement('button');b.className='btn primary';b.textContent='加入监控';d.querySelector('.work-actions').append(b);const quote=document.createElement('div');quote.hidden=true;d.querySelector('.modal-work-header').after(quote);b.onclick=()=>GuanchaoAudience.runInline(quote,{source:'monitor',aweme_id:id},{onComplete:()=>openWork(id,true)});}
      d.querySelectorAll('[data-work-tab]').forEach(b=>b.onclick=()=>{workTab=b.dataset.workTab;workPane();});workPane();
    }catch(e){if(version===workVersion)body.innerHTML=busy(e.message);}
  }
  let editorVersion=0;
  async function openAccountEditor(id=null){
    const d=dialog('accountEditorDialog',id?'管理账号':'添加账号','account-editor-dialog'),body=d.querySelector('.product-dialog-body'),version=++editorVersion;
    d.querySelector('h2').textContent=id?'管理账号':'添加账号';
    const close=d.querySelector('.product-dialog-head [data-dismiss]');close.className='editor-close';close.type='button';close.setAttribute('aria-label','关闭弹窗');close.innerHTML='<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15"></path></svg>';
    body.innerHTML=busy('正在读取账号');openDialog(d);
    try{const a=id?(await api('/api/workbench/state')).accounts.find(x=>x.sec_uid===id):null;if(version!==editorVersion)return;if(id&&!a)throw Error('账号已移除');
      body.innerHTML=`<form id="accountEditorForm" class="account-editor-form"><div class="editor-fields">
        ${a?`<div class="editor-account-summary">${picture(a.avatar_path,'editor-avatar')}<div><strong>${esc(a.nickname||a.note||'未命名账号')}</strong><span>${roles[a.role]||'未分组'}</span></div><a href="${esc(a.profile_url)}" target="_blank" rel="noreferrer">打开主页<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M6 3H3v10h10v-3M8 3h5v5M13 3l-7 7"></path></svg></a></div>`:''}
        <label class="editor-field" for="editorAccountUrl">抖音主页<input id="editorAccountUrl" name="url" type="url" required placeholder="粘贴抖音账号主页链接" value="${esc(a?.profile_url||'')}" ${id?'readonly':'autofocus'} autocomplete="off" spellcheck="false"></label>
        <div class="editor-field-row"><label class="editor-field" for="editorAccountNote">账号备注<input id="editorAccountNote" name="note" maxlength="60" value="${esc(a?.note||'')}" placeholder="填写备注（选填）" autocomplete="off"></label><label class="editor-field" for="editorAccountRole">账号分组<select id="editorAccountRole" name="role">${Object.entries(roles).map(([key,label])=>`<option value="${key}" ${(a?.role||'peer')===key?'selected':''}>${label}</option>`).join('')}</select></label></div>
        ${id?`<label class="editor-monitor" for="editorAccountEnabled"><span>账号监控</span><span class="editor-monitor-state">${a.enabled?'已启用':'已暂停'}</span><input id="editorAccountEnabled" name="enabled" type="checkbox" role="switch" ${a.enabled?'checked':''}></label>`:''}
        <p class="error-text" role="alert" data-editor-error></p></div><div class="product-dialog-actions"><button class="btn" type="button" data-dismiss>取消</button><button class="btn primary" type="submit">${id?'保存修改':'添加账号'}</button></div></form>`;
      const enabled=body.querySelector('[name=enabled]');if(enabled)enabled.onchange=()=>{body.querySelector('.editor-monitor-state').textContent=enabled.checked?'已启用':'已暂停';};
      const form=body.querySelector('form');if(d.open)form.elements[id?'note':'url'].focus({preventScroll:true});let saving=false;form.onsubmit=async e=>{e.preventDefault();if(saving)return;saving=true;const button=form.querySelector('[type=submit]');button.disabled=true;button.textContent=id?'保存中…':'添加中…';body.querySelector('[data-editor-error]').textContent='';
        try{const values=Object.fromEntries(new FormData(form));await api(id?'/api/intel/account-settings':'/api/intel/account',id?{sec_uid:id,note:values.note,role:values.role,enabled:form.elements.enabled.checked}:{url:values.url,note:values.note,role:values.role});d.close();window.dispatchEvent(new CustomEvent('gc-data-updated',{detail:{type:'accounts',account:id}}));}
        catch(error){body.querySelector('[data-editor-error]').textContent=error.message;}finally{saving=false;button.disabled=false;button.textContent=id?'保存修改':'添加账号';}
      };
    }catch(error){if(version===editorVersion)body.innerHTML=busy(error.message);}
  }
  document.addEventListener('click',e=>{
    const add=e.target.closest('[data-add-account]'),edit=e.target.closest('[data-edit-account]');
    if(add||edit){e.preventDefault();openAccountEditor(edit?.dataset.editAccount||null);return;}
    const action=e.target.closest('[data-inline-action]'),account=e.target.closest('[data-open-account]'),work=e.target.closest('[data-open-work]');
    if(action){e.preventDefault();openAction({action:action.dataset.inlineAction,...(action.dataset.aid?{video_ids:[action.dataset.aid]}:{}),...(action.dataset.inlineAccount?{account:action.dataset.inlineAccount}:{})});}
    else if(account){e.preventDefault();openAccount(account.dataset.openAccount);}
    else if(work){e.preventDefault();openWork(work.dataset.openWork);}
  });
  window.addEventListener('gc-data-updated',()=>{
    document.querySelectorAll('dialog[data-suspended]').forEach(d=>d.dataset.needsRefresh='true');
    if(document.getElementById('workDetailDialog')?.open&&activeWork)openWork(activeWork,true);
    if(document.getElementById('accountDetailDialog')?.open&&activeAccount)openAccount(activeAccount,true);
  });
  initFilters();
  window.Guanchao={esc,fmt,date,picture,api,download,roles,pagination,openAction,openAccount,openWork,openAccountEditor,openDialog};
})();
