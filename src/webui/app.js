'use strict';

const $ = (s) => document.querySelector(s);

function toast(msg, ms) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), ms || 3200);
}

async function api(path, body) {
  const opt = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {};
  const r = await fetch(path, opt);
  return await r.json();
}

function fmt(n) {
  if (n === null || n === undefined || n === '') return '—';
  n = Number(n);
  if (!isFinite(n)) return '—';
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿';
  if (n >= 1e4) return (n / 1e4).toFixed(1) + '万';
  return n.toLocaleString();
}

function deltaHtml(d) {
  if (d === null || d === undefined) return '<span class="faint">—</span>';
  if (d > 0) return `<span class="up">+${Number(d).toLocaleString()}</span>`;
  if (d < 0) return `<span class="down">${Number(d).toLocaleString()}</span>`;
  return '<span class="faint">0</span>';
}

function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* ---------------- 标签页 ---------------- */
document.querySelectorAll('.tab').forEach((t) => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
    document.querySelectorAll('.page').forEach((x) => x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById(t.dataset.p).classList.add('active');
    if (t.dataset.p === 'p3') loadResult();
    if (t.dataset.p === 'p4') loadRuns();
  });
});

/* ---------------- 清单 ---------------- */
async function loadWatchlist() {
  const d = await api('/api/watchlist');
  const s = d.stats || {};
  $('#watchStats').innerHTML = `
    <div class="stat"><div class="k">清单总数</div><div class="v">${s.total || 0}</div></div>
    <div class="stat"><div class="k">追踪中</div><div class="v">${s.active || 0}</div></div>
    <div class="stat"><div class="k">已停用</div><div class="v faint">${s.disabled || 0}</div></div>
    <div class="stat"><div class="k">未识别到合集</div><div class="v ${s.no_mix ? 'faint' : ''}">${s.no_mix || 0}</div></div>`;

  const items = d.items || [];
  if (!items.length) {
    $('#watchTable').innerHTML = '<div class="empty">清单为空，把视频链接粘到上面导入。</div>';
    return;
  }
  const rows = items.map((it) => {
    let tag = '<span class="tag off">待解析</span>';
    if (it.resolve_state === 'ok') tag = '<span class="tag on">追踪中</span>';
    else if (it.resolve_state === 'no_mix') tag = '<span class="tag warn">无合集</span>';
    else if (it.resolve_state === 'failed') tag = '<span class="tag off">解析失败</span>';
    if (!it.enabled) tag = '<span class="tag off">已停用</span>';
    return `<tr>
      <td class="faint">${it.id}</td>
      <td class="mono">${esc(String(it.url).replace(/^https?:\/\//, ''))}</td>
      <td class="faint">${esc(it.note || '')}</td>
      <td>${esc(it.mix_name || '—')}</td>
      <td class="num">${it.episode_count || '—'}</td>
      <td>${tag}</td>
      <td>
        <button class="mini ghost" onclick="toggleItem(${it.id},${it.enabled ? 0 : 1})">${it.enabled ? '停用' : '启用'}</button>
        <button class="mini danger" onclick="deleteItem(${it.id})">删除</button>
      </td></tr>`;
  }).join('');
  $('#watchTable').innerHTML = `<table><thead><tr>
      <th style="width:48px">#</th><th>链接</th><th>备注</th><th>所属合集</th>
      <th style="width:60px">集数</th><th style="width:80px">状态</th><th style="width:130px">操作</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
}

window.toggleItem = async (id, enabled) => {
  await api('/api/watchlist/toggle', { id: id, enabled: !!enabled });
  loadWatchlist();
};
window.deleteItem = async (id) => {
  if (!confirm('确认从清单中删除这一条？（历史数据保留）')) return;
  await api('/api/watchlist/delete', { id: id });
  loadWatchlist();
};

$('#btnImport').addEventListener('click', async () => {
  const text = $('#paste').value.trim();
  if (!text) return toast('先粘贴链接');
  const r = await api('/api/watchlist/add', { text: text, note: $('#noteInput').value.trim() });
  if (!r.ok) return toast(r.error || '导入失败');
  toast(`导入完成：新增 ${r.added} 条，重复跳过 ${r.dup} 条`);
  $('#paste').value = '';
  loadWatchlist();
});
$('#btnReloadList').addEventListener('click', () => { loadWatchlist(); toast('已刷新'); });

/* ---------------- 抓取 ---------------- */
let polling = null;

function schedText(st) {
  const times = (st.config && st.config.schedule_times) || [];
  const doneToday = !!(st.overview && st.overview.fetch_date === st.today);
  if (!times.length) {
    return doneToday
      ? '仅手动抓取 · 今日已采集 ✓'
      : '仅手动抓取 · 今日还没跑 ⚠';
  }
  return st.next_run ? '下次自动抓取 ' + st.next_run
                     : (doneToday ? '今日已采集 ✓' : '未启用定时');
}

async function pollRun() {
  const st = await api('/api/state');
  const r = st.run;
  $('#dot').className = 'dot' + (r.running ? ' busy' : '');
  $('#runlabel').textContent = r.running ? '抓取中' : '就绪';
  $('#nextrun').textContent = schedText(st);
  $('#logBox').textContent = (r.logs || []).join('\n');
  $('#logBox').scrollTop = $('#logBox').scrollHeight;
  $('#btnRun').disabled = r.running;
  $('#btnStop').disabled = !r.running;

  const pct = r.total ? Math.round(r.done / r.total * 100) : 0;
  $('#bar').style.width = pct + '%';
  $('#progressTxt').textContent = r.running
    ? `${r.msg || ''} · ${r.done}/${r.total}（${pct}%）`
    : (r.finished_at ? '上次完成于 ' + r.finished_at : '未开始');

  if (!r.running && polling) {
    clearInterval(polling);
    polling = null;
    loadWatchlist();
    const lr = r.last_result;
    if (lr && lr.interrupted) {
      toast('抓取被中断：已保存 ' + lr.ok + ' 个合集的数据。原因请看下方日志。', 8000);
    } else if (lr) {
      toast('抓取完成：成功 ' + lr.ok + '，失败 ' + lr.failed);
    }
  }
}

function startPolling() {
  if (polling) return;
  pollRun();
  polling = setInterval(pollRun, 1500);
}

$('#btnRun').addEventListener('click', async () => {
  const r = await api('/api/run', {});
  if (!r.ok) return toast('已有任务在运行中');
  toast('已开始抓取');
  startPolling();
});
$('#btnStop').addEventListener('click', async () => {
  await api('/api/stop', {});
  toast('已请求停止');
});

/* ---------------- 结果 ---------------- */
let sortState = { key: 'play', dir: 'desc' };
let mixCache = [];

function renderLegend(labels) {
  const items = [['var(--lv1)', labels[1] || '达标'],
                 ['var(--lv2)', labels[2] || ''],
                 ['var(--lv3)', labels[3] || '']].filter((x) => x[1]);
  const box = document.getElementById('levelLegend');
  if (box) {
    box.innerHTML = items
      .map(([c, t]) => `<span><i style="background:${c}"></i>${esc(t)}</span>`).join('');
  }
}

function sortedMixes() {
  const s = mixCache.slice();
  if (sortState.key === 'play') {
    const k = sortState.dir === 'desc' ? -1 : 1;
    s.sort((a, b) => ((a.play_vv || 0) - (b.play_vv || 0)) * k);
    return s;
  }
  // 按发布日期排：没有发布日期的永远沉到最后，不混在正常数据里
  const has = s.filter((x) => x.first_publish_at);
  const none = s.filter((x) => !x.first_publish_at);
  has.sort((a, b) => (a.first_publish_at < b.first_publish_at ? -1
    : a.first_publish_at > b.first_publish_at ? 1 : 0));
  if (sortState.dir === 'desc') has.reverse();
  return has.concat(none);
}

function renderMixTable() {
  const rows = sortedMixes();
  const ar = (k) => (sortState.key === k ? (sortState.dir === 'desc' ? '▼' : '▲') : '▾');
  const th = (k, label, w) => `<th class="sortable${sortState.key === k ? ' sorted' : ''}"
    data-sort="${k}"${w ? ` style="width:${w}px"` : ''}>${label}<span class="arrow">${ar(k)}</span></th>`;

  $('#mixTable').innerHTML = `<table><thead><tr>
      <th>合集名</th><th>账号</th>${th('date', '发布日期', 104)}${th('play', '总播放量', 116)}
      <th>本次增量</th><th>收藏</th>
      <th style="width:56px">集数</th><th style="width:74px">更新至</th>
    </tr></thead><tbody>${rows.map((m) => {
    const lv = m.level || 0;
    return `<tr class="${lv ? 'lv' + lv : ''}">
      <td>${esc(m.mix_name)}${m.stale
        ? ' <span class="tag warn" title="该合集本次未采集到，显示的是上一次的数据">未更新</span>'
        : ''}</td>
      <td class="faint">${esc(m.author_name || '—')}</td>
      <td class="num faint">${m.first_publish_at || '—'}</td>
      <td class="num">${fmt(m.play_vv)}${m.level_label
        ? ` <span class="tag lv${m.level}">${esc(m.level_label)}</span>` : ''}</td>
      <td class="num">${deltaHtml(m.delta)}</td>
      <td class="num faint">${m.collect_vv || 0}</td>
      <td class="num">${m.episode_count || '—'}</td>
      <td class="num">${m.updated_to_episode || '—'}</td></tr>`;
  }).join('')}</tbody></table>`;

  $('#mixTable').querySelectorAll('th[data-sort]').forEach((el) => {
    el.onclick = () => {
      const k = el.dataset.sort;
      if (sortState.key === k) sortState.dir = sortState.dir === 'desc' ? 'asc' : 'desc';
      else sortState = { key: k, dir: 'desc' };
      const sel = $('#sortSelect');
      if (sel) sel.value = (k === 'play' ? 'play_' : 'date_') + sortState.dir;
      renderMixTable();
    };
  });
}

// 顶层绑定必须做存在性检查：万一 HTML 与 JS 版本不同步（例如只替换了其一），
// 裸访问会让整个脚本在这里中断，导致所有按钮失效。
const sortSel = document.getElementById('sortSelect');
if (sortSel) {
  sortSel.addEventListener('change', (e) => {
    const [k, d] = e.target.value.split('_');
    sortState = { key: k, dir: d };
    renderMixTable();
  });
}

async function loadResult() {
  const d = await api('/api/result');
  const ov = d.overview || {};
  const firstRun = !ov.prev_date;
  $('#resultDate').textContent = ov.fetch_date
    ? `数据日期 ${ov.fetch_date}` + (firstRun
        ? ' · 首次采集，明天第二次抓取后开始有日增对比'
        : ` · 对比 ${ov.prev_date}`)
    : '暂无数据';

  $('#resultStats').innerHTML = `
    <div class="stat"><div class="k">追踪合集</div><div class="v">${ov.mix_count || 0}</div></div>
    <div class="stat"><div class="k">总播放量</div><div class="v">${fmt(ov.total_play)}</div></div>
    <div class="stat"><div class="k">本次总增量</div>${firstRun
      ? '<div class="v faint">—</div><div class="d faint">首次采集，无可比数据</div>'
      : `<div class="v">${deltaHtml(ov.total_delta)}</div>`}</div>
    <div class="stat"><div class="k">停涨合集</div>${firstRun
      ? '<div class="v faint">—</div><div class="d faint">需至少两天数据</div>'
      : `<div class="v">${ov.stop_count || 0}</div>`}</div>`;

  const mixes = ov.mixes || [];
  mixCache = mixes;
  renderLegend(ov.labels || {});
  if (!mixes.length) {
    $('#mixTable').innerHTML = '<div class="empty">暂无数据，先去「抓取」页跑一次。</div>';
    $('#epBlocks').innerHTML = '';
    return;
  }
  renderMixTable();

  $('#epBlocks').innerHTML = mixes.map((m) => {
    const eps = (d.details || {})[m.mix_id] || [];
    const body = eps.length
      ? eps.map((e) => `<tr>
          <td class="num">第${e.episode_no}集</td>
          <td class="ttl" title="${esc(e.title)}">${esc(e.title)}</td>
          <td class="num">${fmt(e.play_count)}</td>
          <td class="num">${deltaHtml(e.delta)}</td>
          <td class="num">${e.share}%</td>
          <td class="num faint">${e.digg_count || 0}</td>
          <td class="num faint">${e.comment_count || 0}</td>
        </tr>`).join('')
      : '<tr><td colspan="7" class="faint">该合集还没有逐集播放数据'
        + '（通常是分集抓取未成功，下次抓取会补上）</td></tr>';
    return `<details><summary><b>${esc(m.mix_name)}</b>
      <span class="faint"> · ${esc(m.author_name || '')} · 总播放 ${fmt(m.play_vv)}</span></summary>
      <table><thead><tr><th style="width:70px">集数</th><th>标题</th>
      <th style="width:100px">播放量</th><th style="width:96px">本次增量</th>
      <th style="width:82px">占合集比</th><th style="width:74px">点赞</th>
      <th style="width:74px">评论</th></tr></thead><tbody>${body}</tbody></table></details>`;
  }).join('');
}

$('#btnExport').addEventListener('click', async () => {
  const r = await api('/api/export', {});
  if (_apiDocErr(r)) return;
  toast('已导出：' + r.path);
  api('/api/open', { path: r.path });
});
$('#btnDash').addEventListener('click', async () => {
  const r = await api('/api/dashboard', {});
  if (_apiDocErr(r)) return;
  toast('看板已生成并打开');
  api('/api/open', { path: r.path });
});
$('#btnOpenDir').addEventListener('click', async () => {
  const st = await api('/api/state');
  api('/api/open', { path: st.effective_output_dir || st.config.output_dir });
});

function _apiDocErr(r) {
  if (r && r.ok) return false;
  toast('操作失败：' + ((r && r.error) || '未知错误'));
  return true;
}

/* ---------------- 设置 ---------------- */
let cfgTimes = [];

async function loadConfig() {
  const st = await api('/api/state');
  const c = st.config || {};
  cfgTimes = (c.schedule_times || []).slice();
  renderTimes();
  $('#cfgInterval').value = c.interval_seconds;
  $('#cfgRetry').value = c.max_retries;
  $('#cfgChannel').value = c.browser_channel || 'msedge';
  $('#cfgHeadless').checked = !!c.headless;
  $('#cfgCatchup').checked = c.catch_up !== false;
  $('#cfgMixTotal').checked = !!c.fetch_mix_total;
  $('#cfgLvSuccess').value = c.level_success;
  $('#cfgLvHigh').value = c.level_high;
  $('#cfgLvTop').value = c.level_top;
  $('#cfgOutdir').value = c.output_dir || '';
  $('#cfgOutdirHint').textContent = '留空 = 程序目录下的 output（推荐，换电脑不会出错）'
    + (st.effective_output_dir ? '；当前实际使用：' + st.effective_output_dir : '');
  $('#nextrun').textContent = schedText(st);
}

function renderTimes() {
  const box = $('#timeChips');
  if (!cfgTimes.length) {
    box.innerHTML = '<span class="hint">未设置，仅手动抓取</span>';
    return;
  }
  box.innerHTML = cfgTimes.map((t, i) =>
    `<span class="chip"><b>${t}</b><span class="x" onclick="delTime(${i})">×</span></span>`).join('');
}
window.delTime = (i) => { cfgTimes.splice(i, 1); renderTimes(); };

$('#btnAddTime').addEventListener('click', () => {
  const v = $('#newTime').value;
  if (!v) return toast('请选择时间');
  if (cfgTimes.includes(v)) return toast('该时间点已存在');
  cfgTimes.push(v);
  cfgTimes.sort();
  renderTimes();
});

$('#btnSaveCfg').addEventListener('click', async () => {
  const body = {
    schedule_times: cfgTimes,
    interval_seconds: Number($('#cfgInterval').value) || 5,
    max_retries: Number($('#cfgRetry').value) || 3,
    browser_channel: $('#cfgChannel').value.trim() || 'msedge',
    headless: $('#cfgHeadless').checked,
    catch_up: $('#cfgCatchup').checked,
    fetch_mix_total: $('#cfgMixTotal').checked,
    level_success: Number($('#cfgLvSuccess').value) || 20000,
    level_high: Number($('#cfgLvHigh').value) || 500000,
    level_top: Number($('#cfgLvTop').value) || 1000000,
    output_dir: $('#cfgOutdir').value.trim(),
  };
  await api('/api/config', body);
  toast('设置已保存，定时器已按新配置生效');
  loadConfig();
});

$('#btnResetCfg').addEventListener('click', () => {
  cfgTimes = ['08:30'];
  renderTimes();
  $('#cfgInterval').value = 5;
  $('#cfgRetry').value = 3;
  $('#cfgChannel').value = 'msedge';
  $('#cfgHeadless').checked = true;
  $('#cfgCatchup').checked = true;
  $('#cfgMixTotal').checked = true;
  $('#cfgLvSuccess').value = 20000;
  $('#cfgLvHigh').value = 500000;
  $('#cfgLvTop').value = 1000000;
  toast('已填入默认值，记得点「保存设置」');
});

async function loadRuns() {
  const d = await api('/api/runs');
  const runs = d.runs || [];
  if (!runs.length) {
    $('#runsTable').innerHTML = '<div class="empty">还没有运行记录。</div>';
    return;
  }
  $('#runsTable').innerHTML = `<table><thead><tr>
      <th style="width:150px">开始时间</th><th style="width:150px">结束时间</th>
      <th style="width:70px">成功</th><th style="width:70px">失败</th>
      <th style="width:70px">未采集</th><th>说明</th></tr></thead><tbody>
    ${runs.map((r) => `<tr>
      <td class="mono">${esc(r.started_at)}</td><td class="mono">${esc(r.finished_at || '—')}</td>
      <td class="num up">${r.ok || 0}</td><td class="num ${r.failed ? 'down' : 'faint'}">${r.failed || 0}</td>
      <td class="num faint">${r.skipped || 0}</td><td class="faint">${esc(r.message || '')}</td>
    </tr>`).join('')}</tbody></table>`;
}

/* ---------------- 启动 ---------------- */
(async function init() {
  await loadConfig();
  await loadWatchlist();
  await pollRun();
  const st = await api('/api/state');
  if (st.run && st.run.running) startPolling();
})();
