(() => {
  'use strict';
  const G=Guanchao,A=GuanchaoAudience,{esc,fmt,api}=G,$=s=>document.querySelector(s);
  const endpoint='/api/discovery/hub/', form=$('#hubFilters'), main=$('.hub-main');
  let catalog,focusCatalog,view='library',applied={},rows=[],total=0,page=0,size=20,dirty=false,loading=0,selected=new Set(),report=null,reportList=[],resetting=false;
  const sourceTitle=id=>catalog.sources.find(s=>s.id===id)?.title||id;
  const time=v=>v?String(v).slice(0,16).replace('T',' '):'—';
  const day=()=>new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'});
  const image=(url,cls='')=>url?`<img class="${cls}" src="${esc(url)}" loading="lazy" alt="" referrerpolicy="no-referrer">`:`<span class="hub-cover-empty ${cls}">影</span>`;
  const duration=n=>n==null?'':Math.floor(n/60)+':'+String(Math.round(n)%60).padStart(2,'0');
  const pct=n=>n==null?'—':(n*100).toFixed(n<.001?3:2)+'%';
  const saved={};try{Object.assign(saved,JSON.parse(sessionStorage.getItem('gc-hub-radar-v2')||'{}'));}catch{}
  function values(){return {...Object.fromEntries(new FormData(form)),kind:view==='pulse'?'hot':'works'};}
  function persist(){if(['library','pulse'].includes(view)&&Object.keys(applied).length){saved[view]={draft:values(),applied:{...applied},size};sessionStorage.setItem('gc-hub-radar-v2',JSON.stringify(saved));}}
  function defaults(){for(const key of ['keyword','source','category'])form.elements[key].value='';form.elements.focus.value='ai';form.elements.data_status.value='all';form.elements.days.value='14';form.elements.sort.value=view==='pulse'?'rank':'quality';}
  function setDirty(){dirty=true;persist();$('#hubFeedback').textContent='条件已修改，点击搜索后生效';A.invalidateQuote($('#hubQuote'));}
  function badges(r,rank=true){return (r.creative?.labels||[]).map(label=>`<span class="hub-tag green">${esc(label)}</span>`).join('')+(r.boards||[]).map(b=>`<span class="hub-tag">${esc(b.title)}${rank&&b.rank!=null?' #'+fmt(b.rank):''}</span>`).join('');}
  function change(r){const n=r.comparison?.rank_change;return n==null?'<small>待积累对照</small>':`<small class="${n>0?'hub-up':n<0?'hub-down':'hub-muted'}">${n>0?'↑ '+n:n<0?'↓ '+Math.abs(n):'持平'}</small>`;}
  function spark(trend){
    const points=(trend||[]).map(p=>Number(p.value??p.hot_score)).filter(Number.isFinite);if(points.length<2)return '<span class="hub-muted">—</span>';
    const min=Math.min(...points),max=Math.max(...points),range=max-min||1;
    return `<svg class="hub-mini-trend" viewBox="0 0 104 30" role="img" aria-label="供应商返回热度趋势"><path d="${points.map((n,i)=>(i?'L':'M')+(i/(points.length-1)*100+2).toFixed(1)+','+(27-(n-min)/range*24).toFixed(1)).join(' ')}"/></svg>`;
  }
  const accountStatus=new Map(),accountBusy=new Set(),accountByUid=new Map();
  function rememberAccounts(items){accountByUid.clear();for(const r of items){if(r.account_monitor){accountStatus.set(String(r.id),r.account_monitor);accountByUid.set(r.account_monitor.sec_uid,r.account_monitor);}else accountStatus.delete(String(r.id));}}
  function accountButton(r,cls='text-link'){
    const id=String(r.id||r.aweme_id||'');if(!id)return '';
    const state=accountStatus.get(id)||r.account_monitor||accountByUid.get(r.sec_uid),busy=accountBusy.has(id),conflict=r.account_identity_conflict;
    return `<button class="${cls} hub-account-monitor" data-hub-account="${esc(id)}" data-account-uid="${esc(state?.sec_uid||r.sec_uid||'')}" ${busy||state?.enabled||conflict?'disabled':''} aria-busy="${busy}" title="${conflict?'作者身份不一致，请核实':state?.enabled?'此账号已在工作台监控':r.sec_uid?'将作者账号加入工作台':'核实作者后加入监控，最多调用 1 次 TikHub 单作品接口'}">${conflict?'作者待核实':busy?'添加中…':state?.enabled?'已监控':state?'恢复监控':'监控账号'}</button>`;
  }
  async function monitorAccount(button){
    const id=button.dataset.hubAccount;if(accountBusy.has(id))return;accountBusy.add(id);
    const matching=()=>[...document.querySelectorAll('[data-hub-account]')].filter(b=>b.dataset.hubAccount===id);
    matching().forEach(b=>{b.dataset.previousText=b.textContent;b.disabled=true;b.textContent='添加中…';b.setAttribute('aria-busy','true');});
    try{
      let result=await api(endpoint+'monitor-account',{aweme_id:id});
      if(result.job){
        let completed=false;
        for(let i=0;i<180;i++){
          const job=await api('/api/douk/jobs/'+result.job.id);
          if(['queued','running'].includes(job.status)){await new Promise(resolve=>setTimeout(resolve,1000));continue;}
          if(!['completed','partial'].includes(job.status))throw Error(job.message||'作者核实失败，请到采集中心查看');
          result=await api(endpoint+'monitor-account',{aweme_id:id,resolve_only:true});completed=true;break;
        }
        if(!completed)throw Error('任务仍在执行，可在采集中心查看进度');
      }
      const account=result.account,state={sec_uid:account.sec_uid,enabled:!!account.enabled};
      accountStatus.set(id,state);accountByUid.set(account.sec_uid,state);
      document.querySelectorAll('[data-hub-account]').forEach(b=>{if(b.dataset.hubAccount===id||b.dataset.accountUid===account.sec_uid){b.dataset.accountUid=account.sec_uid;b.textContent='已监控';b.disabled=true;b.setAttribute('aria-busy','false');b.title='此账号已在工作台监控';}});
      $('#hubFeedback').textContent=(account.nickname||'作者账号')+'已加入工作台监控';
      window.dispatchEvent(new CustomEvent('gc-data-updated',{detail:{type:'accounts',account:account.sec_uid}}));
    }catch(e){matching().forEach(b=>{b.disabled=false;b.textContent=b.dataset.previousText||'监控账号';b.setAttribute('aria-busy','false');});$('#hubError').textContent=e.message;const toast=$('#toast');toast.textContent=e.message;toast.classList.add('show');setTimeout(()=>toast.classList.remove('show'),5000);}
    finally{accountBusy.delete(id);}
  }
  document.addEventListener('click',e=>{const b=e.target.closest('[data-hub-account]');if(b&&!b.disabled)monitorAccount(b);});
  function workRow(r){
    const playStates={pending:'待补采',failed:'请求失败',not_returned:'未返回播放'};
    const metric=(key,extra='')=>`<td class="hub-metric-cell ${extra}" title="${esc(r.metric_fields?.[key]?G.timestamp(r.metric_fields[key].observed_at)+' · '+r.metric_fields[key].source:'')}">${G.metricNumber(r[key])}</td>`;
    return `<tr class="${selected.has(r.id)?'is-selected':''}"><td class="hub-check"><input type="checkbox" data-hub-select="${esc(r.id)}" aria-label="选择作品 ${esc(r.title)}" ${selected.has(r.id)?'checked':''}></td>
      <td><div class="hub-work">${image(r.cover_path||r.cover_url)}<div><button class="hub-work-title" data-hub-detail="${esc(r.id)}" data-kind="works">${esc(r.title)}</button><div class="hub-caption">${time(r.published_at)} 发布 ${duration(r.duration)}</div><div class="hub-tags">${(r.creative?.labels||[]).map(label=>`<span class="hub-tag green">${esc(label)}</span>`).join('')}</div></div></div></td>
      <td class="hub-author-cell">${r.sec_uid?`<a class="hub-author" href="https://www.douyin.com/user/${encodeURIComponent(r.sec_uid)}" target="_blank" rel="noreferrer">${r.avatar_path||r.avatar_url?image(r.avatar_path||r.avatar_url):''}${esc(r.account_name||'作者主页')} ↗</a>`:`<span class="hub-author">${esc(r.account_name||'—')}</span>`}<small>${r.fans==null?'':fmt(r.fans)+' 粉丝'}</small>${accountButton(r)}</td>
      <td class="hub-metric-cell hub-play">${G.metricNumber(r.play_count)}${r.play_count==null?`<small title="${esc(r.last_statistics_attempt?.error||'')}">${playStates[r.play_status]||'待补采'}</small>`:r.play_scope==='organic'?'<small>自然播放</small>':''}${r.growth?.status==='measured'&&!r.growth.stale?`<small class="hub-up">日均 +${fmt(r.growth.per_day)}</small>`:''}</td>
      ${metric('digg_count')}${metric('comment_count')}${metric('collect_count')}${metric('share_count')}
      <td class="hub-time">${time(r.metrics_updated_at||r.last_seen).replace(' ','<br>')}</td><td><div class="hub-actions"><button class="text-link" data-radar-enrich="${esc(r.id)}">更新数据</button><button class="text-link" data-hub-detail="${esc(r.id)}" data-kind="works">明细</button><button class="text-link" data-inline-action="download" data-aid="${esc(r.id)}">下载</button><a class="text-link" href="${esc(r.url)}" target="_blank" rel="noreferrer">原视频 ↗</a></div></td></tr>`;
  }
  function hotRow(r){return `<tr><td class="hub-number">${fmt(r.rank)}${change(r)}</td><td><button class="hub-hot-title" data-hub-detail="${esc(r.id)}" data-kind="hot">${esc(r.title)}</button><div class="hub-tags"><span class="hub-tag">${esc(sourceTitle(r.source))}</span>${r.category?`<span class="hub-tag">${esc(r.category)}</span>`:''}${r.allow_publish===false?'<span class="hub-tag">仅观察</span>':''}</div></td><td class="hub-number">${fmt(r.heat)}<small>${r.comparison?.heat_percent!=null?(r.comparison.heat_percent>0?'+':'')+r.comparison.heat_percent+'%':'待积累对照'}</small></td><td>${spark(r.trend)}</td><td class="hub-number">${fmt(r.publish_count)}</td><td class="hub-time">首次 ${time(r.first_seen).slice(5)}<br>最近 ${time(r.last_seen).slice(5)}</td><td><div class="hub-actions"><button class="text-link" data-hub-detail="${esc(r.id)}" data-kind="hot">明细与趋势</button><button class="text-link" data-hub-use="${esc(r.title)}">用于搜索</button><a class="text-link" href="https://www.douyin.com/search/${encodeURIComponent(r.title)}" target="_blank" rel="noreferrer">抖音搜索 ↗</a></div></td></tr>`;}
  function render(){
    const works=view!=='pulse',table=$('#hubTable');
    const th=(label,key,cls='')=>`<th class="${cls}" aria-sort="${applied.sort===key?(applied.direction==='asc'?'ascending':'descending'):'none'}"><button class="hub-sort-button ${applied.sort===key?'active':''}" data-hub-sort="${key}">${label} ${applied.sort===key?(applied.direction==='asc'?'↑':'↓'):'↕'}</button></th>`;
    table.querySelector('thead').innerHTML=works?`<tr><th class="hub-check"><input type="checkbox" id="hubSelectVisible" aria-label="选择当前页"></th>${th('作品 / 发布时间','new','hub-name-cell')}${th('作者 / 粉丝','author','hub-author-cell')}${th('播放','video_plays','hub-number')}${th('点赞','likes','hub-number')}${th('评论','comments','hub-number')}${th('收藏','saves','hub-number')}${th('分享','shares','hub-number')}${th('数据更新','updated','hub-time-cell')}<th class="hub-operation-cell">操作</th></tr>`:'<tr><th style="width:80px" class="hub-number">榜内序位</th><th style="width:36%">热点 / 来源</th><th class="hub-number">热度</th><th style="width:120px">返回趋势</th><th class="hub-number">参与作品</th><th style="width:150px">收录时间</th><th style="width:170px">操作</th></tr>';
    table.querySelector('tbody').innerHTML=rows.map(works?workRow:hotRow).join('')||`<tr><td colspan="${works?10:7}"><div class="hub-empty">没有符合条件的已存内容。<br><button class="btn" data-creative-find>定向找作品</button></div></td></tr>`;
    G.pagination($('#hubPager'),{total,page,pageSize:size,unit:works?'条作品':'条热点',onChange:(p,s)=>{page=p;size=s;load();$('.hub-table-scroll').scrollTop=0;}});
    const select=$('#hubSelectVisible');if(select){select.checked=!!rows.length&&rows.every(r=>selected.has(r.id));select.onchange=()=>{if(select.checked&&new Set([...selected,...rows.map(r=>r.id)]).size>20){$('#hubError').textContent='每次最多选择 20 条作品加入监控';select.checked=false;return;}rows.forEach(r=>select.checked?selected.add(r.id):selected.delete(r.id));render();};}
    $('#hubEnrich').hidden=!works;$('#hubEnrich').disabled=!selected.size;$('#hubEnrich').textContent=selected.size?'更新选中数据 ('+selected.size+')':'更新选中数据';
    $('#hubMonitor').hidden=!works;$('#hubMonitor').disabled=!selected.size;$('#hubMonitor').textContent=selected.size?'监控作品 ('+selected.size+')':'监控作品';
  }
  async function load(){
    const token=++loading;persist();$('#hubError').textContent='';
    try{const query={...applied,page:page+1,page_size:size};const data=await api(endpoint+'library?query='+encodeURIComponent(JSON.stringify(query)));if(token!==loading)return;rows=data.items;rememberAccounts(rows);total=data.total;page=data.page-1;
      $('#hubLastUpdated').textContent=data.last_updated?'最近采集 '+time(data.last_updated):'';
      $('#hubFeedback').textContent=dirty?'条件已修改，点击搜索后生效':'已保存的数据 · 搜索和翻页不调用接口';
      $('#hubExport').href=endpoint+'export?query='+encodeURIComponent(JSON.stringify(applied));render();
    }catch(e){if(token===loading)$('#hubError').textContent=e.message;}
  }
  function search(){applied=values();dirty=false;page=0;selected.clear();A.invalidateQuote($('#hubQuote'));load();}
  async function selectView(){
    persist();++loading;const hash=location.hash.slice(1);view=['pulse','reports','references'].includes(hash)?hash:'library';
    document.querySelectorAll('[data-hub-view]').forEach(a=>{const active=a.dataset.hubView===view;a.classList.toggle('active',active);if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
    form.hidden=['reports','references'].includes(view);$('#hubLibraryView').hidden=form.hidden;$('#hubReportsView').hidden=view!=='reports';$('#hubReferencesView').hidden=view!=='references';$('#hubError').textContent='';
    if(view==='reports'){await loadReportList();return;}
    if(view==='references'){renderReferences();await window.IpRadar.load();return;}
    const section=view==='pulse'?'hot':'works';form.elements.source.innerHTML='<option value="">全部榜单</option>'+catalog.sources.filter(s=>s.section===section&&!['native','native_hot'].includes(s.id)).map(s=>`<option value="${s.id}">${esc(s.title)}</option>`).join('');
    defaults();const old=saved[view];if(old?.draft)for(const [k,v] of Object.entries(old.draft))if(form.elements[k])form.elements[k].value=v;
    form.elements.days.hidden=view==='pulse';form.elements.source.hidden=view!=='pulse';form.elements.data_status.hidden=view==='pulse';form.elements.category.hidden=true;form.elements.keyword.placeholder=view==='pulse'?'搜索已收录的热点':'搜索标题、作者、作品 ID';
    applied=old?.applied||values();dirty=!!old&&JSON.stringify(values())!==JSON.stringify({...values(),...applied});page=0;size=old?.size||20;selected.clear();$('#hubFeedback').textContent=dirty?'条件已修改，点击搜索后生效':'读取已保存数据…';await load();
  }
  form.onsubmit=e=>{e.preventDefault();search();};form.onreset=e=>{if(resetting)return;e.preventDefault();defaults();search();};form.addEventListener('input',setDirty);form.addEventListener('change',setDirty);
  $('#hubDensity').onclick=e=>{const compact=main.classList.toggle('hub-compact');e.currentTarget.textContent=compact?'标准显示':'紧凑显示';e.currentTarget.setAttribute('aria-pressed',String(compact));};
  $('#hubTable').addEventListener('change',e=>{if(e.target.dataset.hubSelect){if(e.target.checked&&selected.size>=20){e.target.checked=false;$('#hubError').textContent='每次最多选择 20 条作品';return;}e.target.checked?selected.add(e.target.dataset.hubSelect):selected.delete(e.target.dataset.hubSelect);render();}});
  $('#hubMonitor').onclick=()=>A.runInline($('#hubQuote'),{source:'monitor',aweme_ids:[...selected]},{onComplete:()=>{selected.clear();render();$('#hubFeedback').textContent='已加入工作台监控。';}});
  function renderReferences(){
    $('#hubReferenceCards').innerHTML=focusCatalog.references.map((r,i)=>`<article class="hub-reference-card"><div class="hub-reference-heading"><h3>${esc(r.name)}</h3><span class="hub-tag green">${esc(focusCatalog.lanes.find(l=>l.id===r.lane)?.title)}</span></div><p class="hub-reference-author">${esc(r.creator)}</p><p>${esc(r.note)}</p><div class="hub-actions"><a class="btn" href="${esc(r.url)}" target="_blank" rel="noreferrer">查看原作 ↗</a><button class="btn" data-creative-reference="${i}">找同类作品</button>${r.aweme_id?accountButton({id:r.aweme_id}):''}</div></article>`).join('');
  }
  const creativeForm=$('#hubCreativeForm');
  function creativeSeeds(){
    const lane=focusCatalog.lanes.find(l=>l.id===creativeForm.elements.lane.value);
    $('#hubCreativeSeeds').innerHTML=lane.keywords.map(w=>`<button class="hub-seed" type="button" data-creative-seed="${esc(w)}">${esc(w)}</button>`).join('');
    $('#hubCreativeStudy').textContent=lane.study;
  }
  function openCreative(lane,keyword){
    const value=focusCatalog.lanes.some(l=>l.id===lane)?lane:'comic';
    creativeForm.elements.lane.innerHTML=focusCatalog.lanes.map(l=>`<option value="${l.id}">${esc(l.title)}</option>`).join('');
    creativeForm.elements.lane.value=value;creativeForm.elements.days.value=keyword?'0':'7';
    creativeForm.elements.keyword.value=keyword||focusCatalog.lanes.find(l=>l.id===value).keywords[0];
    creativeSeeds();A.invalidateQuote($('#hubCreativeQuote'));$('#hubCreativeError').textContent='';G.openDialog($('#hubCreative'));
  }
  function creativeQuery(){return {source:'search',keyword:creativeForm.elements.keyword.value.trim(),days:Number(creativeForm.elements.days.value),duration:0};}
  creativeForm.addEventListener('input',()=>{A.invalidateQuote($('#hubCreativeQuote'));$('#hubCreativeError').textContent='';});
  creativeForm.elements.lane.onchange=()=>{creativeSeeds();creativeForm.elements.keyword.value=focusCatalog.lanes.find(l=>l.id===creativeForm.elements.lane.value).keywords[0];A.invalidateQuote($('#hubCreativeQuote'));};
  creativeForm.onsubmit=async e=>{
    e.preventDefault();const query=creativeQuery(),lane=creativeForm.elements.lane.value;
    await A.runInline($('#hubCreativeQuote'),query,{onComplete:()=>{
      const b=document.createElement('button');b.className='btn';b.textContent='查看已保存的作品';b.type='button';
      b.onclick=async()=>{$('#hubCreative').close();if(view!=='library'){history.replaceState(null,'','#library');await selectView();}defaults();form.elements.focus.value=lane;form.elements.source.value='search';search();};
      $('#hubCreativeQuote').append(b);
    }});
  };
  $('#hubCreativeSchedule').onclick=()=>{if(!creativeForm.reportValidity())return;const query=creativeQuery();$('#hubCreative').close();A.schedule(query);};
  $('#hubFind').onclick=()=>openCreative(form.elements.focus.value);
  document.addEventListener('click',e=>{
    const b=e.target.closest('button');if(!b)return;
    if(b.hasAttribute('data-creative-find'))openCreative(form.elements.focus.value);
    if(b.hasAttribute('data-creative-seed')){creativeForm.elements.keyword.value=b.dataset.creativeSeed;A.invalidateQuote($('#hubCreativeQuote'));}
    if(b.hasAttribute('data-creative-reference')){const r=focusCatalog.references[Number(b.dataset.creativeReference)];openCreative(r.lane,r.keyword);}
  });
  async function detail(kind,id){
    const dialog=$('#hubDetail');dialog.dataset.kind=kind;dialog.dataset.aid=id;G.openDialog(dialog);$('#hubDetailBody').innerHTML='<div class="hub-empty">读取收录记录…</div>';
    try{const data=await api(endpoint+'history?kind='+kind+'&id='+encodeURIComponent(id));const r=data.item;
      const facts=kind==='works'?[['作品播放',fmt(r.play_count)],['榜单播放',fmt(r.board_play_count)],['作者粉丝',fmt(r.fans)],['窗口涨粉',fmt(r.follow_count)],['榜单分值',fmt(r.index)],['点赞',fmt(r.digg_count)],['评论',fmt(r.comment_count)],['收藏',fmt(r.collect_count)],['分享',fmt(r.share_count)],['下载',fmt(r.download_count)],['点赞率',pct(r.like_rate)],['涨粉率',pct(r.follow_rate)],['完播率',pct(r.completion_rate)]]:[['榜内序位',fmt(r.rank)],['热度',fmt(r.heat)],['参与作品',fmt(r.publish_count)],['话题播放',fmt(r.topic_plays)]];
      $('#hubDetailBody').innerHTML=`<div class="hub-detail-top" ${kind==='hot'?'style="grid-template-columns:1fr"':''}>${kind==='works'?image(r.cover_path||r.cover_url,'hub-detail-cover'):''}<div><h3>${esc(r.title)}</h3><div class="hub-tags">${badges(r)}</div><p class="hub-detail-caption">${kind==='works'?esc(r.account_name||'')+'　'+time(r.published_at)+' 发布　'+duration(r.duration):esc(r.category||'')}<br>首次收录 ${time(r.first_seen)}　上次数据更新 ${esc(G.timestamp(r.metrics_updated_at||r.last_seen))}</p><div class="hub-actions">${kind==='works'?`<a class="btn" href="${esc(r.url)}" target="_blank" rel="noreferrer">打开原视频 ↗</a><button class="btn" data-inline-action="download" data-aid="${esc(r.id)}">下载视频</button><button class="btn primary" data-radar-enrich="${esc(r.id)}">更新数据</button><button class="btn" data-hub-monitor-one="${esc(r.id)}">监控作品</button>${accountButton(r,'btn')}`:`<a class="btn" href="https://www.douyin.com/search/${encodeURIComponent(r.title)}" target="_blank" rel="noreferrer">抖音搜索 ↗</a><button class="btn" data-hub-use="${esc(r.title)}">用于搜索</button>`}<button class="btn" data-hub-copy>复制文案</button></div><dl class="hub-detail-facts">${facts.filter(([,v])=>v!=='—').map(([label,v])=>`<div><dt>${label}</dt><dd>${v}</dd></div>`).join('')}</dl></div></div><div id="hubDetailQuote" hidden></div><div data-hub-trend></div>${kind==='works'?`<nav class="hub-history-tabs"><button class="btn active" data-history-tab="metrics">数据历史</button><button class="btn" data-history-tab="search">收录历史</button></nav><div data-history-pane="metrics"><div class="table-scroll"><table><thead><tr><th>更新时间</th><th>来源</th><th>播放范围</th><th class="hub-number">播放</th><th class="hub-number">点赞</th><th class="hub-number">评论</th><th class="hub-number">收藏</th><th class="hub-number">分享</th></tr></thead><tbody>${(data.metric_history||[]).slice().reverse().map(h=>`<tr><td>${esc(G.timestamp(h.observed_at))}</td><td>${h.source==='tikhub_app'?'单作品播放':h.source==='tikhub_app_v3_detail'?'作品详情':esc(h.source)}</td><td>${h.scope==='organic'?'自然播放':h.scope==='total'?'累计播放':'未指定'}</td><td class="hub-number">${fmt(h.play_count)}</td><td class="hub-number">${fmt(h.digg_count)}</td><td class="hub-number">${fmt(h.comment_count)}</td><td class="hub-number">${fmt(h.collect_count)}</td><td class="hub-number">${fmt(h.share_count)}</td></tr>`).join('')||'<tr><td colspan="8">尚未补采。点击“更新数据”建立第一条记录。</td></tr>'}</tbody></table></div></div>`:'<h4>历史记录</h4>'}<div data-history-pane="search" ${kind==='works'?'hidden':''}><div class="table-scroll"><table><thead><tr><th>采集时间</th><th>榜单</th><th>范围</th><th>排名</th><th>热度</th><th>榜单播放</th><th>点赞</th><th>评论</th><th>收藏</th><th>分享</th></tr></thead><tbody>${data.history.map(h=>`<tr><td>${time(h.observed_at)}</td><td>${esc(h.source_title)}</td><td>${h.query.keyword?esc(h.query.keyword):h.query.window?Math.round(h.query.window/24)+'天':'实时'}${h.query.categories?.length?' / '+h.query.categories.map(c=>esc(catalog.categories.find(v=>String(v.value)===c)?.label||c)).join('、'):''}</td><td>${fmt(h.rank)}</td><td>${fmt(h.heat)}</td><td>${fmt(h.board_play_count)}</td><td>${fmt(h.digg_count)}</td><td>${fmt(h.comment_count)}</td><td>${fmt(h.collect_count)}</td><td>${fmt(h.share_count)}</td></tr>`).join('')}</tbody></table></div></div><details><summary>查看供应商返回字段</summary><pre>${esc(JSON.stringify(r.provider_data||r,null,2))}</pre></details>`;
      $('#hubDetailBody [data-hub-copy]').onclick=async()=>{if(navigator.clipboard)await navigator.clipboard.writeText(r.title);else{const t=document.createElement('textarea');t.value=r.title;document.body.append(t);t.select();document.execCommand('copy');t.remove();}};
      if(r.trend?.length){const series=r.trend.map(p=>({x:String(p.datetime||p.date||p.key),metrics:{heat:Number(p.hot_score??p.value)}})).filter(p=>Number.isFinite(p.metrics.heat));A.charts($('#hubDetailBody [data-hub-trend]'),{series:[{name:'供应商返回热度',points:series,unit:'index'}],created_at:r.last_seen},{source:r.source});}
    }catch(e){$('#hubDetailBody').innerHTML='<p class="hub-error">'+esc(e.message)+'</p>';}
  }
  function useKeyword(word){
    $('#hubDetail').close();const draft={keyword:word,source:'',category:'',focus:'ai',days:'0',sort:'seen',kind:'works'};
    saved.library={applied:saved.library?.applied||{keyword:'',kind:'works',focus:'ai',sort:'seen'},draft,size:20};
    if(view==='library'){form.elements.keyword.value=word;setDirty();}else location.hash='library';
  }
  document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.hasAttribute('data-hub-close'))b.closest('dialog').close();if(b.dataset.hubDetail)detail(b.dataset.kind||'works',b.dataset.hubDetail);if(b.dataset.hubUse)useKeyword(b.dataset.hubUse);if(b.dataset.hubMonitorOne)A.runInline($('#hubDetailQuote'),{source:'monitor',aweme_id:b.dataset.hubMonitorOne});});
  async function automationLabel(){$('#hubAutomation').textContent='监测计划';$('#hubAutomation').title='AI 方向搜索、重点作品追踪与请求上限';}
  $('#hubAutomation').onclick=()=>window.IpRadar.settings();
  $('#hubEnrich').onclick=()=>window.IpRadar.enrich([...selected]);
  $('#hubTable').addEventListener('click',e=>{const b=e.target.closest('[data-hub-sort]');if(b){const key=b.dataset.hubSort;applied={...applied,sort:key,direction:applied.sort===key&&applied.direction!=='asc'?'asc':'desc'};form.elements.sort.value=key;page=0;load();}});
  async function loadReportList(){
    const data=await api(endpoint+'reports');reportList=data.items.filter(r=>r.id.endsWith('-ai'));const picker=$('#hubReportHistory');picker.innerHTML='<option value="">已保存的报告</option>'+reportList.map(r=>`<option value="${r.id}">${r.kind==='daily'?'日报':'周报'}　${r.id.slice(r.kind.length+1,-3)}　${time(r.generated_at).slice(5)}</option>`).join('');
    if(report&&reportList.some(r=>r.id===report.id))picker.value=report.id;else if(reportList.length){const initial=reportList.find(r=>r.kind===$('#hubReportFilters').elements.kind.value)||reportList[0];picker.value=initial.id;await loadReport(initial.id);}else $('#hubReportBody').innerHTML='<div class="hub-table-panel hub-empty">榜单采集后，可生成本地报告；日报和周报也会按设置的时间自动保存。</div>';
  }
  async function loadReport(id){try{report=await api(endpoint+'reports?id='+encodeURIComponent(id));rememberAccounts([...(report.references||[]),...(report.opportunities||[]).map(r=>r.work)]);renderReport();}catch(e){$('#hubError').textContent=e.message;}}
  function renderReport(){renderCreativeReport();}
  function renderCreativeReport(){
    if(report.radar){const r=report,f=$('#hubReportFilters');f.elements.kind.value=r.kind;f.elements.date.value=r.anchor_date;$('#hubReportHistory').value=r.id;$('#hubReportExport').hidden=false;$('#hubReportExport').href=endpoint+'report-export?id='+encodeURIComponent(r.id);$('#hubReportBody').innerHTML=window.IpRadar.report(r);return;}
    const r=report,f=$('#hubReportFilters');rememberAccounts([...(r.references||[]),...(r.opportunities||[]).map(i=>i.work)]);f.elements.kind.value=r.kind;f.elements.date.value=r.anchor_date;$('#hubReportHistory').value=r.id;
    $('#hubReportExport').hidden=false;$('#hubReportExport').href=endpoint+'report-export?id='+encodeURIComponent(r.id);
    const referenceRow=w=>`<tr><td><div class="hub-work">${image(w.cover_path||w.cover_url)}<div><button class="hub-work-title" data-hub-detail="${esc(w.id)}" data-kind="works">${esc(w.title)}</button><div class="hub-tags">${(w.creative?.labels||[]).map(l=>`<span class="hub-tag green">${esc(l)}</span>`).join('')}</div><div class="hub-caption">${esc(w.account_name||'—')} · ${time(w.published_at)} 发布</div></div></div></td><td class="hub-number">${fmt(w.digg_count)}</td><td class="hub-number">${fmt(w.fans)}</td><td class="hub-number">${fmt(w.board_play_count)}</td><td>${esc(G.timestamp(w.last_seen,true))}</td><td><div class="hub-actions"><button class="text-link" data-hub-detail="${esc(w.id)}" data-kind="works">明细</button>${accountButton(w)}<button class="text-link" data-inline-action="download" data-aid="${esc(w.id)}">下载</button><a class="text-link" href="${esc(w.url)}" target="_blank" rel="noreferrer">原作 ↗</a></div></td></tr>`;
    $('#hubReportBody').innerHTML=`<article class="hub-report-document"><header class="hub-report-heading"><h2>${esc(r.title)}</h2><span>${time(r.start_at)} — ${time(r.end_at)}</span></header><section class="hub-report-section"><h3>优先拆解</h3><div class="hub-ideas">${r.opportunities.map(i=>`<article class="hub-idea"><span class="hub-tag green">${esc(i.label)}</span><h4><button class="hub-work-title" data-hub-detail="${esc(i.work.id)}" data-kind="works">${esc(i.work.title)}</button></h4><div class="hub-caption">${esc(i.work.account_name)} · ${fmt(i.work.digg_count)} 赞 · ${time(i.work.published_at)} 发布</div><p>${esc(i.study)}</p><div class="hub-actions"><a class="text-link" href="${esc(i.work.url)}" target="_blank" rel="noreferrer">看原作 ↗</a><button class="text-link" data-hub-detail="${esc(i.work.id)}" data-kind="works">收录明细</button>${accountButton(i.work)}</div></article>`).join('')||'<div class="hub-empty">本期尚无相关作品。先定向抓取，再生成报告。<br><button class="btn" data-creative-find>定向找作品</button></div>'}</div></section><div class="hub-report-grid"><section class="hub-report-section"><h3>方向覆盖</h3><table><thead><tr><th>方向</th><th class="hub-number">收录作品</th><th class="hub-number">作者</th><th>下一步</th></tr></thead><tbody>${r.lanes.map(l=>`<tr><td>${esc(l.title)}</td><td class="hub-number">${l.works}</td><td class="hub-number">${l.accounts}</td><td><button class="text-link" data-find-lane="${l.id}">找作品</button></td></tr>`).join('')}</tbody></table></section><section class="hub-report-section"><h3>作品里重复出现的标签</h3><div class="hub-keywords">${r.keywords.slice(0,12).map(k=>`<span class="hub-keyword">#${esc(k.word)}<b>${k.count}</b><button class="text-link" data-hub-detail="${esc(k.samples[0])}" data-kind="works">样本</button></span>`).join('')||'<p>暂无重复标签，继续积累样本。</p>'}</div>${r.structures.map(s=>`<div class="hub-format-row"><b>${esc(s.label)}</b><span>${s.count} 条样本</span><button class="text-link" data-hub-detail="${esc(s.samples[0])}" data-kind="works">看样本</button></div>`).join('')}</section></div><section class="hub-report-section"><h3>本期参考作品</h3><div class="table-scroll"><table><thead><tr><th style="width:45%">作品</th><th class="hub-number">点赞</th><th class="hub-number">粉丝</th><th class="hub-number">榜单窗口播放</th><th>上次更新</th><th>操作</th></tr></thead><tbody>${r.references.map(referenceRow).join('')||'<tr><td colspan="6">没有相关采集记录</td></tr>'}</tbody></table></div></section></article>`;
  }
  $('#hubReportFilters').onsubmit=e=>{e.preventDefault();const f=e.target,picked=$('#hubReportHistory').value,kind=f.elements.kind.value,date=f.elements.date.value;const id=picked||kind+'-'+date+'-ai';if(reportList.some(r=>r.id===id))loadReport(id);else{$('#hubReportBody').innerHTML='<div class="hub-table-panel hub-empty">这个日期尚无报告，点击“生成本地报告”即可。</div>';$('#hubReportExport').hidden=true;}};
  $('#hubReportFilters').onreset=e=>{e.preventDefault();e.target.elements.kind.value='daily';e.target.elements.date.value=day();$('#hubReportHistory').value=reportList[0]?.id||'';if(reportList.length)loadReport(reportList[0].id);};
  $('#hubGenerate').onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{const f=$('#hubReportFilters');report=await api(endpoint+'generate',{kind:f.elements.kind.value,date:f.elements.date.value,focus:'ai'});await loadReportList();renderReport();}catch(e){$('#hubError').textContent=e.message;}finally{b.disabled=false;}};
  $('#hubReportFilters').addEventListener('input',e=>{if(e.target.id!=='hubReportHistory')$('#hubReportHistory').value='';});
  window.addEventListener('hashchange',()=>selectView().catch(e=>$('#hubError').textContent=e.message));
  window.addEventListener('gc-data-updated',e=>{if(['library','pulse'].includes(view))load();const modal=$('#hubDetail');if((e.detail?.type==='radar'||!e.detail)&&modal.open)detail(modal.dataset.kind,modal.dataset.aid);});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-find-lane]');if(b)openCreative(b.dataset.findLane);});
  document.addEventListener('click',e=>{const b=e.target.closest('[data-history-tab]');if(b){const host=b.closest('.product-dialog-body');host.querySelectorAll('[data-history-pane]').forEach(p=>p.hidden=p.dataset.historyPane!==b.dataset.historyTab);host.querySelectorAll('[data-history-tab]').forEach(t=>t.classList.toggle('active',t===b));}});
  window.HubDiscovery={accountButton,detail};
  Promise.all([api('/api/discovery/catalog'),api(endpoint+'focus')]).then(async ([c,f])=>{catalog=c;focusCatalog=f;form.elements.category.innerHTML='<option value="">全部采集分类</option>'+c.categories.map(v=>`<option value="${v.value}">${esc(v.label)}</option>`).join('');$('#hubReportFilters').elements.date.value=day();await selectView();await automationLabel();}).catch(e=>$('#hubError').textContent=e.message);
})();
