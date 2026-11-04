(() => {
  'use strict';
  const form=document.getElementById('loginForm'),error=document.getElementById('loginError'),password=form.elements.password,show=document.getElementById('showPassword');
  function next(){const value=new URLSearchParams(location.search).get('next')||'/home';try{const url=new URL(value,location.origin);return value.startsWith('/')&&!value.startsWith('//')&&url.origin===location.origin&&!value.includes('\\')?url.pathname+url.search+url.hash:'/home';}catch{return '/home';}}
  show.onclick=()=>{const visible=password.type==='password';password.type=visible?'text':'password';show.setAttribute('aria-label',visible?'隐藏密码':'显示密码');show.setAttribute('aria-pressed',String(visible));};
  let saving=false;
  form.onsubmit=async event=>{
    event.preventDefault();if(saving)return;saving=true;const button=form.querySelector('[type=submit]');button.disabled=true;button.textContent='登录中…';error.hidden=true;
    try{const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:form.elements.username.value.trim(),password:password.value,next:next()})});const result=await response.json();if(!response.ok)throw Error(result.error||'登录失败，请重试');location.replace(result.next||'/home');}
    catch(e){error.textContent=e.message;error.hidden=false;password.focus();}
    finally{saving=false;button.disabled=false;button.textContent='登录';}
  };
})();
