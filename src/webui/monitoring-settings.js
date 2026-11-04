(() => {
  'use strict';
  if(location.pathname!=='/settings')return;
  const $=s=>document.querySelector(s);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let current=null,dirty=false,version=0;
  async function api(path,body){const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const v=await r.json();if(!r.ok||v.error)throw Error(v.error||'读取失败');return v;}
  function values(){return {enabled:$('#monitorEnabled').checked,account_ids:[...document.querySelectorAll('[data-monitor-account]:checked')].map(x=>x.dataset.monitorAccount),publication_days:Number($('#monitorDays').value),frequency:$('#monitorFrequency').value,at_time:$('#monitorTime').value,interval_minutes:Number($('#monitorInterval').value),start_date:$('#monitorStart').value,end_date:$('#monitorEnd').value,use_batch:$('#monitorBatch').checked};}
  function frequency(){$('#monitorTimeField').hidden=$('#monitorFrequency').value!=='daily';$('#monitorIntervalField').hidden=$('#monitorFrequency').value!=='interval';}
  function changed(){dirty=true;version++;$('#monitorPlan').textContent='设置已更改，请预览本轮范围';$('#monitorError').textContent='';}
  function showPlan(p){const reason=p.reason;$('#monitorPlan').classList.toggle('plan-blocked',!p.allowed);$('#monitorPlan').innerHTML=`<b>${p.eligible} 条作品 · ${p.calls} 次请求</b><span>${p.method==='batch'?'批量统计接口':'单作品接口 · 同时最多刷新 3 条'}</span>${reason?'<p>'+esc(reason)+'</p>':''}${!p.allowed?'<a href="#connection" class="text-link">调整接口设置</a>':''}`;$('#monitorVideos').innerHTML=(p.videos||[]).slice(0,12).map(v=>`<button type="button" data-open-work="${v.aweme_id}" class="monitor-video"><span>${esc(v.title||v.aweme_id)}</span><small>${esc(v.account_name||'')}</small></button>`).join('')+(p.eligible>12?`<p class="form-hint">另有 ${p.eligible-12} 条作品</p>`:'');}
  function render(data){current=data;const c=data.config;dirty=false;
    $('#monitorEnabled').checked=c.enabled;$('#monitorDays').value=c.publication_days;$('#monitorFrequency').value=c.frequency;$('#monitorTime').value=c.at_time;$('#monitorInterval').value=c.interval_minutes;$('#monitorStart').value=c.start_date;$('#monitorEnd').value=c.end_date||'';
    $('#monitorBatch').disabled=!data.limits.allow_batch_statistics;$('#monitorBatch').checked=c.use_batch&&data.limits.allow_batch_statistics;
    $('#monitorBatchField').hidden=!data.limits.allow_batch_statistics;
    $('#monitorAccounts').innerHTML=data.accounts.filter(a=>a.enabled).map(a=>`<label class="check-label"><input type="checkbox" data-monitor-account="${esc(a.sec_uid)}" ${c.account_ids.includes(a.sec_uid)?'checked':''}><span>${esc(a.nickname||a.note||'未命名账号')}</span></label>`).join('')||'<a class="text-link" href="/home">先添加监控账号</a>';
    $('#monitorNext').textContent=c.enabled?(data.next_run?'下次 '+data.next_run.slice(0,16).replace('T',' ')+' · 北京时间':'已到结束日期'):'未启用 · 北京时间';
    frequency();showPlan(data.preview);
    const labels={started:'准备中',queued:'已排队',running:'进行中',completed:'完成',partial:'部分完成',blocked:'超限 / 未配置',skipped:'跳过',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
    $('#monitorHistory').innerHTML=data.history.map(r=>`<tr><td>${esc(r.scheduled_for.slice(0,16).replace('T',' '))}</td><td>${r.eligible} 条 / ${r.calls} 次</td><td>${r.job_id?`<a class="text-link" href="/collect?job=${r.job_id}">${esc(labels[r.status]||r.status)}</a>`:esc(labels[r.status]||r.status)}</td><td>${esc(r.message||'')}</td></tr>`).join('')||'<tr><td colspan="4" class="wb-empty">尚无定时执行记录</td></tr>';
  }
  $('#monitorFrequency').onchange=frequency;
  $('#monitorForm').addEventListener('input',changed);
  $('#monitorForm').addEventListener('change',e=>{if(e.target.tagName==='SELECT')changed();});
  $('#monitorAll').onclick=()=>{document.querySelectorAll('[data-monitor-account]').forEach(e=>e.checked=true);changed();};
  $('#monitorOwn').onclick=()=>{document.querySelectorAll('[data-monitor-account]').forEach(e=>e.checked=current.accounts.find(a=>a.sec_uid===e.dataset.monitorAccount)?.role==='own');changed();};
  $('#monitorNone').onclick=()=>{document.querySelectorAll('[data-monitor-account]').forEach(e=>e.checked=false);changed();};
  $('#monitorPreview').onclick=async()=>{const v=++version,body=values();$('#monitorError').textContent='';try{const p=await api('/api/monitoring/preview',body);if(v===version)showPlan(p);}catch(e){$('#monitorError').textContent=e.message;}};
  $('#monitorForm').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;$('#monitorError').textContent='';try{render(await api('/api/monitoring/settings',values()));$('#monitorError').textContent='';const toast=$('#toast');toast.textContent='监控计划已保存';toast.classList.add('show');setTimeout(()=>toast.classList.remove('show'),2500);}catch(error){$('#monitorError').textContent=error.message;}finally{button.disabled=false;}};
  async function load(){try{const data=await api('/api/monitoring/status');if(!dirty)render(data);else{$('#monitorBatch').disabled=!data.limits.allow_batch_statistics;$('#monitorBatchField').hidden=!data.limits.allow_batch_statistics;if(!data.limits.allow_batch_statistics)$('#monitorBatch').checked=false;}}catch(e){$('#monitorError').textContent=e.message;}}
  addEventListener('gc-data-updated',e=>{if(e.detail?.type==='accounts')load();});addEventListener('playback-settings-changed',load);addEventListener('settings-reload-requested',load);load();
})();
