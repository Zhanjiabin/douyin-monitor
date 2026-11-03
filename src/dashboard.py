"""生成单文件 HTML 看板：数据内联，双击即开，无需联网。"""
import html
import json
from datetime import datetime
from pathlib import Path

import analyze
from config import load_config, resolve_output_dir


def _e(s):
    return html.escape(str(s if s is not None else ""))


def _fmt(n):
    if n is None:
        return "—"
    try:
        n = int(n)
    except Exception:
        return "—"
    if n >= 100000000:
        return f"{n/100000000:.2f}亿"
    if n >= 10000:
        return f"{n/10000:.1f}万"
    return f"{n:,}"


def _delta_html(d):
    if d is None:
        return '<span class="faint">—</span>'
    if d > 0:
        return f'<span class="up">+{d:,}</span>'
    if d < 0:
        return f'<span class="down">{d:,}</span>'
    return '<span class="faint">0</span>'


def build_html(cfg=None):
    cfg = cfg or load_config()
    ov = analyze.overview(cfg)
    fd = ov.get("fetch_date") or "暂无数据"

    max_play = max([(r.get("play_vv") or 0) for r in ov["mixes"]] or [1]) or 1

    # 合集卡片
    cards = []
    for r in ov["mixes"]:
        pv = r.get("play_vv") or 0
        w = round(pv / max_play * 100, 1)
        lv = r.get("level") or 0
        lv_cls = f" lv{lv}" if lv else ""
        badge = (f'<span class="ctag lv{lv}">{_e(r.get("level_label"))}</span>'
                 if r.get("level_label") else "")
        pub = r.get("first_publish_at") or "—"
        cards.append(f"""
        <div class="card{lv_cls}" data-play="{pv}" data-date="{_e(r.get('first_publish_at') or '')}">
          <div class="cname" title="{_e(r.get('mix_name'))}">{_e(r.get('mix_name'))}</div>
          <div class="cacct">{_e(r.get('account'))} · 首发 {pub} · {r.get('episode_count') or 0} 集</div>
          <div class="crow"><span class="cplay">{_fmt(pv)}</span>{badge}{_delta_html(r.get('delta'))}</div>
          <div class="bar"><i style="width:{w}%"></i></div>
          <div class="cmeta">收藏 {r.get('collect_vv') or 0} · 更新至第 {r.get('updated_to_episode') or '-'} 集 · 分集求和校验 {_fmt(r.get('play_sum_calc'))}</div>
        </div>""")

    labels = ov.get("labels") or {}
    legend_html = "".join(
        f'<span><i style="background:{c}"></i>{_e(t)}</span>'
        for c, t in (("#4c9aff", labels.get(1) or "达标"),
                     ("#d98324", labels.get(2) or ""),
                     ("#e5484d", labels.get(3) or "")) if t)

    # 分集明细
    ep_blocks = []
    for r in ov["mixes"]:
        det = analyze.mix_detail(r["mix_id"], r)
        rows = []
        for e in det["episodes"]:
            rows.append(f"""<tr>
              <td class="num">第{e.get('episode_no')}集</td>
              <td class="ttl">{_e(str(e.get('title'))[:44])}</td>
              <td class="num">{_fmt(e.get('play_count'))}</td>
              <td class="num">{_delta_html(e.get('delta'))}</td>
              <td class="num">{e.get('share')}%</td>
              <td class="num faint">{e.get('digg_count') or 0}</td>
              <td class="num faint">{e.get('comment_count') or 0}</td>
              <td class="num faint">{e.get('collect_count') or 0}</td>
            </tr>""")
        eps = "".join(rows) or '<tr><td colspan="8" class="faint">暂无分集数据（该合集本批未逐集采集）</td></tr>'
        ep_blocks.append(f"""
        <details>
          <summary><b>{_e(r.get('mix_name'))}</b>
            <span class="faint"> · {_e(r.get('account'))} · 总播放 {_fmt(r.get('play_vv'))}</span>
          </summary>
          <table>
            <thead><tr><th>集数</th><th>标题</th><th>播放量</th><th>昨日增量</th>
            <th>占合集比</th><th>点赞</th><th>评论</th><th>收藏</th></tr></thead>
            <tbody>{eps}</tbody>
          </table>
        </details>""")

    accounts = "".join(
        f"""<tr><td>{_e(a['account'])}</td><td class="num">{a['mixes']}</td>
        <td class="num">{_fmt(a['play'])}</td><td class="num">{_delta_html(a['delta'])}</td></tr>"""
        for a in ov["accounts"]
    ) or '<tr><td colspan="4" class="faint">暂无数据</td></tr>'

    # 多日趋势（每个合集一条线，数据点少时用文字表）
    trend_rows = []
    for r in ov["mixes"]:
        tr = analyze.store.mix_trend(r["mix_id"])
        if len(tr) >= 2:
            pts = " → ".join(_fmt(t.get("play_vv")) for t in tr[-7:])
            trend_rows.append(
                f"<tr><td>{_e(r.get('mix_name'))}</td><td class='num'>{pts}</td></tr>")
    trend_html = ("<table><thead><tr><th>合集</th><th>最近采集日播放量（旧 → 新）</th></tr></thead>"
                  f"<tbody>{''.join(trend_rows)}</tbody></table>") if trend_rows \
        else '<p class="faint">只有一天数据，明天再来看趋势。</p>'

    first_run = not ov.get("prev_date")
    delta_stat = ('<div class="v faint">—</div><div class="k">首次采集，无可比数据</div>'
                  if first_run else f'<div class="v">{_delta_html(ov["total_delta"])}</div>')
    stop_stat = ('<div class="v faint">—</div><div class="k">需至少两天数据</div>'
                 if first_run else
                 f'<div class="v">{ov["stop_count"]}</div><div class="k">连续两次采集零增长</div>')
    date_note = (f'数据日期 {_e(fd)} · 首次采集，明天第二次抓取后开始有日增对比'
                 if first_run else
                 f'数据日期 {_e(fd)} · 对比 {_e(str(ov.get("prev_date")))}')

    data_json = json.dumps({
        "fetch_date": fd, "prev_date": ov.get("prev_date"),
        "total_play": ov["total_play"], "total_delta": ov["total_delta"],
        "mix_count": ov["mix_count"], "stop_count": ov["stop_count"],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>抖音合集播放量看板 · {_e(fd)}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#191919;color:#ebebeb;font:13px/1.6 "Microsoft YaHei","PingFang SC",system-ui,sans-serif;padding:24px 28px 48px}}
h1{{font-size:17px;font-weight:500;margin-bottom:4px}}
.sub{{color:#6f6f6f;font-size:12px;margin-bottom:20px}}
.stats{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:22px}}
.stat{{background:#232323;border:1px solid #373737;border-radius:10px;padding:13px 20px;min-width:158px}}
.stat .k{{font-size:12px;color:#9b9b9b;margin-bottom:3px}}
.stat .v{{font-size:21px;font-weight:500}}
.up{{color:#e5484d}} .down{{color:#30a46c}} .faint{{color:#6f6f6f}}
h2{{font-size:14px;font-weight:500;margin:26px 0 12px;padding-left:9px;border-left:3px solid #4c9aff}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}}
.card{{background:#232323;border:1px solid #373737;border-radius:10px;padding:14px}}
.cname{{font-size:13.5px;font-weight:500;margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.cacct{{font-size:11.5px;color:#6f6f6f;margin-bottom:9px}}
.crow{{display:flex;align-items:baseline;gap:10px;margin-bottom:8px}}
.cplay{{font-size:19px;font-weight:500;font-family:ui-monospace,Consolas,monospace}}
.bar{{height:5px;background:#2b2b2b;border-radius:3px;overflow:hidden;margin-bottom:7px}}
.bar>i{{display:block;height:100%;background:#4c9aff}}
.cmeta{{font-size:11.5px;color:#6f6f6f}}
.card.lv1{{border-left:4px solid #4c9aff}}
.card.lv2{{border-left:4px solid #d98324}}
.card.lv3{{border-left:4px solid #e5484d}}
.ctag{{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;margin-left:7px;vertical-align:middle}}
.ctag.lv1{{background:rgba(76,154,255,.15);color:#8ab8ff}}
.ctag.lv2{{background:rgba(217,131,36,.16);color:#e5a95f}}
.ctag.lv3{{background:rgba(229,72,77,.16);color:#ff8186}}
.sortbar{{display:flex;align-items:center;gap:10px;margin:10px 0 12px;flex-wrap:wrap}}
.sortbar select{{background:#191919;border:1px solid #373737;border-radius:6px;color:#ebebeb;padding:5px 10px;font-size:12.5px;font-family:inherit}}
.sortbar .hint{{color:#6f6f6f;font-size:12px}}
.legend{{display:flex;gap:12px;align-items:center;margin-left:auto;flex-wrap:wrap}}
.legend i{{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:middle}}
.legend span{{font-size:12px;color:#9b9b9b}}
table{{width:100%;border-collapse:collapse;font-size:12.5px;background:#232323;border:1px solid #373737;border-radius:10px;overflow:hidden}}
th{{text-align:left;padding:9px 12px;color:#9b9b9b;font-weight:400;font-size:12px;border-bottom:1px solid #373737;white-space:nowrap}}
td{{padding:9px 12px;border-bottom:1px solid #2e2e2e}}
tr:last-child td{{border-bottom:none}}
tbody tr:hover{{background:#282828}}
.num{{font-family:ui-monospace,Consolas,monospace;font-size:12.5px}}
.ttl{{color:#c9c9c9;max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
details{{background:#232323;border:1px solid #373737;border-radius:10px;margin-bottom:10px;overflow:hidden}}
details summary{{padding:12px 14px;cursor:pointer;font-size:13px;list-style:none}}
details summary::-webkit-details-marker{{display:none}}
details summary::before{{content:"▸ ";color:#4c9aff}}
details[open] summary::before{{content:"▾ "}}
details table{{border:none;border-radius:0;border-top:1px solid #373737}}
.footer{{margin-top:30px;color:#5c5c5c;font-size:11.5px}}
</style></head><body>
<h1>抖音合集播放量看板</h1>
<div class="sub">{date_note}</div>

<div class="stats">
  <div class="stat"><div class="k">追踪合集</div><div class="v">{ov['mix_count']}</div></div>
  <div class="stat"><div class="k">总播放量</div><div class="v">{_fmt(ov['total_play'])}</div></div>
  <div class="stat"><div class="k">本次总增量</div>{delta_stat}</div>
  <div class="stat"><div class="k">停涨合集</div>{stop_stat}</div>
</div>

<h2>合集排行</h2>
<div class="sortbar">
  <span class="hint">排序</span>
  <select id="sortSel">
    <option value="play_desc">播放量 从高到低</option>
    <option value="play_asc">播放量 从低到高</option>
    <option value="date_desc">发布日期 最新在前</option>
    <option value="date_asc">发布日期 最早在前</option>
  </select>
  <span class="legend">{legend_html}</span>
</div>
<div class="cards" id="cards">{''.join(cards) or '<p class="faint">暂无数据，先跑一次抓取。</p>'}</div>

<h2>账号汇总</h2>
<table><thead><tr><th>账号</th><th>合集数</th><th>总播放量</th><th>本次增量</th></tr></thead>
<tbody>{accounts}</tbody></table>

<h2>分集明细</h2>
{''.join(ep_blocks) or '<p class="faint">暂无数据</p>'}

<h2>历史趋势</h2>
{trend_html}

<div class="footer">生成时间 <span id="gen"></span> · 本页数据内联，可离线打开、可直接转发</div>
<script>
var D = {data_json};
document.getElementById('gen').textContent = D.generated_at;
(function(){{
  var box = document.getElementById('cards');
  var sel = document.getElementById('sortSel');
  if (!box || !sel) return;
  sel.onchange = function(){{
    var p = this.value.split('_');
    var k = p[0], d = p[1];
    var items = Array.prototype.slice.call(box.children);
    items.sort(function(a, b){{
      if (k === 'play') {{
        return (Number(a.dataset.play) - Number(b.dataset.play)) * (d === 'desc' ? -1 : 1);
      }}
      var av = a.dataset.date || '', bv = b.dataset.date || '';
      if (!av && !bv) return 0;
      if (!av) return 1;
      if (!bv) return -1;
      return (av < bv ? -1 : av > bv ? 1 : 0) * (d === 'desc' ? -1 : 1);
    }});
    items.forEach(function(el){{ box.appendChild(el); }});
  }};
}})();
</script>
</body></html>"""


def export_dashboard(cfg=None):
    cfg = cfg or load_config()
    out = resolve_output_dir(cfg) / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(cfg), encoding="utf-8")
    return str(out)
