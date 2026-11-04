(() => {
  const engine=document.documentElement.dataset.gcEngine;
  const origin='http://127.0.0.1:8795';
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let context=null,lastText=null;
  function fill(text,force=false){const input=engine==='vox'?document.querySelector('#prompt'):document.querySelector('.chat-input-field');if(!input||!text||(!force&&input.value))return false;Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,text);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));lastText=text;return true}
  function mount(){if(!context)return;if(context.text&&context.text!==lastText)fill(context.text,context.force);const holder=engine==='remix'?document.querySelector('[class*="min-h-[60vh]"]'):engine==='vox'?document.querySelector('.new-study:not(.has-plan)'):null;if(!holder||!context.items?.length)return;if(holder.querySelector('#gc-worklist'))return;const list=document.createElement('div');list.id='gc-worklist';list.innerHTML=`<h2>监控作品</h2><table><thead><tr><th>作品</th><th>账号</th><th>播放量</th></tr></thead><tbody>${context.items.slice(0,14).map(v=>`<tr><td><button data-context-id="${esc(v.id)}" title="${esc(v.title)}">${esc(v.title||v.id)}</button></td><td>${esc(v.account)}</td><td>${v.plays==null?'—':Number(v.plays).toLocaleString('zh-CN')}</td></tr>`).join('')}</tbody></table>`;holder.append(list);holder.setAttribute('data-gc-context-list','');list.onclick=e=>{const b=e.target.closest('[data-context-id]');if(b)fill('https://www.douyin.com/video/'+b.dataset.contextId,true)}}
  addEventListener('message',e=>{if(e.origin!==origin||e.source!==parent||e.data?.type!=='observatory-context')return;context=e.data;mount()});
  function ready(){if(parent!==window&&['remix','vox'].includes(engine))parent.postMessage({type:'observatory-ready',engine},origin)}
  addEventListener('DOMContentLoaded',ready);if(document.readyState!=='loading')ready();
  new MutationObserver(()=>mount()).observe(document.documentElement,{childList:true,subtree:true});
})();
