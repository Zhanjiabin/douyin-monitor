(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const APP = 'Douyin-App-V3-API';
  const DEFAULT = 'fetch_video_statistics_api_v1_douyin_app_v3_fetch_video_statistics_get';
  const state = {catalog:null, op:null, preview:null, jobs:[], job:null, view:'table', rowPage:0, source:[], accounts:[], sequence:0, pending:false, launched:null};
  let favorites;
  try { favorites = new Set(JSON.parse(localStorage.getItem('gc-tikhub-favorites') || '[]')); } catch { favorites = new Set(); }
  const groupName = id => id.replace(/^Douyin/, '抖音').replace(/^TikTok/, 'TikTok').replace(/^Xiaohongshu/, '小红书').replace(/^Kuaishou/, '快手').replace(/^WeChat/, '微信').replace(/^Bilibili/, 'B站').replace(/-API$/, '').replaceAll('-', ' ');
  const slug = op => op.path.split('/').pop();
  async function api(path, body) {
    const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || `请求失败 ${response.status}`);
    return data;
  }
  function toast(message) { $('#toast').textContent = message; $('#toast').classList.add('show'); setTimeout(() => $('#toast').classList.remove('show'), 2500); }
  function fail(error) { $('#requestError').textContent = error.message; }
  function invalidate() {
    state.preview = null; $('#confirmCalls').checked = false; $('#runRequest').disabled = true;
    $('#requestPreview').textContent = '参数更新后请重新预览请求数'; $('#requestError').textContent = '';
  }
  function updateRun() { $('#runRequest').disabled = state.pending || !state.preview?.allowed || !$('#confirmCalls').checked; }
  function renderList() {
    const query = $('#endpointSearch').value.trim().toLowerCase();
    const group = $('#apiGroup').value;
    const ops = state.catalog.operations.filter(op => (!group || op.group === group) && (!$('#onlyFavorites').checked || favorites.has(op.id)) && (!query || `${op.title} ${op.path} ${op.group}`.toLowerCase().includes(query)));
    $('#groupCount').textContent = `${ops.length} 个接口`;
    $('#endpointList').innerHTML = ops.map(op => `<button class="endpoint-item ${state.op?.id === op.id ? 'active' : ''}" data-op="${esc(op.id)}"><span class="endpoint-name">${esc(op.title)}</span><span class="endpoint-code"><span class="method ${op.method === 'POST' ? 'post' : ''}">${op.method}</span><span>${esc(slug(op))}</span>${favorites.has(op.id) ? '★' : ''}</span></button>`).join('') || '<p class="no-results">没有匹配的接口</p>';
  }
  function fieldSchema(schema) { return schema.anyOf?.find(s => s.type !== 'null') || schema.oneOf?.find(s => s.type !== 'null') || schema; }
  function renderParameters() {
    $('#parameterFields').innerHTML = state.op.parameters.map((p, i) => {
      const s = fieldSchema(p.schema), value = p.schema.default ?? s.default;
      const hint = String(p.description || '').split('/')[0];
      const nullable = p.schema.anyOf?.some(s => s.type === 'null');
      let input;
      const attrs = `id="param${i}" data-param="${esc(p.name)}" autocomplete="off"`;
      if (s.enum) input = `<select ${attrs}><option value="">请选择</option>${s.enum.map(v => `<option value="${esc(v)}" ${value === v ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select>`;
      else if (s.type === 'boolean') input = `<select ${attrs}><option value="">使用默认值</option><option value="true" ${value === true ? 'selected' : ''}>true</option><option value="false" ${value === false ? 'selected' : ''}>false</option></select>`;
      else if (s.type === 'array' || s.type === 'object') input = `<textarea ${attrs} class="code-editor" rows="3" placeholder="${esc(JSON.stringify(exampleFor(p)))}">${value === undefined ? '' : esc(JSON.stringify(value))}</textarea>`;
      else input = `<input ${attrs} type="${p.secret ? 'password' : 'text'}" ${['integer','number'].includes(s.type) ? 'inputmode="decimal"' : ''} value="${esc(value ?? '')}" placeholder="${esc(p.example ?? s.example ?? (p.required ? '必填' : '选填'))}">`;
      return `<div class="parameter-field"><label for="param${i}"><b>${esc(p.name)}</b>${p.required ? '<span class="required">*</span>' : ''}${s.type ? ` <span class="muted">${esc(s.type)}</span>` : ''}</label>${input}${hint && hint !== p.name ? `<span class="param-description">${esc(hint)}</span>` : ''}${nullable ? `<label class="check-label"><input type="checkbox" data-null-param="${esc(p.name)}">空值 null（不发送）</label>` : ''}</div>`;
    }).join('');
    $('#bodyField').hidden = !state.op.has_body;
    $('#bodyJson').value = ''; $('#bodySchema').textContent = JSON.stringify(state.op.body_schema, null, 2);
    $('#pageLimit').value = '1'; $('#pageLimit').disabled = !state.op.cursor_parameter || state.op.method !== 'GET';
    $('#pageLimitLabel').title = state.op.cursor_parameter ? '仅在响应明确返回下一页时继续；到达页数上限即停止' : '接口未声明分页参数';
    $('#importLabel').hidden = !state.op.import_kind;
    $('#importText').textContent = `写入${state.op.import_kind === '播放统计' ? '作品播放记录' : state.op.import_kind === '评论' ? '评论库' : '作品库'}`;
    $('#importResults').checked = !!state.op.import_kind;
    $('#batchJson').value = ''; $('#batchDetails').open = false;
    $('#operationDescription').textContent = state.op.description;
    renderSources();
  }
  function exampleFor(p) { return p.example ?? p.schema.example ?? fieldSchema(p.schema).default ?? (fieldSchema(p.schema).type === 'array' ? [] : {}); }
  function renderSources() {
    const names = state.op.parameters.map(p => p.name);
    const videos = names.some(n => ['aweme_id','aweme_ids','item_id','share_url','object_id'].includes(n));
    const accounts = names.some(n => ['sec_user_id','sec_uid','uid','user_id'].includes(n));
    $('#localSource').hidden = !videos && !accounts;
    $('#localSource').innerHTML = '<option value="">从监控库填入…</option>' + (accounts ? `<optgroup label="监控账号">${state.accounts.map((a,i) => `<option value="a:${i}">${esc(a.nickname || a.author_name || a.sec_uid)}</option>`).join('')}</optgroup>` : '') + (videos ? `<optgroup label="监控作品">${state.source.map((v,i) => `<option value="v:${i}">${esc((v.account_name || v.author_name || '') + ' · ' + (v.title || v.aweme_id))}</option>`).join('')}</optgroup>` : '');
  }
  function paramInput(name) { return [...document.querySelectorAll('[data-param]')].find(el => el.dataset.param === name); }
  function fillParam(name, value) { const input = paramInput(name); if (input && value !== undefined && value !== null) input.value = String(value); }
  async function selectOperation(id, changeGroup = false) {
    const sequence = ++state.sequence;
    invalidate();
    const op = await api('/api/tikhub/operation?id=' + encodeURIComponent(id));
    if (sequence !== state.sequence) return;
    state.op = op;
    if (changeGroup) $('#apiGroup').value = op.group;
    $('#operationTitle').textContent = op.title; $('#operationSlug').textContent = slug(op);
    $('#requestMethod').textContent = op.method; $('#requestMethod').classList.toggle('post', op.method === 'POST');
    $('#operationDocs').href = op.docs; $('#favorite').textContent = favorites.has(op.id) ? '★' : '☆';
    $('#favorite').setAttribute('aria-label', favorites.has(op.id) ? '取消收藏接口' : '收藏接口');
    renderParameters(); renderList();
    const args = new URLSearchParams({group:op.group, operation:op.id}); history.replaceState(null, '', '/tikhub?' + args);
    if (!state.job) renderWelcome();
  }
  function readRequest() {
    if (!state.op) throw new Error('请先选择接口');
    const params = {};
    document.querySelectorAll('[data-param]').forEach(el => { if (el.value !== '') params[el.dataset.param] = el.value; });
    document.querySelectorAll('[data-null-param]:checked').forEach(el => { params[el.dataset.nullParam] = null; });
    return {operation_id:state.op.id, params, ...(state.op.has_body ? {body_json:$('#bodyJson').value} : {}),
      ...(($('#batchJson').value.trim()) ? {items_json:$('#batchJson').value} : {}),
      page_limit:$('#pageLimit').value, import_results:!!state.op.import_kind && $('#importResults').checked};
  }
  async function previewRequest() {
    $('#requestError').textContent = ''; $('#previewRequest').disabled = true;
    try {
      const request = readRequest(), sequence = state.sequence, snapshot = JSON.stringify(request);
      const p = await api('/api/tikhub/plan', request);
      if (sequence !== state.sequence || snapshot !== JSON.stringify(readRequest())) return;
      state.preview = p;
      $('#requestPreview').innerHTML = `<strong>最多 ${p.calls} 次请求</strong> · ${p.batches} 组 × ${p.page_limit} 页<br><span class="muted">今日 ${p.today_calls}/${p.daily_limit} · 单次限额 ${p.job_limit}</span>${p.reason ? `<br><span class="inline-error">${esc(p.reason)}</span>` : ''}`;
      updateRun();
    } catch (e) { invalidate(); fail(e); } finally { $('#previewRequest').disabled = false; }
  }
  function renderWelcome() {
    const shortcuts = [
      ['fetch_video_statistics', '视频播放统计'], ['fetch_multi_video_statistics', '批量播放统计 · 每次最多 50 条'],
      ['fetch_one_video_v3', '视频详情、文案与媒体链接'], ['fetch_user_post_videos', '账号作品与发布时间'],
      ['fetch_video_comments', '作品评论'], ['fetch_video_mix_post_list', '合集作品'], ['fetch_hot_search_list', '热搜榜']
    ];
    const choices = shortcuts.map(([name,label]) => [state.catalog.operations.find(o => o.group === APP && slug(o) === name),label]).filter(([op]) => op);
    $('#responseContent').innerHTML = `<div class="api-welcome"><h2>常用接口</h2>${choices.map(([op,label]) => `<div class="shortcut-row"><span>${esc(label)}</span><button data-shortcut="${esc(op.id)}">填入参数 →</button></div>`).join('')}<p class="hint">播放统计支持写回监控作品。视频详情和评论可进入本机内容库；其余接口保留完整响应，支持 JSON、CSV、Excel 导出。</p></div>`;
  }
  function safeUrl(value) { try { const u = new URL(value); return ['https:','http:'].includes(u.protocol) && !u.username ? u.href : ''; } catch { return ''; } }
  function cell(key, value) {
    const text = value !== null && typeof value === 'object' ? JSON.stringify(value) : String(value ?? '');
    let url = safeUrl(text);
    if (['aweme_id','video_id','item_id'].includes(key) && /^\d{10,25}$/.test(text) && state.job?.result?.operation?.group?.startsWith('Douyin')) url = 'https://www.douyin.com/video/' + text;
    return url ? `<a href="${esc(url)}" target="_blank" rel="noreferrer">${esc(text)}</a>` : esc(text);
  }
  function renderResult() {
    document.querySelectorAll('[data-result-view]').forEach(b => { const on = b.dataset.resultView === state.view; b.classList.toggle('active', on); b.setAttribute('aria-selected', String(on)); });
    $('#responsePager').hidden = true;
    if (state.view === 'history') {
      $('#responseContent').innerHTML = state.jobs.map(j => `<button class="job-row ${j.id === state.job?.id ? 'active' : ''}" data-job="${j.id}">${esc(j.title)}<small>${esc(j.created_at)} · ${esc(statusLabel(j.status))} · ${j.count || 0} 条</small></button>`).join('') || '<p class="no-results">暂无接口任务</p>';
      return;
    }
    if (!state.job) { renderWelcome(); return; }
    const result = state.job.result || {};
    if (state.view === 'json') {
      $('#responseContent').innerHTML = '<pre class="json-response"></pre>';
      $('#responseContent pre').textContent = result.response_json || JSON.stringify(result, null, 2);
    } else {
      const rows = state.job.records || [], pages = Math.ceil(rows.length / 40);
      state.rowPage = Math.min(state.rowPage, Math.max(0, pages - 1));
      const keys = [...new Set(rows.flatMap(r => Object.keys(r)))];
      $('#responseContent').innerHTML = rows.length ? `<table><thead><tr><th>#</th>${keys.map(k => `<th>${esc(k)}</th>`).join('')}</tr></thead><tbody>${rows.slice(state.rowPage * 40, (state.rowPage + 1) * 40).map((row,i) => `<tr><td>${state.rowPage * 40 + i + 1}</td>${keys.map(k => `<td title="${esc(typeof row[k] === 'object' ? JSON.stringify(row[k]) : row[k])}">${cell(k, row[k])}</td>`).join('')}</tr>`).join('')}</tbody></table>` : `<p class="no-results">${esc(state.job.message || (['running','queued'].includes(state.job.status) ? '请求执行中，结果将自动更新' : '本次响应没有数据列表，可在 JSON 查看完整响应'))}</p>`;
      $('#responsePager').hidden = pages <= 1;
      $('#rowsPage').textContent = `${state.rowPage + 1} / ${pages} · ${rows.length} 条`;
      $('#rowsPrev').disabled = state.rowPage === 0; $('#rowsNext').disabled = state.rowPage + 1 >= pages;
    }
  }
  const statusLabel = s => ({queued:'排队中',running:'执行中',completed:'完成',failed:'失败',partial:'部分完成',cancelled:'已取消',interrupted:'已中断'}[s] || s);
  function renderJob() {
    const j = state.job, r = j?.result || {}, active = j && ['queued','running'].includes(j.status);
    const progress = j?.progress || {};
    $('#cancelRequest').hidden = !active;
    $('#resultSummary').innerHTML = `<span><b>${esc(statusLabel(j.status))}</b></span><span>${esc(j.title)}</span><span>${r.attempted ?? progress.done ?? 0}/${r.requested ?? progress.total ?? '—'} 次请求</span><span>${(j.records || []).length} 条</span>${r.imported?.imported ? `<span>已写入 ${r.imported.imported} 条</span>` : ''}${r.imported?.playback !== undefined ? `<span>有效播放 ${r.imported.playback} 条</span>` : ''}`;
    const receipts = r.receipts || [];
    $('#receiptList').innerHTML = receipts.map(x => `<div class="receipt"><details><summary><span class="${x.ok ? 'ok' : 'bad'}">${x.http_status || '网络'} · ${x.ok ? '成功' : '失败'}</span> · 第 ${x.batch} 组 / ${x.page} 页 · ${x.elapsed_ms} ms${x.request_id ? ' · ' + esc(x.request_id) : ''}</summary><pre>${esc(x.error || (x.next_cursor !== null && x.next_cursor !== undefined ? '下一页游标：' + x.next_cursor : '无下一页'))}</pre>${x.redirect_url ? `<a class="text-link" href="${esc(safeUrl(x.redirect_url))}" target="_blank" rel="noreferrer">打开接口返回的链接 ↗</a>` : ''}</details></div>`).join('') + (r.errors || []).map(e => `<div class="receipt"><span class="bad">${esc(e.error)}</span></div>`).join('');
    $('#exportResult').setAttribute('aria-disabled', String(!receipts.length));
    $('#exportResult').href = '/api/douk/export?id=' + j.id + '&format=' + $('#exportFormat').value;
    const last = receipts.at(-1), same = state.launched?.operation_id === state.op?.id && !state.launched?.items_json;
    $('#nextPage').hidden = active || !same || !state.op?.cursor_parameter || last?.next_cursor === undefined || last?.next_cursor === null;
    renderResult();
  }
  async function openJob(id) { const result = await api('/api/douk/jobs/' + id); state.job = result; renderJob(); }
  async function refresh() {
    const data = await api('/api/tikhub/status');
    state.jobs = data.jobs;
    $('#connectionState').textContent = data.settings.key_set ? 'Key 已配置' : '尚未配置 Key';
    $('#quota').textContent = `今日请求 ${data.today_calls} / ${data.settings.daily_call_limit}`;
    if (!$('#settingsDialog').open) { $('#maxCalls').value = data.settings.max_calls_per_job; $('#dailyCalls').value = data.settings.daily_call_limit; }
    const awaitingReceipt = state.job?.status === 'cancelled' && state.job.cancelled_from === 'running' && !state.job.result?.cancelled;
    if (state.job && (['queued','running'].includes(state.job.status) || awaitingReceipt)) await openJob(state.job.id);
    else if (state.view === 'history') renderResult();
  }
  $('#endpointList').onclick = e => { const b = e.target.closest('[data-op]'); if (b) selectOperation(b.dataset.op).catch(fail); };
  $('#endpointSearch').oninput = renderList; $('#onlyFavorites').onchange = renderList;
  $('#apiGroup').onchange = () => { renderList(); const first = state.catalog.operations.find(op => op.group === $('#apiGroup').value); if (first) selectOperation(first.id).catch(fail); };
  $('#favorite').onclick = () => { if (!state.op) return; favorites.has(state.op.id) ? favorites.delete(state.op.id) : favorites.add(state.op.id); try { localStorage.setItem('gc-tikhub-favorites', JSON.stringify([...favorites])); } catch { /* Session-only favorites still work. */ } $('#favorite').textContent = favorites.has(state.op.id) ? '★' : '☆'; renderList(); };
  $('#requestForm').addEventListener('input', e => { if (e.target.id !== 'confirmCalls') invalidate(); });
  $('#parameterFields').onchange = e => { if (e.target.dataset.nullParam) { const input = paramInput(e.target.dataset.nullParam); input.disabled = e.target.checked; } invalidate(); };
  $('#confirmCalls').onchange = updateRun;
  $('#previewRequest').onclick = previewRequest;
  $('#fillBodyExample').onclick = () => { $('#bodyJson').value = state.op.body_example; invalidate(); };
  $('#batchTemplate').onclick = () => { const request = readRequest(); $('#batchJson').value = '[\n  {"params": ' + JSON.stringify(request.params) + (request.body_json?.trim() ? ', "body": ' + request.body_json : '') + '}\n]'; invalidate(); };
  $('#localSource').onchange = () => {
    const [type,index] = $('#localSource').value.split(':'), row = (type === 'a' ? state.accounts : state.source)[Number(index)]; if (!row) return;
    if (type === 'a') { ['sec_user_id','sec_uid'].forEach(k => fillParam(k, row.sec_uid)); ['uid','user_id'].forEach(k => fillParam(k, row.uid || row.user_id)); }
    else { ['aweme_id','aweme_ids','item_id','object_id'].forEach(k => fillParam(k, row.aweme_id)); fillParam('share_url', 'https://www.douyin.com/video/' + row.aweme_id); }
    invalidate();
  };
  $('#requestForm').onsubmit = async e => {
    e.preventDefault(); if (!state.preview?.allowed || !$('#confirmCalls').checked || state.pending) return;
    state.pending = true; updateRun(); $('#requestError').textContent = '';
    try {
      const request = {...readRequest(), endpoint:'tikhub_api', title:state.op.title, allow_charges:true, fingerprint:state.preview.fingerprint};
      const j = await api('/api/douk/jobs', request); state.launched = readRequest(); state.rowPage = 0; state.view = 'table';
      invalidate(); await openJob(j.id); await refresh();
    } catch (error) { fail(error); } finally { state.pending = false; updateRun(); }
  };
  document.querySelectorAll('[data-result-view]').forEach(b => b.onclick = () => { state.view = b.dataset.resultView; renderResult(); });
  $('#responseContent').onclick = e => { const shortcut = e.target.closest('[data-shortcut]'), job = e.target.closest('[data-job]'); if (shortcut) selectOperation(shortcut.dataset.shortcut, true).catch(fail); if (job) { state.view = 'table'; state.rowPage = 0; state.launched = null; openJob(job.dataset.job).catch(fail); } };
  $('#rowsPrev').onclick = () => { state.rowPage--; renderResult(); }; $('#rowsNext').onclick = () => { state.rowPage++; renderResult(); };
  $('#exportFormat').onchange = () => { if (state.job) $('#exportResult').href = '/api/douk/export?id=' + state.job.id + '&format=' + $('#exportFormat').value; };
  $('#cancelRequest').onclick = async () => { try { await api('/api/douk/cancel', {id:state.job.id}); await openJob(state.job.id); } catch (e) { fail(e); } };
  $('#nextPage').onclick = () => { const last = state.job.result.receipts.at(-1); fillParam(state.op.cursor_parameter, last.next_cursor); $('#pageLimit').value = '1'; invalidate(); toast('已填入下一页游标，请预览后执行'); };
  $('#openSettings').onclick = () => { $('#settingsError').textContent = ''; $('#settingsDialog').showModal(); };
  $('#closeSettings').onclick = () => $('#settingsDialog').close();
  $('#settingsForm').onsubmit = async e => { e.preventDefault(); try { await api('/api/playback/settings', {api_key:$('#apiKey').value, max_calls_per_job:$('#maxCalls').value, daily_call_limit:$('#dailyCalls').value}); $('#apiKey').value = ''; $('#settingsDialog').close(); invalidate(); await refresh(); toast('连接已保存'); } catch (e) { $('#settingsError').textContent = e.message; } };
  $('#clearKey').onclick = async () => { try { await api('/api/playback/settings', {clear_key:true}); $('#apiKey').value = ''; invalidate(); await refresh(); toast('Key 已清除'); } catch (e) { $('#settingsError').textContent = e.message; } };
  async function init() {
    const [cat] = await Promise.all([api('/api/tikhub/catalog'), refresh()]); state.catalog = cat;
    $('#catalogCount').textContent = `${cat.total.toLocaleString()} 个接口 · ${cat.groups.length} 个分组`; $('#catalogVersion').textContent = cat.version;
    $('#apiGroup').innerHTML = '<option value="">全部分组</option>' + cat.groups.map(g => `<option value="${esc(g.id)}">${esc(groupName(g.id))} (${g.count})</option>`).join('');
    const args = new URLSearchParams(location.search); let initial = cat.operations.find(op => op.id === args.get('operation'));
    initial ||= cat.operations.find(op => op.group === args.get('group'));
    initial ||= cat.operations.find(op => op.id === DEFAULT); $('#apiGroup').value = initial.group;
    await selectOperation(initial.id);
    const sources = await Promise.allSettled([api('/api/insights?days=36500'), api('/api/intel/tracked-accounts')]);
    if (sources[0].status === 'fulfilled') state.source = sources[0].value.videos || [];
    if (sources[1].status === 'fulfilled') state.accounts = sources[1].value.items || [];
    renderSources();
    if (args.get('video')) fillParam('aweme_ids', args.get('video'));
    setInterval(() => { if (!document.hidden) refresh().catch(e => { $('#connectionState').textContent = e.message; }); }, 4000);
  }
  init().catch(fail);
})();
