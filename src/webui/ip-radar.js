/* AI discovery UI: saved candidates, bounded collection, visible progress. */
(() => {
  'use strict';
  const G = Guanchao, {esc, fmt, api} = G, $ = s => document.querySelector(s);
  const root = '/api/discovery/radar/';
  const time = v => G.timestamp(v, true), fullTime = v => G.timestamp(v);
  const stateName = {rising:'已观测增长',baseline:'正在积累基线',candidate:'待补采候选',classic:'旧作参考'};
  const laneName = {comic:'AI 漫剧',drama:'AI 短剧',pet:'AI 萌宠',cinema:'AI 大片',other:'AI 创作'};
  let page = 0, size = 12, applied = {}, serial = 0, jobId = '', jobTimer, radarGroups = [];
  const picture = w => w.cover_path || w.cover_url
    ? `<img src="${esc(w.cover_path || w.cover_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
    : '<span class="radar-cover-empty">影</span>';
  const account = w => window.HubDiscovery?.accountButton(w) || '';
  const fail = error => { $('#hubError').textContent = error.message; };
  function dialog(id, title, className) {
    let el = document.getElementById(id);
    if (!el) {
      el = document.createElement('dialog'); el.id = id;
      el.className = 'product-dialog '+className;
      el.innerHTML = `<header class="product-dialog-head"><h2>${esc(title)}</h2><button class="btn" data-radar-close>关闭</button></header><div class="product-dialog-body"></div>`;
      document.body.append(el);
    }
    G.openDialog(el); return el;
  }
  function byCard(g) {
    const w = g.representative;
    return `<article class="radar-card" data-radar-id="${esc(g.id)}">
      <div class="radar-card-head"><div><button class="radar-name" data-radar-group="${esc(g.id)}">${esc(g.name)}</button><span class="hub-tag ${g.state==='rising'?'green':''}">${stateName[g.state]}</span></div><span class="radar-kind">${esc(g.kind_label)}</span></div>
      <div class="radar-author">${esc(g.account_name || '待核实作者')}<span class="hub-caption">${g.identity_verified?'作者已核实':'作者待核实'}</span>${account(w)}</div>
      <div class="radar-reason"><span>${g.lanes.map(l=>laneName[l] || l).join(' / ')}</span><span>${esc(g.evidence)}</span>${g.peak_daily_growth!=null?`<b>日均增播 +${fmt(g.peak_daily_growth)}</b>`:''}</div>
      <div class="radar-samples">${g.latest.map(v=>`<button data-hub-detail="${esc(v.id)}" data-kind="works">${picture(v)}<span>${esc(v.title)}</span><small>${time(v.published_at)}</small><b>${v.play_count==null?fmt(v.digg_count)+' 赞':fmt(v.play_count)+' 播放'}</b></button>`).join('')}</div>
      <footer><span>近期 ${g.recent_works} 作 · 已有播放 ${g.plays_available}/${g.works}</span><button class="text-link" data-radar-group="${esc(g.id)}">全部作品</button><button class="text-link" data-radar-enrich="${esc(w.id)}">补采代表作</button></footer>
    </article>`;
  }
  async function load() {
    const token = ++serial;
    try {
      const result = await api(root+'library?query='+encodeURIComponent(JSON.stringify({...applied,page:page+1,page_size:size})));
      if (token !== serial) return;
      radarGroups = result.items; page = result.page-1;
      $('#radarCards').innerHTML = result.items.map(byCard).join('') || '<div class="hub-empty">没有符合条件的候选。可以调整条件，或点击“发现与追踪”采集。</div>';
      G.pagination($('#radarPager'), {total:result.total,page,pageSize:size,sizes:[12,24,48],unit:'个候选',onChange:(p,s)=>{page=p;size=s;load();$('.radar-grid-scroll').scrollTop=0;}});
    } catch(e) { fail(e); }
  }
  const filters = $('#radarFilters');
  filters.onsubmit = e => {e.preventDefault();applied=Object.fromEntries(new FormData(filters));page=0;load();};
  filters.onreset = e => {e.preventDefault();filters.elements.keyword.value='';filters.elements.focus.value='ai';filters.elements.state.value='recent';applied={};page=0;load();};
  async function groupDetail(id) {
    const el = dialog('radarGroup','IP 作品','radar-group-dialog');
    const host = el.querySelector('.product-dialog-body');
    host.innerHTML = '<p class="hub-caption">读取已保存作品…</p>';
    try {
      const result = await api(root+'group?id='+encodeURIComponent(id)), g = result.group;
      host.innerHTML = `<div class="radar-group-heading"><div><h3>${esc(g.name)}</h3><p>${esc(g.account_name)} · ${esc(g.kind_label)} · ${g.identity_verified?'作者已核实':'作者待核实'}</p></div><div class="hub-actions">${account(g.representative)}<button class="btn" data-radar-pin="${esc(g.representative.id)}" data-radar-pinned="${g.representative.radar_pinned?1:0}">${g.representative.radar_pinned?'取消重点追踪':'重点追踪代表作'}</button></div></div>
        <div class="radar-group-table table-scroll"><table><thead><tr><th>作品 / 发布</th><th class="hub-number">播放</th><th class="hub-number">点赞</th><th class="hub-number">评论</th><th class="hub-number">收藏</th><th class="hub-number">分享</th><th>更新</th><th>操作</th></tr></thead><tbody>${result.items.sort((a,b)=>String(b.published_at).localeCompare(String(a.published_at))).map(w=>`<tr><td><div class="hub-work">${picture(w)}<div><button class="hub-work-title" data-hub-detail="${esc(w.id)}" data-kind="works">${esc(w.title)}</button><small class="hub-caption">${time(w.published_at)}</small></div></div></td><td class="hub-number hub-play">${fmt(w.play_count)}</td><td class="hub-number">${fmt(w.digg_count)}</td><td class="hub-number">${fmt(w.comment_count)}</td><td class="hub-number">${fmt(w.collect_count)}</td><td class="hub-number">${fmt(w.share_count)}</td><td class="hub-time">${time(w.metrics_updated_at)}</td><td><div class="hub-actions"><button class="text-link" data-radar-enrich="${esc(w.id)}">更新数据</button><button class="text-link" data-inline-action="download" data-aid="${esc(w.id)}">下载</button><a class="text-link" href="${esc(w.url)}" target="_blank" rel="noreferrer">原作 ↗</a></div></td></tr>`).join('')}</tbody></table></div>`;
    } catch(e) {host.innerHTML=`<p class="hub-error">${esc(e.message)}</p>`;}
  }
  async function plan(action, ids=[]) {
    const el = dialog('radarTask','采集任务','radar-task-dialog'), host = el.querySelector('.product-dialog-body');
    host.innerHTML = '<p class="hub-caption">正在预览请求…</p>';
    try {
      const quote = await api(root+'plan', {action,ids});
      const names={search:'关键词搜索',detail:'作品详情',statistics:'单作品播放'};
      host.innerHTML = `<div class="radar-task-quote"><b>${quote.calls} 次独立请求</b><span>今日 AI 监测已用 ${quote.today_calls} / ${quote.daily_calls}</span></div>
        <div class="radar-task-list">${quote.tasks.map(t=>`<div><span class="hub-tag">${names[t.kind]}</span><span>${esc(t.title || t.target)}</span></div>`).join('')}</div>
        ${quote.reason?`<p class="hub-error">${esc(quote.reason)}</p>`:''}<div data-radar-progress></div><p class="hub-error" data-radar-task-error></p>
        <div class="product-dialog-actions"><button class="btn" data-radar-stop hidden>停止后续请求</button><button class="btn primary" data-radar-confirm ${quote.allowed?'':'disabled'}>确认开始</button></div>`;
      host.querySelector('[data-radar-confirm]').onclick = async e => {
        e.currentTarget.disabled=true;
        try {
          const job=await api(root+'submit',{action,ids,confirmed:true,fingerprint:quote.fingerprint});
          jobId=job.id;sessionStorage.setItem('nn-ip-radar-job',jobId);
          host.querySelector('[data-radar-confirm]').hidden=true;
          host.querySelector('[data-radar-stop]').hidden=false;
          host.querySelector('[data-radar-stop]').onclick=async e=>{e.currentTarget.disabled=true;try{await api('/api/douk/cancel',{id:jobId});}catch(e){host.querySelector('[data-radar-task-error]').textContent=e.message;}};
          poll(job.id,host);
        } catch(e) {host.querySelector('[data-radar-task-error]').textContent=e.message;host.querySelector('[data-radar-confirm]').disabled=false;}
      };
    } catch(e) {host.innerHTML=`<p class="hub-error">${esc(e.message)}</p>`;}
  }
  async function poll(id, host) {
    clearTimeout(jobTimer);
    try {
      const job=await api('/api/douk/jobs/'+id), p=job.progress || {}, active=['queued','running'].includes(job.status);
      const total=p.total || job.result?.attempted || 1, done=p.done || 0;
      const phase={search:'发现新作品',detail:'核实作者与作品详情',statistics:'获取单作品播放',saved:'已保存响应'}[p.phase] || '排队中';
      if(host?.isConnected) {
        host.querySelector('[data-radar-progress]').innerHTML=`<div class="radar-live-progress" role="status"><b>${active?phase:job.status==='completed'?'已完成':job.status==='cancelled'?'已停止':'部分请求未完成'}<span>${done} / ${total}</span></b><progress max="${total}" value="${done}" aria-label="AI 采集进度"></progress><small>${esc(active?p.target || '':job.message || '')}</small></div>`;
        host.querySelector('[data-radar-stop]').hidden=!active;
        if(!active && job.result?.errors?.length)host.querySelector('[data-radar-task-error]').textContent=job.result.errors.map(e=>e.error).join('；');
      }
      $('#hubLastUpdated').textContent=active?`${phase} ${done}/${total}`:'本轮采集已结束';
      if(active)jobTimer=setTimeout(()=>poll(id,host),1500);
      else {
        sessionStorage.removeItem('nn-ip-radar-job'); jobId='';
        window.dispatchEvent(new CustomEvent('gc-data-updated',{detail:{type:'radar'}}));
        if(location.hash==='#references')await load();
      }
    } catch(e) {fail(e);}
  }
  async function settings() {
    const el=dialog('radarSettings','AI 监测计划','radar-settings-dialog'),host=el.querySelector('.product-dialog-body');
    host.innerHTML='<p class="hub-caption">读取监测计划…</p>';
    try {
      const status=await api(root+'status'), cfg=status.settings;
      const number=(name,label)=>`<label>${label}<input name="${name}" type="number" min="1" value="${cfg[name]}" required></label>`;
      host.innerHTML=`<div class="radar-settings-layout"><form id="radarSettingsForm">
        <div class="radar-setting-switch"><label><input type="checkbox" name="enabled" ${cfg.enabled?'checked':''}>自动发现与追踪</label><span>${status.next_at?'下次 '+fullTime(status.next_at):'当前已暂停'}</span></div>
        <label>持续搜索词<textarea name="keywords" rows="5" required>${esc(cfg.keywords.join('\n'))}</textarea></label>
        <div class="radar-settings-fields">${number('interval_hours','巡检间隔（小时）')}${number('recent_days','重点关注近几天的作品')}${number('refresh_hours','同一作品刷新间隔（小时）')}${number('watch_limit','重点作品数量上限')}${number('cycle_calls','每轮最多请求')}${number('daily_calls','AI 监测每日最多请求')}</div>
        <label class="radar-checkbox"><input name="expand_keywords" type="checkbox" ${cfg.expand_keywords?'checked':''}>从重复剧名扩充搜索词</label>
        <div class="radar-budget-line">每轮先搜索一个词，再补采到期作品。播放逐条调用，所有响应保存。今日已用 ${status.today_calls}/${cfg.daily_calls} 次；全站已用 ${status.global_today_calls} 次。</div>
        <div class="radar-save-row"><button class="btn primary" type="submit">保存计划</button><button class="btn" type="button" data-radar-run>立即巡检</button><span id="radarSettingsFeedback" role="status"></span></div>
      </form><section class="radar-plan-preview"><h3>当前重点作品</h3><div class="radar-target-list">${status.targets.map(w=>`<div><button class="hub-work-title" data-hub-detail="${esc(w.id)}" data-kind="works">${esc(w.title)}</button><small>${esc(w.account_name)} · ${time(w.published_at)} 发布</small><span>${w.play_count==null?'待建立播放基线':fmt(w.play_count)+' 播放'} · ${time(w.metrics_updated_at)} 更新</span></div>`).join('') || '<p class="hub-caption">近期作品采集后按方向选取。</p>'}</div>
        <details><summary>扩展中的搜索词</summary>${status.keywords.filter(k=>k.origin!=='方向词').map(k=>`<div class="radar-keyword">${esc(k.keyword)}</div>`).join('') || '<p class="hub-caption">积累同一剧名的多条作品后自动扩展。</p>'}</details></section></div>
        <details class="radar-execution-log"><summary>最近请求记录</summary><div class="table-scroll"><table><thead><tr><th>时间</th><th>操作</th><th>目标</th><th>结果</th></tr></thead><tbody>${status.attempts.map(a=>`<tr><td>${time(a.created_at)}</td><td>${{search:'搜索',detail:'详情',statistics:'单作品播放'}[a.kind]}</td><td>${esc(a.target)}</td><td>${a.status==='completed'?'已保存':a.status==='started'?'请求中':esc(a.error)}</td></tr>`).join('')}</tbody></table></div></details>`;
      $('#radarSettingsForm').onsubmit=async e=>{
        e.preventDefault();const f=e.target,b=f.querySelector('[type=submit]');b.disabled=true;
        try {
          const body={enabled:f.elements.enabled.checked,expand_keywords:f.elements.expand_keywords.checked,
            keywords:f.elements.keywords.value.split(/[\n，,]/).map(v=>v.trim()).filter(Boolean),confirmed:true};
          for(const name of ['interval_hours','recent_days','refresh_hours','watch_limit','cycle_calls','daily_calls'])body[name]=Number(f.elements[name].value);
          await api(root+'settings',body);$('#radarSettingsFeedback').textContent='已保存';
        }catch(error){$('#radarSettingsFeedback').textContent=error.message;}finally{b.disabled=false;}
      };
    }catch(e){host.innerHTML=`<p class="hub-error">${esc(e.message)}</p>`;}
  }
  function report(r) {
    const data=r.radar;
    const table=(title,gs)=>`<section class="hub-report-section"><h3>${title}</h3><div class="table-scroll"><table class="radar-report-table"><thead><tr><th>IP / 作者</th><th>证据</th><th class="hub-number">近期作品</th><th class="hub-number">代表作播放</th><th class="hub-number">点赞</th><th>最新发布</th><th>操作</th></tr></thead><tbody>${gs.map(g=>`<tr><td><b>${esc(g.name)}</b><small>${esc(g.account_name)}</small></td><td>${esc(g.evidence)}<small>${stateName[g.state]}</small></td><td class="hub-number">${g.recent_works}</td><td class="hub-number hub-play">${fmt(g.representative.play_count)}</td><td class="hub-number">${fmt(g.representative.digg_count)}</td><td>${time(g.latest_published_at)}</td><td><div class="hub-actions"><button class="text-link" data-hub-detail="${esc(g.representative.id)}" data-kind="works">代表作</button>${account(g.representative)}<a class="text-link" href="${esc(g.representative.url)}" target="_blank" rel="noreferrer">原作 ↗</a></div></td></tr>`).join('') || '<tr><td colspan="7">本期暂无样本</td></tr>'}</tbody></table></div></section>`;
    return `<article class="hub-report-document"><header class="hub-report-heading"><h2>${esc(r.title)}</h2><span>${fullTime(r.start_at)} — ${fullTime(r.end_at)}</span></header>
      ${data.rising.length?table('实际观测到增长的 IP',data.rising):'<p class="radar-report-pending">本期还没有间隔足够的可比播放样本，正在建立基线。</p>'}
      ${table('新收录的创作线索',data.new_ips)}${table('近期值得看的 IP',data.candidates.filter(g=>g.recent_works && g.kind!=='commentary').slice(0,12))}
      <section class="hub-report-section"><h3>本期搜索记录</h3><div class="radar-search-log">${data.searches.map(s=>`<span>${esc(s.target)}<small>${time(s.created_at)} · ${s.status==='completed'?'已保存':'未完成'}</small></span>`).join('') || '<p class="hub-caption">本期没有自动搜索记录。</p>'}</div></section></article>`;
  }
  document.addEventListener('click', async e=>{
    const b=e.target.closest('button');if(!b)return;
    if(b.hasAttribute('data-radar-close'))b.closest('dialog').close();
    if(b.hasAttribute('data-radar-run'))plan('cycle');
    if(b.dataset.radarEnrich)plan('enrich',[b.dataset.radarEnrich]);
    if(b.dataset.radarGroup)groupDetail(b.dataset.radarGroup);
    if(b.dataset.radarPin){try{const enabled=b.dataset.radarPinned!=='1';await api(root+'pin',{id:b.dataset.radarPin,enabled});b.dataset.radarPinned=enabled?'1':'0';b.textContent=enabled?'取消重点追踪':'重点追踪代表作';}catch(e){fail(e);}}
  });
  window.addEventListener('gc-data-updated',()=>{if(location.hash==='#references')load();});
  window.IpRadar={load,settings,enrich:ids=>plan('enrich',ids),report};
  const previous=sessionStorage.getItem('nn-ip-radar-job');if(previous)poll(previous,null);
})();
