(() => {
  const current = document.body.dataset.app;
  const paths = {
    grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
    users:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-4M16 3a4 4 0 0 1 0 8',
    chart:'M3 3v18h18M7 15v-4M12 15V6M17 15V9',
    text:'M5 3h14v18H5zM8 8h8M8 12h8M8 16h4',
    task:'M9 5h12M9 12h12M9 19h12M3 5h1M3 12h1M3 19h1',
    media:'M4 3h16v18H4zM4 8h16M4 16h16M8 3v5M16 3v5M8 16v5M16 16v5',
    image:'M3 3h18v18H3zM3 17l6-6 4 4 3-3 5 5M8 7h.1',
    chat:'M21 11a8 8 0 0 1-8 8H6l-4 3V11a9 9 0 0 1 19 0zM7 9h9M7 13h6',
    source:'M3 5c0-4 18-4 18 0v14c0 4-18 4-18 0zM3 5c0 4 18 4 18 0M3 12c0 4 18 4 18 0',
    settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2'
  };
  const nav = [
    ['/home','工作台','grid'],
    ['/screen#overview','作品监控','grid','overview'],
    ['/research','内容研究','text'], ['/collect','采集中心','task'], ['/assets','素材库','media']
  ];
  const svg = key => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[key]}"/></svg>`;
  const sidebar = document.querySelector('aside.sidebar');
  sidebar.classList.add('gc-sidebar'); sidebar.id='appNavigation';
  sidebar.innerHTML=`<a class="gc-brand" href="/home"><div class="gc-mark"><i></i><i></i><i></i></div><span>观潮</span></a><nav class="gc-nav" aria-label="主导航">${nav.map(([href,label,key,page],i)=>`<a href="${href}" ${page&&current==='screen'?`data-page="${page}"`:''} class="${i===2?'gc-nav-break':''}">${svg(key)}<span>${label}</span></a>`).join('')}</nav><div class="gc-sidebar-footer"><a class="gc-settings" href="/settings">${svg('settings')}<span>设置</span></a>${current==='screen'?'<button id="methodButton">指标口径</button>':''}${current==='manage'?'<span id="alertBadge" hidden></span>':''}</div>`;
  const menu=document.createElement('button'); menu.type='button'; menu.className='gc-menu';
  menu.setAttribute('aria-label','打开导航');menu.setAttribute('aria-controls','appNavigation');menu.setAttribute('aria-expanded','false');menu.innerHTML='<span></span><span></span><span></span>';
  document.body.append(menu);
  const backdrop=document.createElement('button');backdrop.className='gc-backdrop';backdrop.setAttribute('aria-label','关闭导航');document.body.append(backdrop);
  function close(){document.body.classList.remove('gc-nav-open');menu.setAttribute('aria-expanded','false')}
  menu.onclick=()=>{const open=document.body.classList.toggle('gc-nav-open');menu.setAttribute('aria-expanded',String(open))};backdrop.onclick=close;
  document.addEventListener('keydown',e=>{if(e.key==='Escape')close()});
  sidebar.addEventListener('click',e=>{if(e.target.closest('a'))close()});
  function selected(){let target=location.pathname+location.hash;if(location.pathname==='/screen'&&!location.hash)target='/screen#overview';if(location.pathname==='/settings'||['/workspace','/studio','/manage','/tikhub'].includes(location.pathname))target='/settings';sidebar.querySelectorAll('.gc-nav a,.gc-settings').forEach(a=>{const on=a.getAttribute('href')===target;a.classList.toggle('is-active',on);if(on)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current')})}
  addEventListener('hashchange',selected);addEventListener('popstate',selected);addEventListener('gc-route-change',selected);selected();
  const originalFetch=window.fetch.bind(window);
  window.fetch=async(...args)=>{const response=await originalFetch(...args);if(response.status===401){location.replace('/login?next='+encodeURIComponent(location.pathname+location.search+location.hash));}return response;};
  originalFetch('/api/auth/session').then(r=>r.json()).then(session=>{
    if(!session.enabled||!session.authenticated)return;
    const row=document.createElement('div');row.className='gc-user-session';
    const name=document.createElement('span');name.textContent=session.username;
    const logout=document.createElement('button');logout.type='button';logout.textContent='退出登录';
    logout.onclick=async()=>{logout.disabled=true;try{const response=await originalFetch('/api/auth/logout',{method:'POST'});if(!response.ok)throw Error();location.replace('/login');}catch{logout.disabled=false;logout.textContent='重试退出';}};
    row.append(name,logout);sidebar.querySelector('.gc-sidebar-footer').append(row);
  }).catch(()=>{});
})();
