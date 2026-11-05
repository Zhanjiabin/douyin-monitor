(() => {
  'use strict';
  const G=Guanchao,{esc,fmt,timestamp,api}=G,$=s=>document.querySelector(s),form=$('#analyticsFilters');
  const names={play_count:'播放',digg_count:'点赞',comment_count:'评论',collect_count:'收藏',share_count:'分享'};
  const keys=Object.keys(names),week=['周一','周二','周三','周四','周五','周六','周日'];
  const params=new URLSearchParams(location.search);
  let data=null,applied={account:params.get('account')||'',days:['7','30','90'].includes(params.get('days'))?params.get('days'):'30',keyword:params.get('keyword')||''};
  let dimension='trend',display='charts',metric='play_count',page=0,size=20,version=0,charts=[];
  form.elements.days.value=applied.days;form.elements.keyword.value=applied.keyword;
  const raw=n=>n==null?'—':Number(n).toLocaleString('zh-CN'),num=n=>`<td class="number">${raw(n)}</td>`;
  const note=v=>`<small>${esc(v)}</small>`;
  function dispose(){charts.forEach(c=>c.dispose());charts=[];}
  const resize=new ResizeObserver(()=>charts.forEach(c=>c.resize()));resize.observe($('#analyticsContent'));
  function table(headers,rows,cls=''){return `<table class="${cls}"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`;}
  function sampleText(n){return n?'有效作品 '+n+' 条':'无有效样本';}
  function tip(title,lines){return `<div class="analytics-tooltip"><b>${esc(title)}</b>${lines.map(s=>'<div>'+s+'</div>').join('')}</div>`;}
  function chart(host,options){
    const c=echarts.init(host,null,{renderer:'canvas'});
    c.setOption({animation:false,color:['#2f82a8','#37a891','#e5b663','#8e86bf'],textStyle:{fontFamily:'Microsoft YaHei UI, sans-serif',fontSize:11},aria:{enabled:true},...options});charts.push(c);return c;
  }
  const axis={axisLine:{show:false},axisTick:{show:false},axisLabel:{color:'#94a6b3',fontSize:10},splitLine:{lineStyle:{color:'#edf2f6'}}};
  const yaxis={...axis,type:'value',axisLabel:{...axis.axisLabel,formatter:fmt}};
  const tooltip={trigger:'axis',confine:true,backgroundColor:'#fff',borderColor:'#dce6ed',padding:[9,12],extraCssText:'box-shadow:0 4px 16px #203d5710;'};
  function card(id,title,caption,footer=''){return `<section class="analytics-chart-card"><header><h2>${esc(title)}</h2><span>${esc(caption)}</span></header><div class="analytics-chart" id="${id}"></div>${footer?'<p>'+esc(footer)+'</p>':''}</section>`;}
  function empty(host,message){host.className='analytics-chart-empty';host.textContent=message;}
  function trendCharts(){
    const first=data.series.findIndex(r=>r[metric]!=null),last=data.series.findLastIndex(r=>r[metric]!=null);
    const rows=first<0?[]:data.series.slice(first,last+1),valid=rows.filter(r=>r.comparable[metric]!=null),growth=rows.filter(r=>r.growth[metric]!=null);
    $('#analyticsContent').innerHTML=`<div class="analytics-chart-grid">${card('accountTotals','固定样本累计'+names[metric],valid.length?'同作品 · 同口径 · '+valid[0].comparable_samples[metric]+' 条样本':'同作品 · 同口径','保持相同作品样本，避免采集覆盖变化造成假涨跌；每日完整总量见明细。')}${card('accountGrowth','可比作品日增'+names[metric],'相邻两天 · 同作品 · 同口径','仅累加可比作品的非负变化；无可比数据的日期留空。')}${valid.length<2?'<div class="analytics-coverage">当前不足两天固定样本观测。继续跟踪相同作品后将自动形成趋势；每天的完整采集结果可切换「数据明细」查看。</div>':''}</div>`;
    const totals=$('#accountTotals');
    if(valid.length<2){
      totals.className='analytics-point-summary';totals.innerHTML=valid.length?table(['观测日期',names[metric]+'总量','有效作品'],valid.map(r=>`<tr><td>${r.date}</td>${num(r.comparable[metric])}${num(r.comparable_samples[metric])}</tr>`),'analytics-point-table'):'<div class="analytics-chart-empty">所选周期尚无有效'+names[metric]+'记录，或观测日期间没有相同口径的固定样本</div>';
    }else chart(totals,{grid:{top:24,left:62,right:24,bottom:30},tooltip:{...tooltip,formatter:items=>{const r=rows[items[0].dataIndex];return tip(r.date,[names[metric]+' '+raw(r.comparable[metric]),sampleText(r.comparable_samples[metric]),'<small>数据更新 '+esc(timestamp(r.updated_at))+'</small>']);}},xAxis:{...axis,type:'category',boundaryGap:false,data:rows.map(r=>r.date.slice(5))},yAxis:yaxis,series:[{name:names[metric],type:'line',data:rows.map(r=>r.comparable[metric]),connectNulls:false,symbolSize:5,lineStyle:{width:2},areaStyle:{color:'#2f82a80b'}}]});
    if(!growth.length)empty($('#accountGrowth'),'尚无相邻两天的可比记录，继续按计划更新即可积累。');
    else chart($('#accountGrowth'),{grid:{top:24,left:58,right:22,bottom:30},tooltip:{...tooltip,formatter:items=>{const r=rows[items[0].dataIndex];return tip(r.date,['新增'+names[metric]+' '+raw(r.growth[metric]),'可比作品 '+r.growth_samples[metric]+' 条']);}},xAxis:{...axis,type:'category',data:rows.map(r=>r.date.slice(5))},yAxis:yaxis,series:[{name:'日增'+names[metric],type:'bar',barMaxWidth:24,itemStyle:{color:'#37a891',borderRadius:[3,3,0,0]},data:rows.map(r=>r.growth[metric])}]});
  }
  function workCharts(){
    const works=data.works.filter(w=>w[metric]!=null).sort((a,b)=>b[metric]-a[metric]).slice(0,10);
    $('#analyticsContent').innerHTML=`<div class="analytics-chart-grid">${card('accountRanking','作品'+names[metric]+'排行','当前周期最新有效值 · 前十','点击条形查看作品详情及历史数据。')}${card('accountInteractions','互动构成','每条作品取周期内最新有效值','点赞、评论、收藏、分享为不同计数，不代表独立用户数量。')}</div>`;
    if(!works.length)empty($('#accountRanking'),'所选周期没有可排行的'+names[metric]+'数据');
    else {
      const c=chart($('#accountRanking'),{grid:{top:18,left:24,right:65,bottom:28,containLabel:true},tooltip:{...tooltip,trigger:'item',formatter:p=>{const w=works[p.dataIndex];return tip(w.title||w.aweme_id,[names[metric]+' '+raw(w[metric]),'<small>更新 '+esc(timestamp(w.metrics_updated_at))+'</small>']);}},xAxis:{...axis,type:'value',axisLabel:{...axis.axisLabel,formatter:fmt}},yAxis:{...axis,type:'category',inverse:true,splitLine:{show:false},axisLabel:{color:'#6f899b',fontSize:10,width:125,overflow:'truncate'},data:works.map(w=>w.title||w.aweme_id)},series:[{name:names[metric],type:'bar',data:works.map(w=>w[metric]),barMaxWidth:13,itemStyle:{borderRadius:[0,3,3,0]},label:{show:true,position:'right',fontSize:10,color:'#52768e',formatter:p=>fmt(p.value)}}]});
      c.on('click',p=>{if(works[p.dataIndex])G.openWork(works[p.dataIndex].aweme_id);});
    }
    const parts=keys.slice(1).filter(k=>data.interactions[k]!=null).map(k=>({name:names[k],value:data.interactions[k]}));
    if(!parts.some(p=>p.value>0))empty($('#accountInteractions'),'当前周期尚无可展示的互动计数');
    else chart($('#accountInteractions'),{tooltip:{...tooltip,trigger:'item',formatter:p=>tip(p.name,[raw(p.value)+' 次','占已观测互动 '+p.percent+'%'])},legend:{bottom:16,textStyle:{color:'#8197a6',fontSize:11},itemWidth:9,itemHeight:9,icon:'circle'},series:[{name:'互动构成',type:'pie',radius:['44%','65%'],center:['50%','45%'],avoidLabelOverlap:true,label:{formatter:p=>p.name+' '+fmt(p.value),color:'#6e899b',fontSize:11},labelLine:{length:12,length2:9,lineStyle:{color:'#cbdbe5'}},data:parts}]});
  }
  function publicationCharts(){
    $('#analyticsContent').innerHTML=`<div class="analytics-chart-grid">${card('publicationHeat','作品发布时间','北京时间 · 所选周期内发布','颜色表示发布数量。悬停查看该时段的样本与播放中位数。')}${card('publicationMedian','各时段作品播放中位数','按作品发布时间分组','播放量为周期内最新观测，作品年龄与样本量会影响比较。')}</div>`;
    if(!data.publication.length){empty($('#publicationHeat'),'所选周期没有已知发布时间的作品');empty($('#publicationMedian'),'暂无可比较的发布时间样本');return;}
    const heat=data.publication.map(r=>[r.hour,r.weekday,r.count]);
    chart($('#publicationHeat'),{grid:{top:22,left:45,right:24,bottom:55},tooltip:{...tooltip,trigger:'item',formatter:p=>{const [hour,weekday]=p.value,r=data.publication.find(r=>r.hour===hour&&r.weekday===weekday);return tip(week[weekday]+' '+hour+':00—'+hour+':59',['发布 '+r.count+' 条作品','已知播放 '+r.known+' 条','播放中位数 '+raw(r.median_play)]);}},xAxis:{...axis,type:'category',splitArea:{show:true,areaStyle:{color:['#fafcfd','#fff']}},data:Array.from({length:24},(_,i)=>i+'时'),axisLabel:{color:'#94a6b3',fontSize:9,interval:2}},yAxis:{...axis,type:'category',data:week,splitLine:{show:false}},visualMap:{min:0,max:Math.max(1,...heat.map(r=>r[2])),orient:'horizontal',left:'center',bottom:5,itemWidth:9,itemHeight:100,calculable:false,inRange:{color:['#edf5f8','#96c4d6','#2d83a7']},textStyle:{color:'#8da1af',fontSize:9}},series:[{type:'heatmap',data:heat,label:{show:true,fontSize:10},itemStyle:{borderColor:'#fff',borderWidth:2},emphasis:{itemStyle:{borderColor:'#2d83a7',borderWidth:1}}}]});

    if(!data.hours.some(r=>r.median_play!=null)){empty($('#publicationMedian'),'已知发布时间的作品尚无有效播放记录');return;}
    chart($('#publicationMedian'),{grid:{top:24,left:60,right:24,bottom:30},tooltip:{...tooltip,formatter:items=>{const r=data.hours[items[0].dataIndex];return tip(r.hour+':00—'+r.hour+':59',['播放中位数 '+raw(r.median_play),'有效播放 '+r.known+' / '+r.count+' 条作品']);}},xAxis:{...axis,type:'category',data:data.hours.map(r=>r.hour+'时'),axisLabel:{color:'#94a6b3',fontSize:9,interval:2}},yAxis:yaxis,series:[{name:'播放中位数',type:'bar',barMaxWidth:18,itemStyle:{color:'#648eae',borderRadius:[3,3,0,0]},data:data.hours.map(r=>r.median_play)}]});
  }
  function tableData(){
    if(dimension==='trend')return {headers:['日期',...keys.map(k=>names[k]),'可比日增'+names[metric],'有效 / 可比作品','最后数据更新'],rows:[...data.series].reverse(),row:r=>`<tr><td>${r.date}</td>${keys.map(k=>num(r[k])).join('')}${num(r.growth[metric])}<td class="number">${r.samples[metric]} / ${r.growth_samples[metric]}</td><td>${esc(timestamp(r.updated_at))}</td></tr>`,csv:r=>[r.date,...keys.map(k=>r[k]),r.growth[metric],r.samples[metric]+' / '+r.growth_samples[metric],timestamp(r.updated_at)]};
    if(dimension==='works')return {headers:['作品 / 发布时间',...keys.map(k=>names[k]),'上次数据更新','操作'],rows:[...data.works].sort((a,b)=>(b[metric]??-1)-(a[metric]??-1)),row:r=>`<tr><td><button type="button" class="analytics-work" data-open-work="${esc(r.aweme_id)}">${G.picture(r.cover_path)}<span><strong>${esc(r.title||r.aweme_id)}</strong>${note(G.date(r.create_time))}</span></button></td>${keys.map(k=>num(r[k])).join('')}<td>${esc(timestamp(r.metrics_updated_at))}</td><td><button type="button" class="text-link" data-open-work="${esc(r.aweme_id)}">详情</button></td></tr>`,csv:r=>[r.title+' / '+G.date(r.create_time),...keys.map(k=>r[k]),timestamp(r.metrics_updated_at),'https://www.douyin.com/video/'+r.aweme_id]};
    return {headers:['星期','发布时段（北京时间）','发布作品','已知播放作品','播放中位数'],rows:data.publication,row:r=>`<tr><td>${week[r.weekday]}</td><td>${r.hour}:00—${r.hour}:59</td>${num(r.count)}${num(r.known)}${num(r.median_play)}</tr>`,csv:r=>[week[r.weekday],r.hour+':00—'+r.hour+':59',r.count,r.known,r.median_play]};
  }
  function renderTable(){
    const t=tableData();page=Math.min(page,Math.max(0,Math.ceil(t.rows.length/size)-1));
    $('#analyticsContent').innerHTML=`<div class="analytics-table-panel"><div class="analytics-table-scroll">${table(t.headers,t.rows.slice(page*size,(page+1)*size).map(t.row))}</div><div id="analyticsPagination"></div></div>`;
    if(!t.rows.length)$('#analyticsContent tbody').innerHTML=`<tr><td colspan="${t.headers.length}" class="analytics-table-empty">当前条件没有可展示的数据</td></tr>`;
    G.pagination($('#analyticsPagination'),{total:t.rows.length,page,pageSize:size,unit:'条记录',onChange:(p,s)=>{page=p;size=s;renderTable();}});
  }
  function render(){
    dispose();document.querySelectorAll('[data-dimension]').forEach(b=>{const active=b.dataset.dimension===dimension;b.setAttribute('aria-selected',active);b.tabIndex=active?0:-1;});
    document.querySelectorAll('[data-display]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.display===display));
    $('#analyticsMetric').hidden=dimension==='publication';$('#analyticsExport').disabled=!data?.account;
    if(!data?.account){$('#analyticsContent').innerHTML='<div class="analytics-empty">还没有监控账号。<a href="/home">前往工作台添加账号</a></div>';return;}
    $('#analyticsMethod').textContent=data.method;
    if(display==='table')renderTable();else if(dimension==='trend')trendCharts();else if(dimension==='works')workCharts();else publicationCharts();
  }
  async function load(query,initial=false){
    const current=++version;$('#analyticsContent').setAttribute('aria-busy','true');$('#analyticsError').hidden=true;$('#analyticsFeedback').textContent='正在读取';
    try{
      const result=await api('/api/analytics?'+new URLSearchParams(query));if(current!==version)return;
      data=result;applied={account:result.account?.sec_uid||'',days:String(result.days),keyword:result.keyword||''};page=0;
      const draft=form.elements.account.value;
      form.elements.account.innerHTML=result.accounts.map(a=>`<option value="${esc(a.sec_uid)}">${esc(a.nickname||a.note||'未命名账号')}${a.enabled?'':' · 已暂停'}</option>`).join('')||'<option value="">暂无监控账号</option>';
      form.elements.account.value=initial||!draft?applied.account:draft;
      const a=result.account;$('#analyticsAccount').innerHTML=a?`${G.picture(a.avatar_path)}<div><h1>${esc(a.nickname||a.note||'未命名账号')}<span class="role-pill">${G.roles[a.role]||'未分组'}</span></h1><p>${result.start} 至 ${result.end} · 上次数据更新 ${esc(timestamp(result.updated_at))}</p></div><div class="analytics-account-links"><button type="button" class="text-link" data-open-account="${esc(a.sec_uid)}">账号作品</button><a href="https://www.douyin.com/user/${encodeURIComponent(a.sec_uid)}" target="_blank" rel="noreferrer">抖音主页 ↗</a></div>`:'';
      history.replaceState(null,'','/analytics?'+new URLSearchParams(applied));$('#analyticsFeedback').textContent='';render();
    }catch(e){if(current===version){$('#analyticsError').textContent=e.message;$('#analyticsError').hidden=false;$('#analyticsFeedback').textContent='读取失败';}}
    finally{if(current===version)$('#analyticsContent').setAttribute('aria-busy','false');}
  }
  form.onsubmit=e=>{e.preventDefault();load({account:form.elements.account.value,days:form.elements.days.value,keyword:form.elements.keyword.value.trim()});};
  form.onreset=e=>{e.preventDefault();form.elements.days.value='30';form.elements.keyword.value='';const account=data?.accounts.find(a=>a.enabled&&a.role==='own')||data?.accounts.find(a=>a.enabled)||data?.accounts[0];form.elements.account.value=account?.sec_uid||'';form.requestSubmit();};
  form.oninput=()=>{$('#analyticsFeedback').textContent='条件已修改，点击搜索应用';};
  $('#reloadAnalytics').onclick=()=>load(applied);window.addEventListener('gc-data-updated',()=>load(applied));
  document.querySelectorAll('[data-dimension]').forEach(b=>b.onclick=()=>{dimension=b.dataset.dimension;page=0;render();});
  $('.analytics-tabs').onkeydown=e=>{const tabs=[...document.querySelectorAll('[data-dimension]')],i=tabs.indexOf(document.activeElement);if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key)||i<0)return;e.preventDefault();const n=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[n].focus();tabs[n].click();};
  document.querySelectorAll('[data-display]').forEach(b=>b.onclick=()=>{display=b.dataset.display;page=0;render();});
  $('#analyticsMetric').onchange=e=>{metric=e.target.value;page=0;render();};
  $('#analyticsExport').onclick=()=>{if(!data?.account)return;const t=tableData(),lines=[t.headers,...t.rows.map(t.csv)],csv=lines.map(row=>row.map(v=>{let s=String(v??'');if(/^[=+\-@\t\r]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';}).join(',')).join('\r\n'),url=URL.createObjectURL(new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download='观潮-'+(data.account.nickname||'账号')+'-'+({trend:'账号趋势',works:'作品表现',publication:'发布分析'})[dimension]+'-'+data.end+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);};
  load(applied,true);
})();
