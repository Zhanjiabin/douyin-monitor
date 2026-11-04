(() => {
  const G=Guanchao,A=GuanchaoAudience,{esc,fmt,api}=G,$=s=>document.querySelector(s),form=$('#discoveryFilters');
  const defaults={works:'native',hot:'native_hot',keywords:'keyword'},sources={works:['native','search','lowfan','completion','likes','followers'],hot:['native_hot','rising','topics','searches','industry'],keywords:['keyword']};
  let catalog,watchData=[],view='works',applied={},snapshot=null,dirty=false,resetting=false,version=0,selected=new Set(),page=0,pageSize=20,watchPage=0,watchSize=20;
  const saved={};try{Object.assign(saved,JSON.parse(sessionStorage.getItem('gc-discovery-v2')||'{}'));}catch{}
  const isNative=s=>s==='native'||s==='native_hot';
  function persist(){saved[view]={applied:{...applied},draft:Object.fromEntries(new FormData(form))};sessionStorage.setItem('gc-discovery-v2',JSON.stringify(saved));}
  function values(){const d=Object.fromEntries(new FormData(form));for(const k of ['window','days','duration','limit','native_sort'])d[k]=Number(d[k]);return d;}
  function controls(){
    const source=form.elements.source.value,native=source==='native',search=native||source==='search',date=source==='industry'||view==='keywords';
    $('#profileField').hidden=!native;$('#limitField').hidden=!native;$('#nativeSortField').hidden=!native;
    $('#windowField').hidden=search||['rising','native_hot'].includes(source)||date;$('#daysField').hidden=!search;
    $('#categoryField').hidden=['search','native','native_hot','rising','searches','keyword'].includes(source);$('#dateFields').hidden=!date||view==='keywords';
    $('#discoveryExpand').hidden=view!=='works';$('#durationField').hidden=source!=='search';$('#discoveryMonitor').hidden=view!=='works';
    $('#discoveryMore').hidden=!snapshot?.next_page||date||isNative(source);$('#discoverySchedule').hidden=view==='keywords';
    const choices=native?[[7,'近一周'],[1,'近一天'],[180,'近半年'],[0,'不限时间']]:[[7,'近七天'],[3,'近三天'],[30,'近一个月'],[0,'不限时间']],signature=native?'native':'tikhub',days=form.elements.days;
    if(days.dataset.options!==signature){const old=Number(days.value||7);days.innerHTML=choices.map(([n,t])=>`<option value="${n}">${t}</option>`).join('');days.value=String(choices.some(([n])=>n===old)?old:7);days.dataset.options=signature;}
    form.elements.keyword.placeholder=view==='keywords'?'搜索已追踪关键词':native?'多个词用逗号分隔':'搜索标题、热点或关键词';
  }
  function pending(){dirty=true;$('#discoveryFeedback').textContent='条件已修改，点击搜索后生效';A.invalidateQuote($('#discoveryQuote'));controls();persist();}
  function image(url){return url?`<img src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`:'<span class="cover-fallback">▧</span>';}
  function filtered(){let rows=snapshot?.items||[];if(applied.source==='native_hot'&&applied.keyword)rows=rows.filter(r=>r.title.includes(applied.keyword));if(applied.max_fans)rows=rows.filter(r=>r.fans!=null&&r.fans<=Number(applied.max_fans));if(applied.local_sort==='likes')rows=[...rows].sort((a,b)=>(b.digg_count??-1)-(a.digg_count??-1));if(applied.local_sort==='new')rows=[...rows].sort((a,b)=>(b.published_at||'').localeCompare(a.published_at||''));return rows;}
  function duration(seconds){if(seconds==null)return '时长未提供';const n=Math.round(seconds),m=Math.floor(n/60);return (m>=60?Math.floor(m/60)+':'+String(m%60).padStart(2,'0'):String(m).padStart(2,'0'))+':'+String(n%60).padStart(2,'0');}
  function workRow(r){
    const native=applied.source==='native',rate=applied.source==='likes'?r.like_rate:applied.source==='followers'?r.follow_rate:null;
    const metric=native?fmt(r.collect_count):['likes','followers'].includes(applied.source)?rate==null?'—':(rate*100).toFixed(2)+'%':fmt(r.index);
    return `<tr><td class="check-cell"><input type="checkbox" data-select="${r.id}" aria-label="选择作品 ${esc(r.title)}" ${selected.has(r.id)?'checked':''}></td><td><div class="discover-work">${image(r.cover_path||r.cover_url)}<div><strong>${esc(r.title)}</strong><small>${duration(r.duration)}${r.matched_keyword?' · '+esc(r.matched_keyword):''}${r.is_ai_declared?' · 作者声明 AI':''}</small></div></div></td><td class="discover-account"><b>${r.sec_uid?`<a href="https://www.douyin.com/user/${encodeURIComponent(r.sec_uid)}" target="_blank" rel="noreferrer">${esc(r.account_name||'作者主页')} ↗</a>`:esc(r.account_name||'未提供作者')}</b><small>${r.fans==null?'':fmt(r.fans)+' 粉丝'}</small></td><td class="number">${esc(r.published_at?.slice(0,16).replace('T',' ')||'—')}</td><td class="number">${fmt(r.board_play_count??r.play_count)}</td><td class="number">${fmt(r.digg_count)}</td><td class="number">${metric}</td><td><div class="discover-actions"><button class="text-link" data-open-work="${r.id}">查看</button><button class="text-link" data-inline-action="download" data-aid="${r.id}">下载</button><a class="text-link" href="${esc(r.url)}" target="_blank" rel="noreferrer">原视频 ↗</a></div></td></tr>`;
  }
  function hotRow(r){
    const cells=applied.source==='topics'?`<td class="number">${fmt(r.topic_plays)}</td><td class="number">${fmt(r.publish_count)}</td>`:applied.source==='searches'?`<td class="number">${r.trend.length} 个数据点</td>`:applied.source==='native_hot'?'':`<td class="number ${Number(r.rank_change)>0?'rank-up':Number(r.rank_change)<0?'rank-down':''}">${r.rank_change==null?'—':Number(r.rank_change)>0?'↑ '+r.rank_change:Number(r.rank_change)<0?'↓ '+Math.abs(r.rank_change):'持平'}</td><td>${esc(r.category||'—')}</td>`;
    return `<tr><td class="number">${fmt(r.rank)}</td><td class="hot-name">${esc(r.title)}</td><td class="number">${fmt(r.heat)}</td>${cells}<td><div class="discover-actions">${r.trend.length?`<button class="text-link" data-hot-trend="${esc(r.id)}">趋势</button>`:''}<button class="text-link" data-related="${esc(r.title)}">相关作品</button></div></td></tr>`;
  }
  function updateSelection(){$('#discoveryMonitor').disabled=!selected.size;$('#discoveryMonitor').textContent=selected.size?'加入监控 ('+selected.size+')':'加入监控';}
  function render(){
    const rows=filtered(),table=$('#discoveryTable'),works=view==='works',native=applied.source==='native';
    const metric=native?'收藏':applied.source==='likes'?'点赞率':applied.source==='followers'?'涨粉率':applied.source==='search'?'热度指数':'榜单分值';
    const hotColumns=applied.source==='topics'?'<th>话题播放</th><th>参与作品</th>':applied.source==='searches'?'<th>返回趋势</th>':applied.source==='native_hot'?'':'<th>排名变化</th><th>分类</th>';
    table.querySelector('thead').innerHTML=works?`<tr><th class="check-cell"><input type="checkbox" id="selectVisible" aria-label="选择当前页"></th><th>作品</th><th>作者 / 粉丝</th><th>发布时间</th><th>${native?'播放':'榜单播放'}</th><th>点赞</th><th>${metric}</th><th>操作</th></tr>`:`<tr><th>排名</th><th>${applied.source==='topics'?'话题':applied.source==='searches'?'搜索词':'热点'}</th><th>${applied.source==='searches'?'搜索指数':'热度'}</th>${hotColumns}<th>操作</th></tr>`;
    page=Math.min(page,Math.max(0,Math.ceil(rows.length/pageSize)-1));
    G.pagination($('#discoveryPager'),{total:rows.length,page,pageSize,unit:works?'条作品':'条热点',onChange:(p,size)=>{page=p;pageSize=size;render();$('.discovery-table-wrap').scrollTop=0;}});
    const visible=rows.slice(page*pageSize,(page+1)*pageSize);
    table.querySelector('tbody').innerHTML=visible.map(r=>works?workRow(r):hotRow(r)).join('')||`<tr><td colspan="8"><div class="discovery-empty">${snapshot?'没有符合条件的结果。':native?'尚未采集这个方向，点击“抓取最新”使用 MediaCrawlerPro 搜索。':'暂无这组条件的缓存，点击“抓取最新”查询。'}</div></td></tr>`;
    updateSelection();const select=$('#selectVisible');if(select){select.checked=!!visible.length&&visible.every(r=>selected.has(r.id));select.onchange=()=>{visible.forEach(r=>select.checked?selected.add(r.id):selected.delete(r.id));render();};}controls();
  }
  async function load(){
    const mine=++version;$('#discoveryError').textContent='';persist();$('#discoveryExport').href='/api/discovery/export?query='+encodeURIComponent(JSON.stringify(applied));if(view==='keywords'){await loadWatches();return;}
    try{const data=await api('/api/discovery/cache?query='+encodeURIComponent(JSON.stringify(applied)));if(mine!==version)return;snapshot=data.snapshot;$('#discoveryUpdated').textContent=snapshot?'采集于 '+snapshot.created_at.slice(0,16).replace('T',' '):'';
      $('#discoveryFeedback').textContent=dirty?'条件已修改，点击搜索后生效':isNative(applied.source)?'MediaCrawlerPro · 不消耗 TikHub 额度':applied.source==='search'?'热度指数与播放量分开显示。':'TikHub · 抓取前确认请求数';
      $('#industryView').hidden=applied.source!=='industry';$('#discoveryResults').hidden=applied.source==='industry';if(applied.source==='industry')A.charts($('#industryView'),snapshot,applied);else render();controls();if(snapshot?.errors?.length)$('#discoveryError').textContent=snapshot.errors.map(e=>e.error).join('；');
    }catch(e){$('#discoveryError').textContent=e.message;}
  }
  async function loadWatches(){const data=await api('/api/discovery/watches');watchData=data.items;const rows=data.items.filter(r=>!applied.keyword||r.keyword.toLowerCase().includes(applied.keyword.toLowerCase()));watchPage=Math.min(watchPage,Math.max(0,Math.ceil(rows.length/watchSize)-1));G.pagination($('#keywordPager'),{total:rows.length,page:watchPage,pageSize:watchSize,unit:'个关键词',onChange:(p,s)=>{watchPage=p;watchSize=s;loadWatches();}});$('#keywordRows').innerHTML=rows.slice(watchPage*watchSize,(watchPage+1)*watchSize).map(r=>`<tr><td>${esc(r.keyword)}</td><td>${esc(r.last_fetched_at?.slice(0,16).replace('T',' ')||'尚未抓取')}</td><td><div class="discover-actions"><button class="text-link" data-keyword-trend="${esc(r.keyword)}">查看趋势</button><button class="text-link" data-keyword-works="${esc(r.keyword)}">相关作品</button><button class="text-link" data-keyword-rule="${esc(r.keyword)}">定时更新</button><button class="text-link" data-delete-keyword="${r.id}">移除</button></div></td></tr>`).join('')||'<tr><td colspan="3"><div class="discovery-empty">添加你关注的关键词。</div></td></tr>';}
  function defaultFields(){resetting=true;form.reset();resetting=false;form.elements.source.value=defaults[view];form.elements.profile.value='ai';form.elements.keyword.value=view==='works'?catalog.profiles.find(p=>p.id==='ai').keywords.join('，'):'';controls();}
  function search(){applied=values();dirty=false;selected.clear();page=0;watchPage=0;A.invalidateQuote($('#discoveryQuote'));load();}
  async function selectView(){
    const next=location.hash.slice(1);if(applied.source)persist();view=next in defaults?next:'works';const stored=saved[view]||{};
    $('#discoverySource').innerHTML=sources[view].map(id=>`<option value="${id}">${esc((isNative(id)?'Pro · ':'TikHub · ')+catalog.sources.find(s=>s.id===id).title.replace(' · Pro','').replace('Pro ',''))}</option>`).join('');
    defaultFields();Object.entries(stored.draft||stored.applied||{}).forEach(([k,v])=>{if(form.elements[k])form.elements[k].value=v;});controls();applied=stored.applied||values();dirty=JSON.stringify(values())!==JSON.stringify({...values(),...applied});selected.clear();page=0;snapshot=null;A.invalidateQuote($('#discoveryQuote'));
    $('#keywordView').hidden=view!=='keywords';$('#discoveryResults').hidden=view==='keywords';$('#industryView').hidden=true;$('.discovery-toolbar').hidden=view==='keywords';form.elements.source.closest('label').hidden=view==='keywords';
    document.querySelectorAll('[data-view]').forEach(a=>{const active=a.dataset.view===view;a.classList.toggle('active',active);if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});controls();await load();
  }
  form.onsubmit=e=>{e.preventDefault();search();};form.onreset=e=>{if(resetting)return;e.preventDefault();defaultFields();search();};
  form.elements.source.onchange=()=>{if(view==='works'&&form.elements.source.value!=='native')form.elements.keyword.value=form.elements.keyword.value.split(/[,，;；\n]/)[0];};
  form.elements.profile.onchange=()=>{form.elements.keyword.value=catalog.profiles.find(p=>p.id===form.elements.profile.value).keywords.join('，');};
  form.addEventListener('input',pending);form.addEventListener('change',pending);
  $('#discoveryExpand').onclick=()=>{const open=$('#discoveryExtra').hidden;$('#discoveryExtra').hidden=!open;$('#discoveryExpand').textContent=open?'收起筛选':'展开筛选';$('#discoveryExpand').setAttribute('aria-expanded',String(open));};
  async function refresh(more=false){if(dirty){$('#discoveryFeedback').textContent='先点击搜索确认筛选条件';return;}await A.runInline($('#discoveryQuote'),{...applied,page_start:more?snapshot.next_page:1},{onComplete:load});}
  $('#discoveryFetch').onclick=()=>refresh();$('#discoveryMore').onclick=()=>refresh(true);$('#discoverySchedule').onclick=()=>dirty?$('#discoveryFeedback').textContent='先点击搜索确认筛选条件':A.schedule(applied);
  $('#discoveryMonitor').onclick=()=>A.runInline($('#discoveryQuote'),{source:'monitor',aweme_ids:[...selected]},{onComplete:()=>{selected.clear();render();$('#discoveryFeedback').textContent='作品与作者已加入工作台监控。';}});
  $('#discoveryTable').addEventListener('change',e=>{if(e.target.dataset.select){e.target.checked?selected.add(e.target.dataset.select):selected.delete(e.target.dataset.select);updateSelection();}});
  async function related(word){persist();saved.works={applied:{source:'native',profile:'custom',keyword:word,days:7,limit:100,native_sort:1},draft:{source:'native',profile:'custom',keyword:word,days:7,limit:100,native_sort:1}};applied={};if(location.hash==='#works')await selectView();else location.hash='works';}
  document.addEventListener('click',async e=>{try{const b=e.target.closest('button');if(!b)return;if(b.dataset.hotTrend)A.hotTrend((snapshot?.items||[]).find(r=>r.id===b.dataset.hotTrend));if(b.dataset.related)await related(b.dataset.related);if(b.dataset.keywordWorks)await related(b.dataset.keywordWorks);if(b.dataset.keywordTrend)await A.open(watchData.find(w=>w.keyword===b.dataset.keywordTrend)?.last_query||{source:'keyword',keyword:b.dataset.keywordTrend},b.dataset.keywordTrend+' · 热度趋势');if(b.dataset.keywordRule)await A.schedule({source:'keyword',keyword:b.dataset.keywordRule});if(b.dataset.deleteKeyword){await api('/api/discovery/watches',{delete:b.dataset.deleteKeyword});await loadWatches();}}catch(e){$('#discoveryError').textContent=e.message;}});
  $('#addKeyword').onsubmit=async e=>{e.preventDefault();try{await api('/api/discovery/watches',{keyword:e.target.elements.keyword.value});e.target.reset();watchPage=0;await loadWatches();}catch(e){$('#discoveryError').textContent=e.message;}};
  window.addEventListener('hashchange',()=>selectView().catch(e=>$('#discoveryError').textContent=e.message));
  api('/api/discovery/catalog').then(async data=>{catalog=data;$('#discoveryProfile').innerHTML=data.profiles.map(p=>`<option value="${p.id}">${esc(p.title)}</option>`).join('');$('#discoveryCategory').innerHTML='<option value="">全部分类</option>'+data.categories.map(c=>`<option value="${c.value}">${esc(c.label)}</option>`).join('');await selectView();}).catch(e=>$('#discoveryError').textContent=e.message);
})();
