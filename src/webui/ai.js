(function(){
 const btn=document.getElementById('generatePlan');
 if(!btn)return;
 btn.addEventListener('click',async function(){
  const niche=document.getElementById('aiNiche').value.trim();
  if(!niche)return;
  btn.disabled=true;
  try{
   const response=await fetch('/api/intel/ai-summary',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({niche:niche,stage:document.getElementById('aiStage').value,format:document.getElementById('aiFormat').value,frequency:Number(document.getElementById('aiFrequency').value)})});
   const result=await response.json();
   if(result.configured&&result.content){
    const box=document.getElementById('aiResult');box.classList.remove('empty');box.textContent=result.content;box.style.whiteSpace='pre-wrap';
   }else if(!result.configured){
    alert('尚未配置模型密钥。当前规则诊断仍可使用；请先设置 OPENAI_API_KEY 后启用模型总结。');
   }else{
    alert(result.error||result.message||'AI 分析失败');
   }
  }catch(error){alert('AI 分析请求失败：'+error.message)}
  finally{btn.disabled=false}
 });
})();
