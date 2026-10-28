'use strict';
const $ = id => document.getElementById(id);
const el = (tag, text, cls) => {const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls;return n;};
let workspace='', generation=0, selectedTask='', renderedTask='', docs=[], pending=null, submitting=false;
const states={QUEUED:'排队中',RUNNING:'处理中',SUCCEEDED:'已完成',FAILED:'失败'};
async function api(path, options={}) {
  const r=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
  if(r.status===204)return null;
  const body=await r.json();
  if(!r.ok)throw new Error(body.message||body.error||`请求失败 (${r.status})`);
  return body;
}
function notice(message,error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error);}
function renderConditions(){
  const remote=$('strategy').value!=='bm25'||$('mode').value==='live';
  $('consent-wrap').hidden=!remote;
  $('cost-note').textContent=remote?'语义/混合检索会调用嵌入模型；模型整理还会调用生成模型。实际调用情况保存在结果中。':'关键词检索与原文摘录在本机完成，不调用模型。';
  $('submit').disabled=!workspace||!docs.length||submitting||(remote&&!$('consent').checked);
}
async function loadWorkspaces(preferred){
  const data=await api('/workspaces');$('workspace').replaceChildren(new Option('请选择项目',''));
  for(const w of data.workspaces)$('workspace').append(new Option(w.name,w.id));
  if(preferred&&data.workspaces.some(w=>w.id===preferred))$('workspace').value=preferred;
  await changeWorkspace();
}
async function changeWorkspace(){
  const rev=++generation;workspace=$('workspace').value;selectedTask='';renderedTask='';pending=null;docs=[];
  $('consent').checked=false;$('documents').replaceChildren();$('tasks').replaceChildren();$('answer').replaceChildren(el('p','选择任务后查看原文与来源。','empty'));
  $('file').disabled=!workspace;$('sample').disabled=!workspace;$('result-status').textContent='等待提问';renderConditions();
  localStorage.setItem('lablens.workspace',workspace);
  if(!workspace){$('revision').textContent='先创建或选择一个项目';return;}
  try{await loadDocuments(rev);await loadTasks(rev);notice('资料保存在当前项目中。');}catch(e){if(rev===generation)notice(e.message,true);}
}
async function loadDocuments(rev=generation){
  const data=await api(`/workspaces/${workspace}/documents`);if(rev!==generation)return;
  docs=data.documents;$('revision').textContent=`${docs.length} 份资料 · 版本 ${data.revision}`;$('documents').replaceChildren();
  for(const d of docs){const row=el('div',undefined,'document');row.append(el('span',d.title));const b=el('button','删除');b.setAttribute('aria-label',`删除 ${d.title}`);b.onclick=async()=>{b.disabled=true;try{await api(`/workspaces/${workspace}/documents/${encodeURIComponent(d.id)}`,{method:'DELETE'});if(rev===generation){pending=null;$('consent').checked=false;await loadDocuments(rev);notice('资料已删除。新任务使用新版本，历史任务保留原始快照。');}}catch(e){notice(e.message,true);b.disabled=false;}};row.append(b);$('documents').append(row);}
  renderConditions();
}
async function loadTasks(rev=generation){
  if(!workspace)return;
  const data=await api(`/workspaces/${workspace}/tasks`);if(rev!==generation)return;
  $('tasks').replaceChildren();
  for(const t of data.tasks){const b=el('button',undefined,'task');b.append(el('span',t.request.question),el('small',states[t.status]||t.status));b.onclick=()=>{selectedTask=t.id;renderTask(t);};$('tasks').append(b);if(t.id===selectedTask&&renderedTask!==`${t.id}:${t.status}`)renderTask(t);}
  if(!data.tasks.length)$('tasks').append(el('p','暂无任务记录。','small'));
}
function renderTask(t){
  renderedTask=`${t.id}:${t.status}`;
  $('result-status').textContent=`${states[t.status]||t.status} · 资料版本 ${t.snapshot_revision}`;$('answer').replaceChildren();
  if(t.status==='FAILED'){$('answer').append(el('p',t.error_message||'任务失败，可查看运行记录。','error'));if(t.usage_unknown)$('answer').append(el('p','中断前的模型用量可能未知；未自动重试。','small'));}
  if(!t.result){if(t.status!=='FAILED')$('answer').append(el('p',t.status==='RUNNING'?'正在查找资料…':'任务已保存，正在等待处理。','empty'));return;}
  const r=t.result;
  if(r.status==='refused')$('result-status').textContent+=' · 模型拒答';
  $('answer').append(el('div',r.status==='excerpts'?`找到 ${(r.citations||[]).length} 段相关原文。`:typeof r.answer==='string'?r.answer:JSON.stringify(r.answer), 'answer-text'));
  if(r.status==='excerpts')$('answer').append(el('p','以下为原文摘录，尚未判断这些资料是否完整回答问题。','small'));
  if(r.status==='refused')$('answer').append(el('p','模型认为证据不足。以下是本次找到的原文，便于核对；拒答也可能有误。','small'));
  for(const c of (r.status==='refused'?(r.retrieval?.hits||[]):r.citations||[])){const box=el('div',undefined,'citation');box.append(el('strong',c.title||c.document_id),el('p',c.text),el('small',`${c.source} · ${c.start}–${c.end}`));$('answer').append(box);}
  const detail=el('details');detail.append(el('summary','查看本次调用与检索记录'));detail.append(el('pre',JSON.stringify({task_id:t.id,cache_hit:t.cache_hit,worker_ms:t.worker_ms,execution_usage:t.execution_usage||r.usage,origin_usage:r.origin_usage,retrieval:r.retrieval},null,2)));$('answer').append(detail);
}
$('create-form').onsubmit=async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;try{const w=await api('/workspaces',{method:'POST',body:JSON.stringify({name:$('new-name').value.trim()})});$('new-name').value='';await loadWorkspaces(w.id);}catch(x){notice(x.message,true);}finally{b.disabled=false;}};
$('workspace').onchange=changeWorkspace;
$('file').onchange=async()=>{const f=$('file').files[0];if(!f||!workspace)return;const rev=generation,w=workspace;$('file').disabled=true;try{if(f.size>200000)throw new Error('请选择小于 200 KB 的文本文件。');const text=await f.text();if(text.length>20000)throw new Error('每份资料最多 20,000 个字符，请拆分后导入。');await api(`/workspaces/${w}/documents/${crypto.randomUUID()}`,{method:'PUT',body:JSON.stringify({title:f.name,text,source:f.name})});if(rev===generation){pending=null;$('consent').checked=false;await loadDocuments(rev);notice('资料已保存。');}}catch(e){if(rev===generation)notice(e.message,true);}finally{$('file').value='';$('file').disabled=!workspace;}};
$('sample').onclick=async()=>{const rev=generation,w=workspace;pending=null;$('consent').checked=false;renderConditions();$('sample').disabled=true;try{const response=await fetch('/example-corpus.json');if(!response.ok)throw new Error('示例读取失败');const data=await response.json();let count=0;for(const d of data.documents){await api(`/workspaces/${w}/documents/${encodeURIComponent(d.id)}`,{method:'PUT',body:JSON.stringify({title:d.title,text:d.text,source:d.source})});count++;if(rev===generation)notice(`正在导入 ${count}/${data.documents.length} 份公开资料…`);}if(rev===generation){pending=null;$('consent').checked=false;await loadDocuments(rev);notice(`已保存 ${count} 份公开资料。`);}}catch(e){if(rev===generation)notice(e.message,true);}finally{if(rev===generation)await loadDocuments(rev);$('sample').disabled=!workspace;}};
$('question-form').onsubmit=async e=>{e.preventDefault();if(submitting||$('submit').disabled)return;submitting=true;renderConditions();const rev=generation,w=workspace,body=JSON.stringify({question:$('question').value.trim(),strategy:$('strategy').value,mode:$('mode').value,top_k:5});if(!pending||pending.body!==body||pending.workspace!==w)pending={body,workspace:w,key:crypto.randomUUID()};try{const t=await api(`/workspaces/${w}/tasks`,{method:'POST',headers:{'Idempotency-Key':pending.key},body});if(rev===generation){selectedTask=t.id;renderTask(t);notice(t.reused?'已找到同一请求的任务。':'任务已保存，可以继续浏览资料。');await loadTasks(rev);}}catch(e){if(rev===generation)notice(e.message,true);}finally{submitting=false;renderConditions();}};
for(const id of ['strategy','mode','question'])$(id).addEventListener('input',()=>{pending=null;if(id!=='question')$('consent').checked=false;renderConditions();});
$('consent').onchange=renderConditions;
$('refresh').onclick=()=>loadTasks().catch(e=>notice(e.message,true));
async function systemStatus(){try{const s=await api('/system/status');$('system').textContent=JSON.stringify(s,null,2);$('connection').textContent=s.redis?.degraded?'已连接 · 缓存暂不可用':'本地服务已连接';$('connection-dot').classList.add('online');}catch(e){$('connection').textContent='本地服务暂不可用';$('connection-dot').classList.remove('online');}}
loadWorkspaces(localStorage.getItem('lablens.workspace')).catch(e=>notice(e.message,true));systemStatus();
setInterval(()=>{if(!document.hidden){loadTasks().catch(()=>{});systemStatus();}},2500);
