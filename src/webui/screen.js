'use strict';
const $=s=>document.querySelector(s),$$=s=>Array.from(document.querySelectorAll(s));
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>n==null?'—':n>=1e8?(n/1e8).toFixed(2)+'亿':Math.abs(n)>=1e4?(n/1e4).toFixed(1)+'万':Number(n).toLocaleString('zh-CN',{maximumFractionDigits:1});
const icon=n=>`<svg class="icon"><use href="#i-${n}"/></svg>`;
const playLabels={available:'已取得',not_provided:'待查播放来源',checked_missing:'已查·未返回',unavailable:'接口未提供',failed:'补采失败',pending:'合集待补采'};
const sourceNames={tikhub_app_v3:'TikHub App V3',douk_detail:'DouK 详情',mcpro_python:'MediaCrawlerPro',mcpro_downloader:'多平台解析',tikhub_app:'TikHub · 自然播放',tikhub_app_batch:'TikHub · 批量统计',tikhub_xingtu:'TikHub · 含投流总播放',public_mix_metric:'合集',author_post:'账号作品',mix:'合集'};
const roleNames={own:'自有账号',peer:'同行账号',unclassified:'未分组'};
const colors=['#a394ce','#86aaa0','#a6aec9','#c6a78b','#c09cad','#8fa3be','#bbb9bf'];
let data={accounts:[],videos:[],features:[],topics:[],trend:[],stats:{}},pageName='overview',sort='published',expanded=false,pageIndex=0,pageSize=50,requestVersion=0,running=false,detailVersion=0,hasLoaded=false;
const filters={days:30,role:'all',account:'',topic:'',query:''};
let renderedFilters={...filters},trendView='chart',trendRows=[];
let filterOptionsReady=false;
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const value=await r.json();if(!r.ok||value.error)throw Error(value.error||'请求失败');return value}
function toast(message){$('#toast').textContent=message;$('#toast').classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').classList.remove('show'),3200)}
function empty(text='暂无数据'){return `<div class="empty">${text}</div>`}
function name(a){return a.nickname||a.note||'未命名账号'}
async function refresh(){const version=++requestVersion,query={...filters};try{const next=await api('/api/insights?'+new URLSearchParams(query));if(version!==requestVersion)return false;data=next;renderedFilters=query;hasLoaded=true;render();return true}catch(e){if(version===requestVersion)toast(e.message);return false}}
const published=n=>n?new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(n*1000)):'—';
const avatar=(path,label)=>path?`<img class="avatar" src="${esc(path)}" alt="" loading="lazy">`:`<span class="avatar placeholder">${esc((label||'未')[0])}</span>`;
function render(){renderStats();renderRank();renderTrend();renderFilters();}

function renderFilters(){const current=$('#accountFilter').value;$('#accountFilter').innerHTML='<option value="">全部账号</option>'+data.accounts.map(a=>`<option value="${esc(a.sec_uid)}">${esc(name(a))}</option>`).join('');$('#accountFilter').value=current;if(!filterOptionsReady){const ts=new Set(['逆袭复仇','都市情感','古装穿越','悬疑探案','家庭伦理','奇幻异能','未识别']);$('#topicFilter').innerHTML='<option value="">全部题材</option>'+[...ts].map(t=>`<option value="${esc(t)}">${esc(t)}</option>`).join('');filterOptionsReady=true;}}
function hasTrendValue(p){return Number.isFinite(p.own)||Number.isFinite(p.peer)}
const trendTableSort={key:'date',direction:'desc'};
const trendColumns=[{key:'date',label:'日期',type:'date'},{key:'own',label:'自有增长',type:'number'},{key:'own_n',label:'自有作品',type:'number'},{key:'peer',label:'同行账号均值',type:'number'},{key:'peer_accounts',label:'同行账号数',type:'number'},{key:'peer_n',label:'同行作品数',type:'number'}];
function renderTrendTable(){
  const head=$('#trendDailyRows').closest('table').tHead;head.innerHTML=Guanchao.sortHead(trendColumns,trendTableSort);
  Guanchao.bindSort(head,trendTableSort,renderTrendTable);
  $('#trendDailyRows').innerHTML=Guanchao.sortRows(trendRows,trendTableSort,trendColumns).map(p=>`<tr><td>${esc(p.date)}</td><td class="own">${Guanchao.metricNumber(p.own)}</td><td>${p.own_n||0}</td><td class="peer">${Guanchao.metricNumber(p.peer)}</td><td>${p.peer_accounts||0}</td><td>${p.peer_n||0}</td></tr>`).join('');
}

function renderTrend(){
  const dialog=$('#trendDialog');if(!dialog.open)return;
  const points=data.trend||[];trendRows=points.filter(hasTrendValue);
  const canPlot=trendRows.length>=2,view=canPlot?trendView:'daily';
  const scope=[data.start&&data.end?data.start+' 至 '+data.end:'',roleNames[renderedFilters.role]||'全部账号'];
  if(renderedFilters.account){const a=data.accounts.find(a=>a.sec_uid===renderedFilters.account);scope.push(a?name(a):renderedFilters.account)}
  if(renderedFilters.topic)scope.push(renderedFilters.topic);
  if(renderedFilters.query)scope.push('关键词：'+renderedFilters.query);
  $('#trendScope').textContent=scope.filter(Boolean).join(' · ');$('#trendScope').title=$('#trendScope').textContent;
  $('#trendChartTab').disabled=!canPlot;$('#trendDailyTab').disabled=!trendRows.length;$('#exportTrend').disabled=!trendRows.length;
  $$('[data-trend-view]').forEach(b=>{const on=b.dataset.trendView===view;b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1});
  $('#trendChartView').hidden=!canPlot||view!=='chart';$('#trendDailyView').hidden=!trendRows.length||view!=='daily';
  $('#trendEmpty').hidden=!!trendRows.length;$('#trendAvailability').hidden=trendRows.length!==1;
  $('#trendAvailability').textContent=trendRows.length===1?'目前仅 '+trendRows[0].date+' 有可比增长，先展示这一天的明细。':'';
  renderTrendTable();
  if(!canPlot||view!=='chart'){$('#trendWrap').innerHTML='';return}
  const first=points.findIndex(hasTrendValue),last=points.reduce((last,p,i)=>hasTrendValue(p)?i:last,first),visible=points.slice(first,last+1);
  const wrap=$('#trendWrap'),w=Math.max(wrap.clientWidth,280),h=wrap.clientHeight||240,L=w<450?44:58,R=23,T=16,B=32;
  const raw=Math.max(...visible.flatMap(p=>[p.own||0,p.peer||0]),1),power=10**Math.floor(Math.log10(raw));
  const max=[1,2,2.5,5,10].find(n=>n*power>=raw)*power;
  const x=i=>L+i*(w-L-R)/Math.max(1,visible.length-1),y=v=>h-B-v/max*(h-T-B);
  const tick=Math.max(1,Math.ceil(visible.length/(w<450?3:6)));
  let html='<title>每日可比作品播放增量</title>';
  for(let i=0;i<=4;i++){const Y=y(max*i/4);html+=`<line class="trend-grid" x1="${L}" x2="${w-R}" y1="${Y}" y2="${Y}"/><text x="${L-10}" y="${Y+3}" text-anchor="end">${fmt(max*i/4)}</text>`}
  visible.forEach((p,i)=>{if(i===0||i===visible.length-1||(i%tick===0&&i<visible.length-tick/2))html+=`<text x="${x(i)}" y="${h-8}" text-anchor="middle">${esc(p.date.slice(5).replace('-','/'))}</text>`});
  for(const [key,color] of [['own','#2b7fa7'],['peer','#269e86']]){
    let path='',connected=false;
    visible.forEach((p,i)=>{if(!Number.isFinite(p[key])){connected=false;return}path+=`${connected?'L':'M'}${x(i)},${y(p[key])} `;connected=true});
    html+=`<path data-trend-series="${key}" d="${path}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round"/>`;
    visible.forEach((p,i)=>{if(Number.isFinite(p[key]))html+=`<circle cx="${x(i)}" cy="${y(p[key])}" r="3.2" fill="${color}" stroke="#fff" stroke-width="1.4"/>`});
  }
  html+=`<line id="trendMarker" class="trend-marker" x1="${x(visible.length-1)}" x2="${x(visible.length-1)}" y1="${T}" y2="${h-B}"/>`;
  const step=(w-L-R)/Math.max(1,visible.length-1);
  visible.forEach((p,i)=>{html+=`<rect data-trend-point="${i}" tabindex="0" role="button" aria-label="${esc(p.date)}：自有增长 ${fmt(p.own)}，同行账号均值 ${fmt(p.peer)}" x="${Math.max(L,x(i)-step/2)}" y="${T}" width="${i===0||i===visible.length-1?step/2:step}" height="${h-T-B}" fill="transparent"/>`});
  wrap.innerHTML=`<svg id="trendChart" viewBox="0 0 ${w} ${h}" role="img" aria-label="每日可比作品播放增量">${html}</svg>`;
  const show=i=>{const p=visible[i],line=$('#trendMarker');line.setAttribute('x1',x(i));line.setAttribute('x2',x(i));$('#trendPointDetail').innerHTML=`<b>${esc(p.date)}</b><span>自有增长 <b>${fmt(p.own)}</b><small>${p.own_n||0} 条作品</small></span><span>同行账号均值 <b>${fmt(p.peer)}</b><small>${p.peer_accounts||0} 个账号 · ${p.peer_n||0} 条</small></span>`};
  show(visible.length-1);
  wrap.onpointermove=trendPointEvent;wrap.onpointerdown=trendPointEvent;wrap.querySelectorAll('[data-trend-point]').forEach(point=>point.onfocus=trendPointEvent);
  function trendPointEvent(e){const point=e.target.closest('[data-trend-point]');if(point)show(Number(point.dataset.trendPoint))}
}
function exportTrend(){
  const rows=[['日期','自有总增长','自有作品数','同行账号平均增长','同行账号数','同行作品数'],...(trendView==='daily'?Guanchao.sortRows(trendRows,trendTableSort,trendColumns):trendRows).map(p=>[p.date,p.own,p.own_n,p.peer,p.peer_accounts,p.peer_n])];
  const csv=rows.map(row=>row.map(v=>'"'+String(v??'').replace(/"/g,'""')+'"').join(',')).join('\r\n');
  const url=URL.createObjectURL(new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');
  a.href=url;a.download=`播放增长-${data.start}-${data.end}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);
}
const rankSort={key:'create_time',direction:'desc'};
const rankColumns=[{key:'title',label:'作品 / 合集',className:'work-col'},{key:'account_name',label:'账号'},{key:'create_time',label:'发布时间',type:'date',get:r=>r.create_time||null},{key:'metrics_updated_at',label:'数据更新',type:'date'},...['play_count','digg_count','comment_count','collect_count','share_count'].map((key,i)=>({key,label:['播放量','点赞','评论','收藏','分享'][i],type:'number'})),{key:'daily_growth',label:'日均增长',type:'number',get:r=>r.stale?null:r.daily_growth},{key:'topic',label:'题材'}];
function ranked(){return Guanchao.sortRows(data.videos,rankSort,rankColumns);}
function rankHeaders(){
  const head=$('#rankBody').closest('table').tHead,checkbox=head.querySelector('.selection-col');
  head.innerHTML=Guanchao.sortHead(rankColumns,rankSort);if(checkbox)head.querySelector('tr').prepend(checkbox);
  Guanchao.bindSort(head,rankSort,()=>{sort='header';$('#sortFilter').value='header';pageIndex=0;renderRank();$('#rankBody').closest('.table-scroll').scrollTop=0;});
}

function renderStats(){$('#scanNotice').textContent='更新于 '+(data.generated_at||'').slice(0,16).replace('T',' ');}

function renderRank(){if(!hasLoaded){$('#rankBody').innerHTML='<tr><td colspan="12">'+empty('正在读取监控作品…')+'</td></tr>';return;}rankHeaders();const all=ranked();pageIndex=Math.min(pageIndex,Math.max(0,Math.ceil(all.length/pageSize)-1));const rows=all.slice(pageIndex*pageSize,(pageIndex+1)*pageSize);$('#rankBody').innerHTML=rows.map(v=>`<tr><td class="work-cell"><div class="work"><a href="https://www.douyin.com/video/${encodeURIComponent(v.aweme_id)}" target="_blank" rel="noopener noreferrer" aria-label="打开抖音作品">${v.cover_path?`<img class="cover" src="${esc(v.cover_path)}" loading="lazy" alt="">`:'<span class="cover cover-placeholder">▶</span>'}</a><div><a class="table-title" href="https://www.douyin.com/video/${encodeURIComponent(v.aweme_id)}" target="_blank" rel="noopener noreferrer" title="打开抖音作品">${esc(v.title||'无标题')} ↗</a><div class="work-meta">${v.mix_name?`<small class="muted">${esc(v.mix_name)}</small>`:''}<a class="text-link" href="/research?video=${encodeURIComponent(v.aweme_id)}">内容研究</a><button class="text-link" data-video="${esc(v.aweme_id)}">数据详情</button><button class="text-link" data-inline-action="playback" data-aid="${esc(v.aweme_id)}">更新数据</button></div></div></div></td><td><a class="author-cell" href="https://www.douyin.com/user/${encodeURIComponent(v.sec_uid)}" target="_blank" rel="noopener noreferrer" title="打开抖音主页">${avatar(v.avatar_path,v.account_name)}<span>${esc(v.account_name)} ↗</span></a><button class="text-link account-filter-link" data-account="${esc(v.sec_uid)}">账号详情</button></td><td class="date-cell">${esc(published(v.create_time)).replace(' ','<br>')}</td><td class="date-cell metric-updated-cell" title="上次数据更新">${esc(Guanchao.timestamp(v.metrics_updated_at)).replace(' ','<br>')}</td><td class="num" title="${esc(v.play_reason||(v.play_date?'播放采集于 '+v.play_date:'当前数据源未提供可验证的播放量'))}">${Guanchao.metricNumber(v.play_count)}<small class="muted">${v.stale?'快照过期':v.play_count==null?(playLabels[v.play_status]||'未取得'):esc(v.play_date||'')}</small></td><td class="num">${Guanchao.metricNumber(v.digg_count)}</td><td class="num">${Guanchao.metricNumber(v.comment_count)}</td><td class="num">${Guanchao.metricNumber(v.collect_count)}</td><td class="num">${Guanchao.metricNumber(v.share_count)}</td><td class="num green">${Guanchao.metricNumber(v.stale?null:v.daily_growth)}</td><td><span class="tag">${esc(v.topic)}</span></td></tr>`).join('')||`<tr><td colspan="11">${empty(data.accounts.length?'尚无作品快照，请前往采集中心同步账号':'先在监控账号中添加账号')}</td></tr>`;Guanchao.pagination($('#rankPagination'),{total:all.length,page:pageIndex,pageSize,unit:'条作品',totalText:`${all.length} 条作品`,ids:{total:'rankCaption',position:'pageNumber',size:'pageSize',prev:'prevPage',next:'showMore',jump:'rankPageJump',go:'rankPageGo'},onChange:(page,size)=>{pageIndex=page;pageSize=size;renderRank();$('#rankBody').closest('.table-scroll').scrollTop=0;}});}

function go(next,push=true){if(next==='accounts'){location.replace('/home');return;}pageName='overview';$$('.page').forEach(p=>p.classList.toggle('active',p.id==='page-'+pageName));$('#pageTitle').textContent='作品监控';document.title='观潮 · '+$('#pageTitle').textContent;if(location.hash!=='#'+pageName){history[push&&next==='overview'?'pushState':'replaceState'](null,'','#'+pageName);}window.dispatchEvent(new Event('gc-route-change'));renderTrend();}
function exportCSV(){let rows=[['标题','账号','发布时间','上次数据更新时间','题材','播放量','点赞','评论','收藏','分享','播放采集日','日均增长','间隔天数','过期'],...ranked().map(v=>[v.title,v.account_name,published(v.create_time),Guanchao.timestamp(v.metrics_updated_at),v.topic,v.play_count,v.digg_count,v.comment_count,v.collect_count,v.share_count,v.play_date,v.stale?null:v.daily_growth,v.span_days,v.stale?'是':'否'])];const text=rows.map(row=>row.map(v=>{let s=String(v??'');if(/^[=+\-@\t\r]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"'}).join(',')).join('\r\n'),url=URL.createObjectURL(new Blob(['\ufeff'+text],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download=`观潮-${pageName}-${data.end}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000)}
document.addEventListener('click',async e=>{const b=e.target.closest('[data-page],[data-account],[data-video],[data-close]');if(!b)return;if(b.dataset.page){e.preventDefault();go(b.dataset.page);return}if(b.dataset.close){document.getElementById(b.dataset.close).close();detailVersion++;return}if(b.dataset.account){Guanchao.openAccount(b.dataset.account);return}if(b.dataset.video){Guanchao.openWork(b.dataset.video);return}});
$('#screenFilterForm').onsubmit=async e=>{e.preventDefault();filters.days=Number($('#period').value);filters.role=$('#roleFilter').value;filters.account=$('#accountFilter').value;filters.topic=$('#topicFilter').value;filters.query=$('#searchInput').value.trim();sort=$('#sortFilter').value;if(sort!=='header')Object.assign(rankSort,{key:({published:'create_time',likes:'digg_count',growth:'daily_growth',play:'play_count'})[sort],direction:'desc'});pageIndex=0;$('#screenFilterState').textContent='正在搜索…';const ok=await refresh();$('#screenFilterState').textContent=ok?'已应用所选条件':'搜索失败，请重试';};
$('#screenFilterForm').onreset=e=>{e.preventDefault();$('#period').value='30';$('#roleFilter').value='all';$('#accountFilter').value='';$('#topicFilter').value='';$('#searchInput').value='';$('#sortFilter').value='published';$('#screenFilterForm').requestSubmit();};
$('#screenFilterForm').addEventListener('input',()=>$('#screenFilterState').textContent='条件已修改，点击搜索后生效');
$('#openTrend').onclick=()=>{Guanchao.openDialog($('#trendDialog'));renderTrend()};
$('#closeTrend').onclick=()=>$('#trendDialog').close();$('#exportTrend').onclick=exportTrend;
$$('[data-trend-view]').forEach(b=>b.onclick=()=>{trendView=b.dataset.trendView;renderTrend()});
$('.screen-trend-tabs').onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;const tabs=$$('[data-trend-view]').filter(b=>!b.disabled),i=tabs.indexOf(document.activeElement);if(i<0)return;e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].focus();tabs[next].click()};
$('#exportButton').onclick=exportCSV;$('#reloadSnapshot').onclick=()=>refresh();['methodButton','footerMethod'].forEach(id=>document.getElementById(id).onclick=()=>$('#methodDialog').showModal());
window.addEventListener('gc-data-updated',()=>refresh());
$('#fullscreenButton').onclick=async()=>{const next=!document.body.classList.contains('presentation');document.body.classList.toggle('presentation',next);$('#screenLabel').textContent=next?'退出大屏':'大屏';try{if(next)await document.documentElement.requestFullscreen();else if(document.fullscreenElement)await document.exitFullscreen()}catch{}renderTrend()};document.addEventListener('fullscreenchange',()=>{if(!document.fullscreenElement){document.body.classList.remove('presentation');$('#screenLabel').textContent='大屏';renderTrend()}});document.addEventListener('keydown',e=>{if(e.key==='Escape'){$('.sidebar').classList.remove('mobile-open');if(!document.fullscreenElement){document.body.classList.remove('presentation');$('#screenLabel').textContent='大屏'}}});window.addEventListener('resize',renderTrend);window.addEventListener('hashchange',()=>go(location.hash.slice(1),false));window.addEventListener('popstate',()=>go(location.hash.slice(1),false));go(location.hash.slice(1)||'overview',false);refresh();setInterval(()=>{if(!document.hidden&&!document.querySelector('dialog[open]'))refresh()},15000);
