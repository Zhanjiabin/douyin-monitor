/* Shared, explicit query/refresh surfaces for public trends and portraits. */
(() => {
  const G=window.Guanchao,{esc,fmt,api}=G;
  const $=(root,selector)=>root.querySelector(selector);
  const quoteVersions=new WeakMap();
  function invalidateQuote(host){quoteVersions.set(host,(quoteVersions.get(host)||0)+1);host.hidden=true;}
  const labels={homepage_hot:'推荐页',familiar:'朋友页',search:'搜索',homepage:'个人主页',message:'消息',other:'其他',male:'男性',female:'女性',unknown:'未知',current_item:'当前作品',similar_author:'同类作者',play_source:'流量来源',analysis_trend:'观看分析',value:'数值',vv:'播放',play_count:'播放',publish_count:'发布数量',item_count:'作品数',hot_score:'热度',index:'指数',count:'数量',ratio:'占比',rate:'指标原值',portrait:'粉丝分布',portrait_data:'分布',portrait_tgi:'人群指数 TGI',supply_trend:'供给指标',consume_trend:'消费指标',rate_list:'变化趋势',author_list:'发布作者指标',item_list:'发布作品指标'};
  const label=s=>String(s).split('/').filter(Boolean).map(k=>labels[k]||k).join(' · ');
  const pct=(v,ratio)=>v==null?'—':ratio?(Number(v)*100).toFixed(1)+'%':Number(v)!==Math.floor(Number(v))?Number(v).toLocaleString('zh-CN',{maximumSignificantDigits:5}):fmt(v);
  function plot(points,metric,ratio){
    const rows=points.map(p=>({x:labels[p.x]||p.x,y:p.metrics[metric]})),valid=rows.filter(p=>Number.isFinite(p.y));
    if(!valid.length)return '<p class="audience-empty">该指标没有有效数据点</p>';
    const time=rows.every(p=>/^(?:\d{8}$|\d{4}[-/年]\d{2}|\d{2}:\d{2})/.test(p.x));
    if(!time){
      const max=Math.max(...valid.map(p=>p.y),1e-9),height=Math.min(valid.length,30)*28+12;
      return `<svg viewBox="0 0 640 ${height}" role="img" aria-label="${esc(label(metric))}分布">${valid.slice(0,30).map((p,i)=>`<text x="5" y="${i*28+19}" font-size="11" fill="#7d8c9a">${esc(p.x)}</text><rect x="130" y="${i*28+7}" width="${Math.max(1,p.y/max*400)}" height="14" rx="3" fill="#7aabc8"/><text x="${140+p.y/max*400}" y="${i*28+19}" font-size="11" fill="#3f596e">${esc(pct(p.y,ratio))}</text>`).join('')}</svg>`;
    }
    const max=Math.max(...valid.map(p=>p.y),ratio?1:1e-9),w=570,h=150,left=55,top=16;
    const path=rows.map((p,i)=>Number.isFinite(p.y)?`${i&&Number.isFinite(rows[i-1].y)?'L':'M'} ${left+i*w/Math.max(1,rows.length-1)} ${top+h-p.y/max*h}`:'').join(' ');
    return `<svg viewBox="0 0 660 210" role="img" aria-label="${esc(label(metric))}趋势">${[0,.5,1].map(t=>`<line x1="55" y1="${top+h-t*h}" x2="625" y2="${top+h-t*h}" stroke="#eaf0f5"/><text x="48" y="${top+h-t*h+4}" text-anchor="end" font-size="10" fill="#97a4b1">${esc(pct(max*t,ratio))}</text>`).join('')}<path d="${path}" fill="none" stroke="#397fa9" stroke-width="2.5" stroke-linejoin="round"/>${rows.length===1?`<circle cx="55" cy="${top+h-rows[0].y/max*h}" r="3" fill="#397fa9"/>`:''}<text x="55" y="190" font-size="10" fill="#97a4b1">${esc(rows[0].x)}</text><text x="625" y="190" text-anchor="end" font-size="10" fill="#97a4b1">${esc(rows.at(-1).x)}</text></svg>`;
  }
  function charts(container,snapshot,query){
    const series=snapshot?.series||[];
    container.innerHTML=series.map((s,i)=>{const metrics=[...new Set(s.points.flatMap(p=>Object.keys(p.metrics)))];return `<article class="series-card" data-series="${i}"><div class="series-card-head"><b>${esc(query.source==='keyword'?(query.keyword||'关键词')+' · 热度指数':label(s.name)||'趋势')}</b><select aria-label="查看指标">${metrics.map(m=>`<option value="${esc(m)}">${esc(label(m))}</option>`).join('')}</select></div><div class="series-chart"></div><details><summary>查看数据表</summary><div class="series-table"></div></details><div class="series-meta">${s.unit==='provider_rate'?'接口返回 rate 原值，单位未注明；不作为作品数量。 · ':''}${s.points.length} 个数据点 · ${esc(snapshot.created_at?.slice(0,19).replace('T',' ')||'')}</div></article>`;}).join('');
    series.forEach((s,i)=>{const card=container.querySelector(`[data-series="${i}"]`),select=card.querySelector('select'),ratio=s.unit==='ratio';const draw=()=>{const m=select.value;card.querySelector('.series-chart').innerHTML=plot(s.points,m,ratio);card.querySelector('.series-table').innerHTML=`<table><thead><tr><th>时间 / 分类</th><th>${esc(label(m))}</th></tr></thead><tbody>${s.points.map(p=>`<tr><td>${esc(labels[p.x]||p.x)}</td><td>${esc(pct(p.metrics[m],ratio))}</td></tr>`).join('')}</tbody></table>`;};select.onchange=draw;draw();});
    if(!series.length)container.innerHTML=`<div class="audience-empty">${snapshot?'接口未返回可绘制的数据，完整结果已保留。':'尚未抓取这组条件的数据。'}</div>`;
    if(snapshot?.raw?.length)container.insertAdjacentHTML('beforeend',`<details class="audience-raw"><summary>查看接口原始结果</summary><pre>${esc(JSON.stringify(snapshot.raw,null,2))}</pre></details>`);
    if(snapshot?.errors?.length)container.insertAdjacentHTML('afterbegin',`<p class="discovery-error">${esc(snapshot.errors.map(e=>e.error).join('；'))}</p>`);
  }
  async function runInline(host,query,options={}){
    const token=(quoteVersions.get(host)||0)+1;quoteVersions.set(host,token);
    const current=()=>host.isConnected&&quoteVersions.get(host)===token;
    host.hidden=false;host.classList.add('request-inline');host.innerHTML='<span>正在核对请求数…</span>';
    let plan,submitted=false;
    try{plan=await api('/api/discovery/plan',query);}catch(e){if(current())host.textContent=e.message;return;}
    if(!current())return;
    const native=plan.paid===false;
    host.innerHTML=`<div class="request-copy"><strong>${esc(plan.title)} · ${native?'最多 '+plan.limit+' 条内容':'最多 '+plan.calls+' 次请求'}</strong><small>${native?'MediaCrawlerPro 采集，不消耗 TikHub 额度':query.source==='keyword'?'关键词专用接口 · 本次仅一个词':'使用 TikHub 额度'}${plan.query.keyword?' · '+esc(plan.query.keyword):''}${plan.reason?' · '+esc(plan.reason):''}</small></div><button class="btn" data-cancel>取消</button><button class="btn primary" data-confirm ${plan.allowed?'':'disabled'}>确认抓取</button>${native?'':`<details class="request-path"><summary>接口信息</summary>${plan.endpoints.map(esc).join('<br>')}</details>`}<p class="error-text" role="alert"></p>`;
    host.querySelector('[data-cancel]').onclick=()=>{if(!submitted){invalidateQuote(host);options.onCancel?.();}};
    host.querySelector('[data-confirm]').onclick=async()=>{
      if(!current()||submitted||!plan.allowed)return;submitted=true;host.querySelector('[data-confirm]').disabled=true;
      try{
        const job=await api('/api/discovery/submit',{...query,fingerprint:plan.fingerprint,confirmed:true});
        if(!current())return;
        host.querySelector('[data-confirm]').hidden=true;host.querySelector('[data-cancel]').textContent='收起';host.querySelector('[data-cancel]').onclick=()=>host.hidden=true;
        const poll=async()=>{try{const j=await api('/api/douk/jobs/'+job.id);if(!current())return;host.querySelector('.request-copy strong').textContent=({queued:'排队中',running:'抓取中',completed:'抓取完成',partial:'部分完成',failed:'抓取失败',cancelled:'已取消',interrupted:'已中断'})[j.status]||j.status;host.querySelector('.request-copy small').textContent=j.message||'';
          if(['running','queued'].includes(j.status)){setTimeout(poll,1300);return;}
          if(j.status==='failed')host.querySelector('[role=alert]').textContent=j.message||'本次请求失败，未自动重试';
          if(['completed','partial'].includes(j.status)){await options.onComplete?.(j);window.dispatchEvent(new CustomEvent('gc-data-updated',{detail:{type:'discovery',query}}));}
        }catch(e){if(current())host.querySelector('[role=alert]').textContent=e.message+'；可在采集中心查看任务状态';}};poll();
      }catch(e){if(current()){submitted=false;host.querySelector('[role=alert]').textContent=e.message;host.querySelector('[data-confirm]').disabled=false;}}
    };
  }
  async function mount(host,initial){
    const portrait=initial.source==='portrait',dates=['keyword','industry'].includes(initial.source);
    const day=offset=>new Date(Date.now()+offset*86400000).toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'});
    let applied={...initial},draft={...initial},dirty=false,version=0;
    if(dates){draft.start_date=initial.start_date||day(-7);draft.end_date=initial.end_date||day(-1);applied={...draft};}
    host.classList.add('audience-module');
    host.innerHTML=`<div class="audience-controls">${portrait?'<select data-dimension aria-label="画像维度"><option value="2">性别分布</option><option value="3">年龄分布</option><option value="4">省份分布</option><option value="5">城市分布</option><option value="8">兴趣分布</option></select>':''}${dates?`<input type="date" data-start aria-label="开始日期" value="${esc(draft.start_date)}"><span>至</span><input type="date" data-end aria-label="结束日期" value="${esc(draft.end_date)}">`:''}<button class="btn" data-search>搜索</button><button class="btn primary" data-refresh>抓取最新</button><span class="muted" data-status></span></div><div data-quote hidden></div><div data-charts></div>`;
    const dimension=$(host,'[data-dimension]');if(dimension)dimension.value=String(draft.dimension||2);
    function invalidate(){dirty=true;invalidateQuote($(host,'[data-quote]'));$(host,'[data-status]').textContent='条件已修改，点击搜索后生效';}
    if(dimension)dimension.onchange=()=>{draft.dimension=Number(dimension.value);invalidate();};
    if(dates)for(const field of ['start','end'])$(host,'[data-'+field+']').onchange=e=>{draft[field+'_date']=e.target.value;invalidate();};
    async function load(){const mine=++version;$(host,'[data-status]').textContent='读取缓存…';try{const data=await api('/api/discovery/cache?query='+encodeURIComponent(JSON.stringify(applied)));if(version!==mine)return;charts($(host,'[data-charts]'),data.snapshot,applied);host.querySelector('[data-export]')?.remove();if(data.snapshot)$(host,'.audience-controls').insertAdjacentHTML('beforeend',`<a class="btn" data-export href="/api/discovery/export?query=${encodeURIComponent(JSON.stringify(applied))}">导出数据</a>`);$(host,'[data-status]').textContent=dirty?'条件已修改，点击搜索后生效':data.snapshot?'采集于 '+data.snapshot.created_at.slice(0,16).replace('T',' '):'暂无缓存';}catch(e){if(version===mine)$(host,'[data-status]').textContent=e.message;}}
    $(host,'[data-search]').onclick=()=>{invalidateQuote($(host,'[data-quote]'));applied={...draft};dirty=false;load();};
    $(host,'[data-refresh]').onclick=()=>{if(dirty){$(host,'[data-status]').textContent='先点击搜索确认条件';return;}runInline($(host,'[data-quote]'),applied,{onComplete:load});};
    await load();return {reload:load};
  }
  async function open(query,title){
    const d=document.getElementById('audienceDialog')||document.createElement('dialog');
    if(!d.id){d.id='audienceDialog';d.className='product-dialog audience-drawer';d.innerHTML='<header class="product-dialog-head"><h2></h2><button class="btn" data-close>关闭</button></header><div class="product-dialog-body"></div>';document.body.append(d);d.querySelector('[data-close]').onclick=()=>d.close();}
    d.querySelector('h2').textContent=title;G.openDialog(d);await mount(d.querySelector('.product-dialog-body'),query);
  }
  async function schedule(query){
    const d=document.getElementById('discoveryScheduleDialog')||document.createElement('dialog');
    if(!d.id){d.id='discoveryScheduleDialog';d.className='product-dialog action-dialog';d.innerHTML='<header class="product-dialog-head"><h2>发现定时更新</h2><button class="btn" data-close>关闭</button></header><div class="product-dialog-body"></div>';document.body.append(d);d.querySelector('[data-close]').onclick=()=>d.close();}
    const body=d.querySelector('.product-dialog-body');body.innerHTML='<div class="audience-empty">读取计划…</div>';G.openDialog(d);
    const native=query.source==='native'||query.source==='native_hot';
    async function draw(){const rules=await api('/api/discovery/rules');body.innerHTML=`<form class="discovery-rule-form"><label>计划名称<input name="title" value="${esc(query.keyword||'榜单更新')}" maxlength="80"></label><label>每隔多久更新<select name="interval_hours"><option value="24">24 小时</option><option value="12">12 小时</option><option value="6">6 小时</option><option value="1">1 小时</option></select></label><label class="check-label"><input name="enabled" type="checkbox">${native?'启用 Pro 定时采集，不消耗 TikHub 额度':'启用后按计划消耗 TikHub 额度'}</label><p class="audience-status">${native?'每轮最多 '+(query.limit||50)+' 条，按所选发布时间与排序采集。':'每次只抓一页，受现有单次和每日限额控制；关键词与行业趋势按最近七个完整日期更新。'}</p><div><button class="btn primary">保存计划</button></div><p role="status" class="audience-status"></p></form><div class="discovery-rules-list">${rules.items.map(r=>`<div><span>${esc(r.title)}<small>每 ${r.interval_hours} 小时 · 下次 ${esc(r.next_at.slice(0,16).replace('T',' '))}</small></span><button class="text-link" data-rule-toggle="${r.id}" data-enabled="${r.enabled?0:1}">${r.enabled?'暂停':'启用'}</button><button class="text-link" data-rule-delete="${r.id}">删除</button></div>`).join('')}</div>`;
      const form=body.querySelector('form');form.onsubmit=async e=>{e.preventDefault();const button=form.querySelector('button');button.disabled=true;try{await api('/api/discovery/rules',{query,title:form.elements.title.value,interval_hours:Number(form.elements.interval_hours.value),enabled:form.elements.enabled.checked});await draw();}catch(e){form.querySelector('[role=status]').textContent=e.message;button.disabled=false;}};
      async function change(button,data){button.disabled=true;try{await api('/api/discovery/rules',data);await draw();}catch(e){form.querySelector('[role=status]').textContent=e.message;button.disabled=false;}}
      body.querySelectorAll('[data-rule-toggle]').forEach(b=>b.onclick=()=>change(b,{id:b.dataset.ruleToggle,enabled:b.dataset.enabled==='1'}));body.querySelectorAll('[data-rule-delete]').forEach(b=>b.onclick=()=>change(b,{delete:b.dataset.ruleDelete}));
    }try{await draw();}catch(e){body.textContent=e.message;}
  }
  function comparison(host,data){
    if(!data){host.innerHTML='<div class="audience-empty">暂无可比样本。</div>';return;}
    host.classList.add('audience-module');host.innerHTML=`<div class="copy-toolbar"><b>同口径样本对比</b></div><p>${data.available?`本作品播放为可比作品中位数的 <b>${data.ratio} 倍</b>。`:esc(data.reason)}</p>${data.scope?`<p class="audience-status">${esc(data.scope)} · ${data.sample_count} 条可比作品</p>`:''}<div class="series-table" data-sample-table></div><div data-sample-pager></div><p class="audience-status">${esc(data.note||'')}</p>`;
    let page=0,size=20;const rows=data.samples||[];
    function render(){host.querySelector('[data-sample-table]').innerHTML=`<table><thead><tr><th>对比作品</th><th>发布时间</th><th>播放</th><th>采集日</th></tr></thead><tbody>${rows.slice(page*size,(page+1)*size).map(v=>`<tr><td><a target="_blank" rel="noreferrer" href="https://www.douyin.com/video/${v.aweme_id}">${esc(v.title)}</a></td><td>${G.date(v.create_time)}</td><td>${fmt(v.play_count)}</td><td>${esc(v.play_date||'')}</td></tr>`).join('')||'<tr><td colspan="4">没有同口径对比作品</td></tr>'}</tbody></table>`;G.pagination(host.querySelector('[data-sample-pager]'),{total:rows.length,page,pageSize:size,unit:'条样本',onChange:(p,s)=>{page=p;size=s;render();}});}
    render();
  }
  function hotTrend(row){
    if(!row)return;
    const d=document.getElementById('hotTrendDialog')||document.createElement('dialog');
    if(!d.id){d.id='hotTrendDialog';d.className='product-dialog audience-drawer';d.innerHTML='<header class="product-dialog-head"><h2></h2><button class="btn" data-close>关闭</button></header><div class="product-dialog-body"></div>';document.body.append(d);d.querySelector('[data-close]').onclick=()=>d.close();}
    d.querySelector('h2').textContent=row.title+' · 返回趋势';G.openDialog(d);
    const points=row.trend.map(p=>({x:String(p.date||p.datetime||p.key),metrics:{value:Number(p.value??p.hot_score)}})).filter(p=>Number.isFinite(p.metrics.value));
    charts(d.querySelector('.product-dialog-body'),{series:[{name:'热度趋势',points}],created_at:row.fetched_at},{source:'hot'});
  }
  window.GuanchaoAudience={charts,mount,open,runInline,invalidateQuote,schedule,comparison,hotTrend};
  async function settingsPanel(){
    const root=document.getElementById('settings-connection');if(!root)return;
    const cat=await api('/api/discovery/catalog'),panel=document.createElement('section');panel.className='panel audience-module';panel.style.padding='16px';
    panel.innerHTML=`<div class="copy-toolbar"><b>关键词趋势接口</b></div><form data-permission style="padding:12px 0"><label class="check-label"><input name="allow" type="checkbox" ${cat.settings.allow_keyword_trends?'checked':''}>允许关键词趋势专用接口（每次一个词）</label><p class="audience-status">此能力与播放统计分别计费，默认关闭。执行前显示请求数；不会开启批量视频统计。</p><button class="btn" type="submit">保存许可</button> <span data-permission-status class="audience-status"></span></form>`;
    root.append(panel);panel.querySelector('[data-permission]').onsubmit=async e=>{e.preventDefault();const status=panel.querySelector('[data-permission-status]');try{await api('/api/discovery/settings',{allow_keyword_trends:e.target.elements.allow.checked});status.textContent='已保存';}catch(e){status.textContent=e.message;}};
  }
  if(location.pathname==='/settings')settingsPanel().catch(()=>{});
})();
