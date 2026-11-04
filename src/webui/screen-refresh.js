(() => {
  'use strict';
  const $=s=>document.querySelector(s),selected=new Set();
  let preview=null,sequence=0,submitting=false,jobVersion=0;
  const original=renderRank;
  function update(){const boxes=[...document.querySelectorAll('[data-select-work]')],count=boxes.filter(e=>selected.has(e.dataset.selectWork)).length;boxes.forEach(e=>e.checked=selected.has(e.dataset.selectWork));$('#selectPage').checked=boxes.length>0&&count===boxes.length;$('#selectPage').indeterminate=count>0&&count<boxes.length;$('#selectedWorkCount').textContent='已选 '+selected.size+' 条';$('#refreshSelected').disabled=!selected.size||submitting;$('#clearSelection').hidden=!selected.size;}
  renderRank=function(){original();document.querySelectorAll('#rankBody tr').forEach(row=>{const aid=row.querySelector('[data-video]')?.dataset.video;if(!aid){const cell=row.querySelector('td');if(cell)cell.colSpan=11;return;}const td=document.createElement('td');td.className='selection-col';td.innerHTML=`<input type="checkbox" data-select-work="${aid}" aria-label="选择作品">`;row.prepend(td);});update();};
  $('#selectPage').onchange=e=>{document.querySelectorAll('[data-select-work]').forEach(x=>e.target.checked?selected.add(x.dataset.selectWork):selected.delete(x.dataset.selectWork));update();};
  document.addEventListener('change',e=>{const id=e.target.dataset.selectWork;if(!id)return;e.target.checked?selected.add(id):selected.delete(id);update();});
  $('#clearSelection').onclick=()=>{selected.clear();update();};
  function request(){return {video_ids:[...selected],playback_method:$('#refreshMethod').value};}
  async function quote(){const seq=++sequence;preview=null;$('#confirmRefresh').disabled=true;$('#refreshQuote').textContent='正在预览请求数';$('#refreshError').textContent='';try{const body=request(),p=await api('/api/workbench/refresh-plan',body);if(seq!==sequence)return;preview={...p,body};$('#refreshQuote').textContent=`${p.units} 条作品 · ${p.calls} 次请求 · ${body.playback_method==='single'?'单作品接口':'批量接口'}${p.reason?'\n'+p.reason:''}`;$('#confirmRefresh').disabled=!p.allowed;}catch(e){if(seq===sequence)$('#refreshError').textContent=e.message;}}
  $('#refreshSelected').onclick=async()=>{if(!selected.size)return;$('#refreshMethod').value='single';$('#refreshDialog').showModal();$('#refreshSelectionList').innerHTML=[...selected].map(id=>{const v=data.videos.find(v=>v.aweme_id===id);return `<div>${esc(v?.title||id)}</div>`;}).join('');try{const cfg=(await api('/api/playback/status')).settings;$('#refreshMethod option[value=batch]').disabled=!cfg.allow_batch_statistics;await quote();}catch(e){$('#refreshError').textContent=e.message;}};
  $('#refreshMethod').onchange=quote;
  $('#closeRefresh').onclick=()=>{$('#refreshDialog').close();sequence++;};
  async function pollJob(id,version,count=0){if(version!==jobVersion)return;try{const j=await api('/api/douk/jobs/'+id);if(['queued','running'].includes(j.status)){if(count<180)setTimeout(()=>pollJob(id,version,count+1),2000);return;}await refresh();toast(j.status==='completed'?'选中作品的数据已刷新':j.message||'刷新结束，请查看任务记录');}catch(e){toast(e.message);}}
  $('#confirmRefresh').onclick=async()=>{if(!preview?.allowed||submitting)return;submitting=true;$('#confirmRefresh').disabled=true;try{const j=await api('/api/workbench/refresh',{...preview.body,confirmed:true,fingerprint:preview.fingerprint});$('#refreshDialog').close();selected.clear();update();$('#refreshTaskLink').href='/collect?job='+j.id;$('#refreshTaskLink').hidden=false;toast('刷新任务已加入队列');pollJob(j.id,++jobVersion);}catch(e){$('#refreshError').textContent=e.message;await quote();}finally{submitting=false;update();}};
  renderRank();
})();
