(() => {
  'use strict';
  const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = n => n == null ? '—' : n >= 1e8 ? (n/1e8).toFixed(2)+'亿' : n >= 1e4 ? (n/1e4).toFixed(1)+'万' : Number(n).toLocaleString('zh-CN');
  const date = n => n ? new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(n*1000)) : '—';
  const size = n => n >= 1024**3 ? (n/1024**3).toFixed(1)+' GB' : n >= 1024**2 ? (n/1024**2).toFixed(1)+' MB' : Math.ceil(n/1024)+' KB';
  const sources = {tikhub_app:'TikHub 单视频统计',tikhub_app_batch:'TikHub 批量统计',tikhub_app_v3:'TikHub 作品详情',tikhub_xingtu:'星图统计',douk_detail:'视频详情',mcpro_python:'平台采集',author_post:'主页作品',public_mix_metric:'合集统计'};
  const labels = {queued:'排队中',running:'进行中',completed:'完成',partial:'部分完成',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
  const view = location.pathname.slice(1), args = new URLSearchParams(location.search);
  const state = {data:null, video:args.get('video'), detail:null, tab:'caption', limit:40, requestVersion:0, plan:null, jobs:[], selected:null, assets:[], submitting:false,
    researchFilter:{q:'',account:''},assetFilter:{q:'',type:'all',source:''},taskFilter:{q:'',status:''},homeFilter:{q:'',role:'',status:'',sort:'new'}};
  let settingsTab='monitoring',connectionDirty=false,connectionSaving=false,servicesLoaded=false,servicesLoading=false;
  const paging={home:{page:0,size:12},library:{page:0,size:50},jobs:{page:0,size:20},assets:{page:0,size:50},records:{page:0,size:50}};
  function pageItems(id,items,key,render,unit,extra={}){
    const p=paging[key];p.page=Math.max(0,Math.min(p.page,Math.ceil(items.length/p.size)-1));
    Guanchao.pagination($('#'+id),{total:items.length,page:p.page,pageSize:p.size,unit,...extra,onChange:(page,size)=>{p.page=page;p.size=size;render();}});
    return items.slice(p.page*p.size,(p.page+1)*p.size);
  }
  async function api(path, payload) {
    const r = await fetch(path, payload === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data = await r.json();
    if (!r.ok || data.error) throw Error(data.error || '读取失败');
    return data;
  }
  function toast(message) { $('#toast').textContent=message; $('#toast').classList.add('show'); clearTimeout(toast.timer); toast.timer=setTimeout(()=>$('#toast').classList.remove('show'),2600); }
  function options(accounts, all='全部账号') { return `<option value="">${all}</option>`+accounts.map(a=>`<option value="${esc(a.sec_uid)}">${esc(a.nickname || a.note || '未命名账号')}</option>`).join(''); }
  function picture(path, cls='') { return path ? `<img class="${cls}" src="${esc(path)}" alt="" loading="lazy">` : `<span class="library-placeholder ${cls}">影</span>`; }
  function renderHome(){
    const f=state.homeFilter,all=state.data.videos;
    const works=id=>all.filter(v=>v.sec_uid===id).sort((a,b)=>(b.create_time||0)-(a.create_time||0));
    const accounts=state.data.accounts.filter(a=>(!f.q||((a.nickname||'')+' '+(a.note||'')).toLowerCase().includes(f.q))&&(!f.role||a.role===f.role)&&(!f.status||(f.status==='enabled'?!!a.enabled:!a.enabled)));
    accounts.sort((a,b)=>f.sort==='name'?(a.nickname||a.note||'').localeCompare(b.nickname||b.note||'','zh-CN'):f.sort==='play'?(b.play_sum??-1)-(a.play_sum??-1):(works(b.sec_uid)[0]?.create_time||0)-(works(a.sec_uid)[0]?.create_time||0));
    $('#homeCards').innerHTML=pageItems('homePagination',accounts,'home',renderHome,'个账号',{sizes:[12,24,48]}).map(a=>{
      const latest=works(a.sec_uid).slice(0,3),name=a.nickname||a.note||'未命名账号';
      return `<article class="home-account-card" tabindex="0" role="button" aria-label="查看${esc(name)}全部明细" data-card-account="${esc(a.sec_uid)}">
        <header class="account-card-header">${Guanchao.picture(a.avatar_path,'account-card-avatar')}<div><h2><span class="account-card-name" title="${esc(name)}">${esc(name)}</span><span class="role-pill">${Guanchao.roles[a.role]||'未分组'}</span></h2><p><span class="account-card-status ${a.enabled?'is-enabled':'is-paused'}">${a.enabled?'监控中':'已暂停'}</span>${a.follower_count==null?'':`<span>${fmt(a.follower_count)} 粉丝</span>`}</p></div><button type="button" class="card-manage" data-edit-account="${esc(a.sec_uid)}" aria-label="管理${esc(name)}">管理</button></header>
        <div class="account-latest">${latest.map(v=>`<button type="button" class="latest-work" data-open-work="${v.aweme_id}" aria-label="查看作品：${esc(v.title)}"><div>${Guanchao.picture(v.cover_path)}<span class="latest-plays"><svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 1.5 10 6l-7 4.5z"></path></svg>${fmt(v.play_count)}</span></div><strong title="${esc(v.title||v.aweme_id)}">${esc(v.title||v.aweme_id)}</strong><time datetime="${esc(date(v.create_time))}">${date(v.create_time).slice(5)}</time></button>`).join('')}${Array.from({length:3-latest.length},()=>'<span class="latest-placeholder">暂无作品</span>').join('')}</div>
        <footer class="account-card-footer"><button type="button" data-open-account="${esc(a.sec_uid)}">查看全部作品<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m6 4 4 4-4 4"></path></svg></button></footer></article>`;
    }).join('')||'<div class="home-empty wb-empty">没有符合条件的监控账号</div>';
  }
  function renderLibrary() {
    const {q,account}=state.researchFilter;
    const rows=state.data.videos.filter(v=>(!account||v.sec_uid===account)&&(!q||(v.title+v.account_name).toLowerCase().includes(q))).sort((a,b)=>(b.create_time||0)-(a.create_time||0));
    $('#workList').innerHTML=pageItems('libraryPagination',rows,'library',renderLibrary,'条作品',{compact:true}).map(v=>`<button class="library-work ${v.aweme_id===state.video?'active':''}" data-video="${v.aweme_id}">${picture(v.cover_path)}<span><strong>${esc(v.title||v.aweme_id)}</strong><small><span>${esc(v.account_name)}</span><span>${fmt(v.play_count)} 播放</span></small></span></button>`).join('')||'<p class="wb-empty">没有匹配的作品</p>';
  }
  async function selectVideo(id) {
    state.video=id; const version=++state.requestVersion; renderLibrary();
    $('#researchContent').setAttribute('aria-busy','true');
    try {
      const detail=await api('/api/workbench/research?video='+encodeURIComponent(id));
      if(version!==state.requestVersion)return;
      state.detail=detail; history.replaceState(null,'','/research?video='+encodeURIComponent(id)); renderResearch();
    } catch(e) { if(version===state.requestVersion)$('#researchContent').innerHTML=`<div class="panel wb-empty">${esc(e.message)}</div>`; }
    finally { if(version===state.requestVersion)$('#researchContent').removeAttribute('aria-busy'); }
  }
  function renderResearch() {
    const d=state.detail,v=d.video;
    $('#researchContent').innerHTML=`<article class="panel work-header"><div class="work-heading">${picture(v.cover_path,'work-cover')}<div><h1>${esc(v.title||v.aweme_id)}</h1><div class="work-identity">${v.avatar_path?picture(v.avatar_path):''}<button class="text-link" data-open-account="${esc(v.sec_uid)}">${esc(v.account_name)}</button><span>${date(v.create_time)} 发布</span><span>${esc(v.topic||'未分类')}</span></div><div class="work-actions"><a class="btn" href="https://www.douyin.com/video/${v.aweme_id}" target="_blank" rel="noreferrer">打开原视频</a><button class="btn" data-inline-action="download" data-aid="${v.aweme_id}">下载视频</button><button class="btn" data-inline-action="comments" data-aid="${v.aweme_id}">采集评论</button><button class="btn" data-inline-action="playback" data-aid="${v.aweme_id}">更新数据</button></div></div></div><div class="work-metrics">${[['播放量',v.play_count],['点赞',v.digg_count],['评论',v.comment_count],['收藏',v.collect_count],['分享',v.share_count]].map(([k,n])=>`<div><small>${k}</small><b>${fmt(n)}</b></div>`).join('')}</div></article>
    <div class="panel"><nav class="research-tabs" aria-label="作品研究">${[['caption','作品文案'],['media','视频素材'],['comments','评论 '+d.comments.length],['history','数据记录'],['comparison','样本对比']].map(([key,label])=>`<button class="${state.tab===key?'active':''}" data-tab="${key}">${label}</button>`).join('')}</nav><div class="research-pane" id="researchPane"></div></div>
    <div class="research-columns"><div class="panel"><div class="panel-head"><h2>同账号热门作品</h2></div><div class="compact-list">${d.related.map(r=>`<button class="related-work" data-video="${r.aweme_id}"><span>${esc(r.title)}</span><b>${fmt(r.play_count)}</b></button>`).join('')||'<p class="wb-empty">暂无其他监控作品</p>'}</div></div><div class="panel"><div class="panel-head"><h2>观测信息</h2></div><div class="metadata-lines"><div><span>播放数据来源</span><b>${esc(sources[v.play_source]||v.play_source||'—')}</b></div><div><span>最近播放采集</span><b>${esc(v.play_date||'—')}</b></div><div><span>发布年龄</span><b>${esc(v.age_band||'—')}</b></div><div><span>增长样本间隔</span><b>${v.span_days??'—'} 天</b></div><div><span>作品 ID</span><b>${v.aweme_id}</b></div></div></div></div>`;
    renderPane();
  }
  function renderPane() {
    const d=state.detail,v=d.video;
    $$('.research-tabs button').forEach(b=>b.classList.toggle('active',b.dataset.tab===state.tab));
    let html='';
    if(state.tab==='caption') {
      const tags=[...new Set((d.description.match(/#[^\s#]+/g)||[]))];
      html=`<div class="copy-toolbar"><span>发布文案</span><button class="text-link" id="copyCaption">复制文案</button></div><div class="caption-text">${esc(d.description)}</div>${tags.length?`<div class="feature-chips">${tags.slice(0,12).map(t=>`<span>${esc(t)}</span>`).join('')}</div>`:''}`;
    } else if(state.tab==='comments') {
      html=`<div class="copy-toolbar"><span>${d.comments.length} 条已采集评论</span><button class="btn primary" data-inline-action="comments" data-aid="${v.aweme_id}">采集评论</button></div>`+(d.comments.length?d.comments.map(c=>`<div class="comment-row"><div class="comment-meta"><b>${esc(c.author_name||'用户')}</b><span>${esc(c.published_at||'')}</span><span>${fmt(c.like_count)} 赞</span></div><p>${esc(c.content)}</p></div>`).join(''):'<div class="wb-empty">尚未采集这条作品的评论</div>');
    } else if(state.tab==='media') {
      const video=d.files.find(f=>/\.(mp4|webm|mov|mkv)$/i.test(f.name));
      html=`<div class="copy-toolbar"><span>已保存素材</span><button class="btn primary" data-inline-action="download" data-aid="${v.aweme_id}">下载视频到素材库</button></div>${video?`<div class="video-preview"><video controls preload="metadata" poster="${esc(v.cover_path||'')}"><source src="${esc(video.url)}"></video><div class="file-links">`:'<div class="file-links">'}${d.files.map(f=>`<div class="file-link"><b>${esc(f.name.split('/').pop())}</b><small>${size(f.size)}</small><a class="btn" href="${esc(Guanchao.download(f.url))}" download>下载文件</a></div>`).join('')||'<div class="wb-empty">该作品尚未下载到素材库</div>'}</div>${video?'</div>':''}`;
    } else {
      html=`<div class="table-scroll"><table><thead><tr><th>采集时间</th><th>播放</th><th>点赞</th><th>来源</th></tr></thead><tbody>${d.history.map(r=>`<tr><td>${esc(r.fetched_at||r.fetch_date)}</td><td>${fmt(r.play_count)}</td><td>${fmt(r.digg_count)}</td><td>${esc(sources[r.metric_status]||r.metric_status||'主页作品')}</td></tr>`).join('')}</tbody></table></div><p class="history-note">不同来源分别保留。增长只比较相同来源的有效快照；单次播放数据不用于判断推流原因。</p>`;
    }
    $('#researchPane').innerHTML=html;
    if(state.tab==='comparison')GuanchaoAudience.comparison($('#researchPane'),d.comparison);
  }
  function collectValues() { return {action:$('#collectAction').value,account:$('#collectAccount').value,input:$('#collectInput').value,pages:Number($('#collectPages').value),missing_only:$('#missingOnly').checked,playback_method:$('#playbackMethod').value}; }
  function invalidate() { state.plan=null; $('#startCollect').disabled=true; $('#confirmCollect').checked=false; $('#collectPreview').textContent='先预览本次请求'; $('#collectError').textContent=''; }
  function toggleAction() {
    const action=$('#collectAction').value;
    $('#accountField').hidden=!['account','playback'].includes(action);
    $('#inputField').hidden=action==='account';
    $('#pagesField').hidden=!['account','comments'].includes(action);
    $('#missingField').hidden=action!=='playback';
    $('#playbackMethodField').hidden=action!=='playback';
    $('#confirmCollectLabel').textContent=action==='download'?'确认下载所选作品':'确认本次请求及接口所需额度';
    $('#collectionHint').textContent=({account:'每页 20 条，优先同步最新作品。',playback:'默认每条作品使用单作品统计接口。留空则按所选账号处理。',video:'采集发布文案、封面、发布时间与互动数据。',comments:'每页最多 20 条。仅在返回新游标时继续翻页。',download:'使用本机解析服务，视频保存到素材库。'})[action];
    invalidate();
  }
  function renderJobs() {
    const f=state.taskFilter,rows=state.jobs.filter(j=>(!f.q||(j.title||'').toLowerCase().includes(f.q))&&(!f.status||j.status===f.status));
    $('#collectionJobs').innerHTML=pageItems('jobsPagination',rows,'jobs',renderJobs,'个任务').map(j=>`<button class="collection-job ${state.selected===j.id?'active':''}" data-job="${j.id}"><span><strong>${esc(j.title)}</strong><small>${esc(j.created_at)}${j.count?' · '+j.count+' 条':''}</small></span><b class="task-status ${esc(j.status)}">${labels[j.status]||esc(j.status)}</b></button>`).join('')||'<p class="wb-empty">没有符合条件的采集任务</p>';
  }
  async function openJob(id) {
    if(state.selected!==id)paging.records.page=0;
    state.selected=id;renderJobs();const job=await api('/api/douk/jobs/'+id);if(state.selected!==id)return;
    state.job=job;renderJobOutput();
  }
  function renderJobOutput(){
    const job=state.job;if(!job)return;const r=job.result||{},rows=job.records||[];
    $('#jobTitle').textContent=job.title;
    $('#jobActions').innerHTML=`${['queued','running'].includes(job.status)?`<button class="btn" data-cancel-job="${job.id}">取消任务</button>`:''}<a class="text-link" href="/api/douk/export?id=${job.id}&format=xlsx">Excel</a><a class="text-link" href="/api/douk/export?id=${job.id}&format=json">JSON</a>`;
    if(!$('#jobRecordRows'))$('#jobOutput').innerHTML='<p id="jobRecordMessage"></p><div id="jobRefreshProgress" hidden></div><div class="table-scroll" id="jobRecordTable"><table><thead id="jobRecordHead"></thead><tbody id="jobRecordRows"></tbody></table></div><div id="jobRecordsPagination"></div><p class="error-text" id="jobRecordErrors"></p><div class="wb-empty" id="jobRecordEmpty"></div>';
    $('#jobRecordMessage').textContent=(labels[job.status]||job.status)+(job.message?' · '+job.message:'')+(r.attempted?' · '+r.attempted+' 次请求':'');
    const identities=new Map((state.data?.videos||[]).map(v=>[v.aweme_id,v])),showComments=rows.some(x=>x.comment_count!=null||x.statistics?.comment_count!=null);
    G.jobProgress($('#jobRefreshProgress'),job,{showItems:false});
    $('#jobRecordHead').innerHTML=`<tr><th>作品 / 内容</th><th>播放</th><th>点赞</th>${showComments?'<th>评论</th>':''}</tr>`;
    const visible=pageItems('jobRecordsPagination',rows,'records',renderJobOutput,'条记录');
    $('#jobRecordRows').innerHTML=visible.map(x=>{const aid=String(x.aweme_id||x.content_id||''),known=identities.get(aid),label=x.text||x.content||x.title||x.desc||known?.title||aid||x.name||'记录';return `<tr><td>${known?`<button type="button" class="text-link task-work-title" data-open-work="${aid}">${esc(label)}</button><small class="task-work-account">${esc(known.account_name)}</small>`:esc(label)}</td><td>${fmt(x.play_count??x.statistics?.play_count)}</td><td>${fmt(x.digg_count??x.statistics?.digg_count)}</td>${showComments?`<td>${fmt(x.comment_count??x.statistics?.comment_count)}</td>`:''}</tr>`;}).join('');
    $('#jobRecordTable').hidden=!rows.length;
    $('#jobRecordErrors').textContent=(r.errors||[]).map(e=>e.error).join('；');
    $('#jobRecordEmpty').hidden=!!rows.length||!!r.errors?.length;
    $('#jobRecordEmpty').textContent=['running','queued'].includes(job.status)?'任务正在执行，结果将自动更新':'没有可展示的记录，可导出完整响应查看。';
  }
  function fileType(name) { return /\.(mp4|mov|mkv|webm)$/i.test(name)?'video':/\.(mp3|wav|m4a)$/i.test(name)?'audio':/\.(jpg|png|jpeg|webp|gif)$/i.test(name)?'image':'text'; }
  function renderAssets() {
    const f=state.assetFilter,rows=state.assets.filter(x=>(f.type==='all'||fileType(x.name)===f.type)&&x.name.toLowerCase().includes(f.q)&&(!f.source||x.source===f.source));
    $('#assetRows').innerHTML=pageItems('assetsPagination',rows,'assets',renderAssets,'个文件').map(f=>`<tr><td><div class="file-name"><span class="file-icon">${esc(f.name.split('.').pop().toUpperCase().slice(0,4))}</span><div><b>${esc(f.name.split('/').pop())}</b><small>${esc(f.source)}</small></div></div></td><td><span class="file-type">${{video:'视频',audio:'音频',image:'图片',text:'文稿 / 文档'}[fileType(f.name)]}</span></td><td>${size(f.size)}</td><td>${date(f.modified)}</td><td><div class="file-actions"><a class="text-link" href="${esc(f.url)}" target="_blank" rel="noreferrer">预览</a><a class="btn" href="${esc(Guanchao.download(f.url))}" download>下载</a></div></td></tr>`).join('')||'<tr><td colspan="5"><div class="wb-empty">暂无符合条件的素材</div></td></tr>';
  }
  async function settings() {
    const cfg=state.data.settings;
    $('#keyState').textContent=cfg.key_set?'已连接':'未配置';$('#keyState').classList.toggle('not-connected',!cfg.key_set);
    if(!connectionDirty){$('#allowBatchStats').checked=cfg.allow_batch_statistics===true;$('#connectionMax').value=cfg.max_calls_per_job;$('#connectionDaily').value=cfg.daily_call_limit;}
    if(settingsTab==='advanced')await loadServices();
  }
  async function loadServices(){
    if(servicesLoaded||servicesLoading)return;servicesLoading=true;$('#serviceCount').textContent='正在检查…';
    try{const services=(await api('/api/integrations/status')).services;$('#serviceCount').textContent=services.every(s=>s.running)?'正常运行':'部分服务未启动';$('#serviceList').innerHTML=services.map(s=>`<div class="service-item ${s.running?'ready':''}"><i></i><span>${esc(s.name)}</span></div>`).join('');servicesLoaded=true;}
    catch(e){$('#serviceCount').textContent='读取失败';$('#serviceList').innerHTML=`<p class="error-text">${esc(e.message)}</p>`;}finally{servicesLoading=false;}
  }
  function selectSettingsTab(tab,write=true){
    settingsTab=['monitoring','connection','advanced'].includes(tab)?tab:'monitoring';
    $$('[data-settings-tab]').forEach(b=>{const on=b.dataset.settingsTab===settingsTab;b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1;});
    $$('[data-settings-panel]').forEach(p=>p.hidden=p.dataset.settingsPanel!==settingsTab);
    if(write)history.replaceState(null,'','#'+({monitoring:'monitoringSettings',connection:'connection',advanced:'advanced'})[settingsTab]);
    if(settingsTab==='advanced')loadServices();
  }
  function settingsTabFromHash(){const hash=location.hash.slice(1);return ['connection','connectionForm'].includes(hash)?'connection':['advanced','serviceList','legacyTools'].includes(hash)?'advanced':'monitoring';}
  $$('[data-settings-tab]').forEach(b=>b.onclick=()=>selectSettingsTab(b.dataset.settingsTab));
  $('.settings-tabs').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;const tabs=$$('[data-settings-tab]'),i=tabs.indexOf(document.activeElement);if(i<0)return;e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].focus();selectSettingsTab(tabs[next].dataset.settingsTab);});
  if(view==='settings'){selectSettingsTab(settingsTabFromHash(),false);addEventListener('hashchange',()=>selectSettingsTab(settingsTabFromHash(),false));}
  async function load() {
    state.data=await api('/api/workbench/state'); state.jobs=state.data.jobs;
    $('#playbackMethod option[value=batch]').disabled=!state.data.settings.allow_batch_statistics;
    $('#playbackMethod option[value=batch]').textContent=state.data.settings.allow_batch_statistics?'批量接口 · 最多 50 条/次':'批量接口 · 需先在设置开启';
    if(!state.data.settings.allow_batch_statistics)$('#playbackMethod').value='single';
    $('#pageStatus').textContent='更新于 '+String(state.data.generated_at).slice(11,16);
    if(view==='home')renderHome();
    else if(view==='research') {
      const old=$('#workAccount').value; $('#workAccount').innerHTML=options(state.data.accounts); $('#workAccount').value=old; renderLibrary();
      if(!state.video||!state.data.videos.some(v=>v.aweme_id===state.video))state.video=[...state.data.videos].sort((a,b)=>(b.create_time||0)-(a.create_time||0))[0]?.aweme_id;
      if(state.video)await selectVideo(state.video);
    } else if(view==='collect') {
      const old=$('#collectAccount').value; $('#collectAccount').innerHTML=options(state.data.accounts); $('#collectAccount').value=old;
      renderJobs(); if(!state.selected&&state.jobs.length)await openJob(state.jobs[0].id);
    } else if(view==='assets') { state.assets=(await api('/api/integrations/assets')).items; const old=$('#assetSource').value;$('#assetSource').innerHTML='<option value="">全部来源</option>'+[...new Set(state.assets.map(f=>f.source))].map(s=>`<option>${esc(s)}</option>`).join('');$('#assetSource').value=old;renderAssets(); }
    else if(view==='settings')await settings();
  }
  const forms=[['researchFilterForm',()=>{state.researchFilter={q:$('#workSearch').value.trim().toLowerCase(),account:$('#workAccount').value};paging.library.page=0;renderLibrary();},'researchFilterState'],['assetFilterForm',()=>{state.assetFilter={q:$('#assetSearch').value.trim().toLowerCase(),type:$('#assetType').value,source:$('#assetSource').value};paging.assets.page=0;renderAssets();},'assetFilterState'],['taskFilterForm',()=>{state.taskFilter={q:$('#taskSearch').value.trim().toLowerCase(),status:$('#taskStatus').value};paging.jobs.page=0;renderJobs();},'taskFilterState'],['homeFilterForm',()=>{state.homeFilter={q:$('#homeSearch').value.trim().toLowerCase(),role:$('#homeRole').value,status:$('#homeStatus').value,sort:$('#homeSort').value};paging.home.page=0;renderHome();},'homeFilterState']];
  forms.forEach(([id,apply,feedback])=>{const form=$('#'+id);form.onsubmit=e=>{e.preventDefault();apply();$('#'+feedback).textContent='已应用所选条件';};form.addEventListener('input',()=>$('#'+feedback).textContent='条件已修改，点击搜索后生效');form.addEventListener('reset',()=>{setTimeout(()=>{apply();$('#'+feedback).textContent='已重置并搜索';},0);});});
  $('#homeCards').addEventListener('click',e=>{const card=e.target.closest('[data-card-account]');if(card&&!e.target.closest('button,a'))Guanchao.openAccount(card.dataset.cardAccount);});
  $('#homeCards').addEventListener('keydown',e=>{if(e.target.matches('[data-card-account]')&&['Enter',' '].includes(e.key)){e.preventDefault();Guanchao.openAccount(e.target.dataset.cardAccount);}});
  document.addEventListener('click',async e=>{
    const video=e.target.closest('[data-video]'),tab=e.target.closest('[data-tab]'),job=e.target.closest('[data-job]'),cancel=e.target.closest('[data-cancel-job]');
    try {
      if(video)await selectVideo(video.dataset.video);
      if(tab){state.tab=tab.dataset.tab;renderPane();}
      if(job)await openJob(job.dataset.job);
      if(cancel){await api('/api/douk/cancel',{id:cancel.dataset.cancelJob});await openJob(cancel.dataset.cancelJob);}
      if(e.target.closest('#copyCaption')){await navigator.clipboard.writeText(state.detail.description);toast('文案已复制');}
    } catch(error){toast(error.message);}
  });
  $('#collectAction').onchange=toggleAction; $('#collectForm').addEventListener('input',e=>{if(e.target.id!=='confirmCollect')invalidate();});
  $('#confirmCollect').onchange=()=>{$('#startCollect').disabled=!state.plan?.allowed||!$('#confirmCollect').checked||state.submitting;};
  $('#previewCollect').onclick=async()=>{try{invalidate();const values=collectValues(),signature=JSON.stringify(values);const plan=await api('/api/workbench/plan',values);if(signature!==JSON.stringify(collectValues()))return;state.plan={...plan,signature};$('#collectPreview').innerHTML=`<b>最多 ${plan.calls} 次请求</b><br>${plan.units} ${values.action==='account'?'个账号':'条作品'}${plan.paid?' · 使用 TikHub 额度':' · 本机解析'}${plan.reason?'<br>'+esc(plan.reason):''}`;}catch(e){$('#collectError').textContent=e.message;}};
  $('#collectForm').onsubmit=async e=>{e.preventDefault();if(!state.plan?.allowed||!$('#confirmCollect').checked||state.submitting)return;try{const values=collectValues();if(JSON.stringify(values)!==state.plan.signature)throw Error('参数已变化，请重新预览');state.submitting=true;$('#startCollect').disabled=true;const j=await api('/api/workbench/submit',{...values,fingerprint:state.plan.fingerprint,confirmed:true});invalidate();await load();await openJob(j.id);toast('任务已加入队列');}catch(error){$('#collectError').textContent=error.message;}finally{state.submitting=false;}};
  $('#connectionForm').addEventListener('input',()=>connectionDirty=true);
  $('#connectionForm').onsubmit=async e=>{e.preventDefault();if(connectionSaving)return;const button=e.submitter;connectionSaving=true;button.disabled=true;try{await api('/api/playback/settings',{api_key:$('#connectionKey').value,max_calls_per_job:$('#connectionMax').value,daily_call_limit:$('#connectionDaily').value,allow_batch_statistics:$('#allowBatchStats').checked});$('#connectionKey').value='';connectionDirty=false;$('#connectionError').textContent='';await load();toast('连接已保存');window.dispatchEvent(new Event('playback-settings-changed'));}catch(error){$('#connectionError').textContent=error.message;}finally{connectionSaving=false;button.disabled=false;}};
  $('#reloadPage').onclick=async()=>{try{servicesLoaded=false;await load();if(view==='settings')dispatchEvent(new Event('settings-reload-requested'));}catch(e){toast(e.message);}};
  window.addEventListener('gc-data-updated',()=>load().catch(e=>toast(e.message)));
  const titles={home:'工作台',research:'内容研究',collect:'采集中心',assets:'素材库',settings:'设置'};
  document.body.dataset.page=view; document.title='观潮 · '+titles[view]; $('#workbenchTitle').textContent=titles[view]; $$('[data-view]').forEach(s=>s.hidden=s.dataset.view!==view);
  if(args.get('action')&&[...$('#collectAction').options].some(o=>o.value===args.get('action')))$('#collectAction').value=args.get('action');
  if(args.get('video'))$('#collectInput').value=args.get('video'); if(args.get('account'))$('#collectAccount').dataset.initial=args.get('account'); toggleAction();
  load().then(()=>{if(args.get('account'))$('#collectAccount').value=args.get('account');if(view==='collect'&&args.get('job'))openJob(args.get('job')).catch(e=>toast(e.message));}).catch(e=>toast(e.message));
  if(view==='collect')setInterval(async()=>{if(document.hidden)return;try{state.jobs=(await api('/api/workbench/jobs')).items;renderJobs();if(state.selected&&['running','queued','cancelled'].includes(state.job?.status))await openJob(state.selected);}catch(e){$('#pageStatus').textContent=e.message;}},4000);
})();
