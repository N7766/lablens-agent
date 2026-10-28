'use strict';
const $ = id => document.getElementById(id);
const el = (tag, text, cls) => {const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls;return n;};
let workspace='', workspaceRevision=null, approvedRevision=null, generation=0, selectedTask='', renderedTask='', docs=[], pending=null, submitting=false;
const states={QUEUED:'排队中',RUNNING:'处理中',SUCCEEDED:'已完成',FAILED:'失败'};
async function api(path, options={}) {
  const r=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
  if(r.status===204)return null;
  const body=await r.json();
  if(!r.ok){const error=new Error(body.message||body.error||`请求失败 (${r.status})`);error.code=body.error;throw error;}
  return body;
}
function notice(message,error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error);}
function invalidateConsent(){approvedRevision=null;$('consent').checked=false;}
function renderConditions(){
  const remote=$('strategy').value!=='bm25'||$('mode').value!=='extractive';
  $('consent-wrap').hidden=!remote;
  $('cost-note').textContent=$('mode').value==='agent'?'Agent 会调用模型自行选择查询与核验工具，再用同一模型复核报告；最多 6 次决策和 1 次复核。语义检索可能另有嵌入费用。':remote?'语义/混合检索会调用嵌入模型；模型整理还会调用生成模型。实际调用情况保存在结果中。':'关键词检索与原文摘录在本机完成，不调用模型。';
  $('submit').disabled=!workspace||!docs.length||submitting||(remote&&(!$('consent').checked||approvedRevision!==workspaceRevision));
  const retry=$('prepare-retry');if(retry)retry.disabled=submitting;
}
async function loadWorkspaces(preferred){
  const data=await api('/workspaces');$('workspace').replaceChildren(new Option('请选择项目',''));
  for(const w of data.workspaces)$('workspace').append(new Option(w.name,w.id));
  if(preferred&&data.workspaces.some(w=>w.id===preferred))$('workspace').value=preferred;
  await changeWorkspace();
}
async function changeWorkspace(){
  const rev=++generation;workspace=$('workspace').value;selectedTask='';renderedTask='';pending=null;docs=[];workspaceRevision=null;
  invalidateConsent();$('documents').replaceChildren();$('tasks').replaceChildren();$('answer').replaceChildren(el('p','选择任务后查看原文与来源。','empty'));
  $('file').disabled=!workspace;$('sample').disabled=!workspace;$('result-status').textContent='等待提问';renderConditions();
  localStorage.setItem('lablens.workspace',workspace);
  if(!workspace){$('revision').textContent='先创建或选择一个项目';return;}
  try{await loadDocuments(rev);await loadTasks(rev);notice('资料保存在当前项目中。');}catch(e){if(rev===generation)notice(e.message,true);}
}
async function loadDocuments(rev=generation){
  const data=await api(`/workspaces/${workspace}/documents`);if(rev!==generation)return;
  if(workspaceRevision!==data.revision){invalidateConsent();pending=null;}workspaceRevision=data.revision;
  docs=data.documents;$('revision').textContent=`${docs.length} 份资料 · 版本 ${data.revision}`;$('documents').replaceChildren();
  for(const d of docs){const row=el('div',undefined,'document');row.append(el('span',d.title));const b=el('button','删除');b.setAttribute('aria-label',`删除 ${d.title}`);b.onclick=async()=>{b.disabled=true;try{await api(`/workspaces/${workspace}/documents/${encodeURIComponent(d.id)}`,{method:'DELETE'});if(rev===generation){pending=null;invalidateConsent();await loadDocuments(rev);notice('资料已删除。新任务使用新版本，历史任务保留原始快照。');}}catch(e){notice(e.message,true);b.disabled=false;}};row.append(b);$('documents').append(row);}
  renderConditions();
}
async function loadTasks(rev=generation){
  if(!workspace)return;
  const data=await api(`/workspaces/${workspace}/tasks`);if(rev!==generation)return;
  $('tasks').replaceChildren();
  for(const t of data.tasks){const b=el('button',undefined,'task');b.append(el('span',t.request.question),el('small',states[t.status]||t.status));b.onclick=()=>{if(rev!==generation||t.workspace_id!==workspace)return;selectedTask=t.id;renderTask(t);};$('tasks').append(b);if(t.id===selectedTask&&renderedTask!==`${t.id}:${t.status}`)renderTask(t);}
  if(!data.tasks.length)$('tasks').append(el('p','暂无任务记录。','small'));
}
function taskDetails(t){
  const r=t.result,usage=t.execution_usage??r?.usage??t.error_result?.usage??null;
  const detail=el('details');detail.append(el('summary','查看本次调用与运行记录'));
  detail.append(el('p','以下是后端保存的当前任务记录；null 表示未记录或未知，不能当作 0。','small'));
  detail.append(el('pre',JSON.stringify({task_id:t.id,status:t.status,snapshot_revision:t.snapshot_revision,
    cache_hit:t.cache_hit,worker_ms:t.worker_ms,execution_usage:usage,usage_unknown:t.usage_unknown,
    origin_usage:t.origin_usage??r?.origin_usage,error_code:t.error_code,error_result:t.error_result,
    retrieval:r?.retrieval,agent:r?.agent??t.error_result?.agent,assessment_validation:r?.assessment_validation},null,2)));
  return detail;
}
function prepareTaskRetry(t,rev,w){
  if(rev!==generation||w!==workspace||t.workspace_id!==workspace||submitting)return;
  // Preparing is a new user intent; only the later form submission creates its key and sends a request.
  $('question').value=t.request.question;$('strategy').value=t.request.strategy;$('mode').value=t.request.mode;
  pending=null;invalidateConsent();renderConditions();
  const remote=t.request.strategy!=='bm25'||t.request.mode!=='extractive';
  notice('已填回原问题并准备新任务，尚未提交。新任务使用当前资料版本，原失败记录会保留。'+
    (remote?'请重新勾选模型发送许可，再点击“开始查找”；可能再次产生调用费用。':'请点击“开始查找”后再执行。'));
  $('question').focus();
}
function renderTask(t){
  if(t.workspace_id!==workspace)return;
  const rev=generation,w=workspace;
  renderedTask=`${t.id}:${t.status}`;
  $('result-status').textContent=`${states[t.status]||t.status} · 资料版本 ${t.snapshot_revision}`;$('answer').replaceChildren();
  if(t.status==='FAILED'){
    $('answer').append(el('p',t.error_message||'任务失败，可查看运行记录。','error'));
    if(t.usage_unknown||t.execution_usage?.provider_usage_unknown)$('answer').append(el('p','中断前的模型用量可能未知；未自动重试。','small'));
    const retry=el('button','准备新任务重试','secondary');retry.id='prepare-retry';retry.type='button';retry.disabled=submitting;
    retry.onclick=()=>prepareTaskRetry(t,rev,w);$('answer').append(retry);
    $('answer').append(el('p','只填回原请求，不会自动执行。重新提交使用当前资料版本，旧任务与旧快照继续保留。','small'));
  }
  if(!t.result){if(t.status!=='FAILED')$('answer').append(el('p',t.status==='RUNNING'?'正在查找资料…':'任务已保存，正在等待处理。','empty'));if(t.error_result?.agent)$('answer').append(agentTrace(t.error_result.agent));$('answer').append(taskDetails(t));return;}
  const r=t.result;
  if(r.status==='refused')$('result-status').textContent+=' · 模型拒答';
  if(r.evidence_verdict)$('result-status').textContent+=' · '+({supported:'证据支持',contradicted:'存在矛盾',insufficient:'证据不足'}[r.evidence_verdict]||r.evidence_verdict);
  $('answer').append(el('div',r.status==='excerpts'?`找到 ${(r.citations||[]).length} 段相关原文。`:typeof r.answer==='string'?r.answer:JSON.stringify(r.answer), 'answer-text'));
  if(r.status==='excerpts')$('answer').append(el('p','以下为原文摘录，尚未判断这些资料是否完整回答问题。','small'));
  if(r.status==='refused')$('answer').append(el('p','模型认为证据不足。以下是本次找到的原文，便于核对；拒答也可能有误。','small'));
  for(const c of (r.status==='refused'?(r.retrieval?.hits||[]):r.citations||[])){const box=el('div',undefined,'citation');box.append(el('strong',c.title||c.document_id),el('p',c.text),el('small',`${c.source} · ${c.start}–${c.end}`));$('answer').append(box);}
  if(r.agent)$('answer').append(agentTrace(r.agent));
  $('answer').append(taskDetails(t));
}
function agentTrace(agent){
  const box=el('details');box.open=true;box.append(el('summary','Agent 查找与核验过程'));
  const labels={completed:'已完成',insufficient_evidence:'证据不足，已停止',timeout:'超时，已停止',budget_exceeded:'达到调用上限',tool_error:'工具失败',model_error:'模型失败',review_error:'复核失败',protocol_error:'工具协议错误',invalid_report:'报告未通过校验'};
  box.append(el('p',`${labels[agent.state]||agent.state} · ${agent.steps??0} 次决策 · ${agent.tool_calls??0} 次工具调用`,'small'));
  const list=el('ol');
  for(const event of agent.trace||[]){let text='';
    if(event.event==='model_decision')text='模型选择：'+(event.tools||[]).join(' → ');
    if(event.event==='tool'&&event.name==='search_workspace')text=`查询“${event.arguments?.query||''}”：返回 ${event.output?.hits?.length||0} 段原文`;
    if(event.event==='tool'&&event.name==='verify_sources')text=`核对 ${event.output?.chunk_ids?.length||0} 段原文的来源、位置与内容摘要`;
    if(event.event==='review_started')text='用同一模型单独复核证据判断（并非独立人工评审）';
    if(event.event==='tool'&&event.name==='submit_report')text='报告通过来源与引文检查并保存';
    if(event.event==='tool_rejected')text='工具参数或报告未通过检查，已向模型返回原因';
    if(event.event==='stopped')text='执行已停止：'+(labels[event.state]||event.state);
    if(text)list.append(el('li',text));
  }
  box.append(list,el('p',`快照：${agent.snapshot_id} · 资料摘要：${agent.documents_sha256}`,'small'));
  return box;
}
$('create-form').onsubmit=async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;try{const w=await api('/workspaces',{method:'POST',body:JSON.stringify({name:$('new-name').value.trim()})});$('new-name').value='';await loadWorkspaces(w.id);}catch(x){notice(x.message,true);}finally{b.disabled=false;}};
$('workspace').onchange=changeWorkspace;
$('file').onchange=async()=>{const f=$('file').files[0];if(!f||!workspace)return;const rev=generation,w=workspace;$('file').disabled=true;try{if(f.size>200000)throw new Error('请选择小于 200 KB 的文本文件。');const text=await f.text();if(text.length>20000)throw new Error('每份资料最多 20,000 个字符，请拆分后导入。');await api(`/workspaces/${w}/documents/${crypto.randomUUID()}`,{method:'PUT',body:JSON.stringify({title:f.name,text,source:f.name})});if(rev===generation){pending=null;invalidateConsent();await loadDocuments(rev);notice('资料已保存。');}}catch(e){if(rev===generation)notice(e.message,true);}finally{$('file').value='';$('file').disabled=!workspace;}};
$('sample').onclick=async()=>{const rev=generation,w=workspace;pending=null;invalidateConsent();renderConditions();$('sample').disabled=true;try{const response=await fetch('/example-corpus.json');if(!response.ok)throw new Error('示例读取失败');const data=await response.json();let count=0;for(const d of data.documents){await api(`/workspaces/${w}/documents/${encodeURIComponent(d.id)}`,{method:'PUT',body:JSON.stringify({title:d.title,text:d.text,source:d.source})});count++;if(rev===generation)notice(`正在导入 ${count}/${data.documents.length} 份公开资料…`);}if(rev===generation){pending=null;invalidateConsent();await loadDocuments(rev);notice(`已保存 ${count} 份公开资料。`);}}catch(e){if(rev===generation)notice(e.message,true);}finally{if(rev===generation)await loadDocuments(rev);$('sample').disabled=!workspace;}};
$('question-form').onsubmit=async e=>{e.preventDefault();if(submitting||$('submit').disabled)return;submitting=true;renderConditions();const rev=generation,w=workspace,body=JSON.stringify({question:$('question').value.trim(),strategy:$('strategy').value,mode:$('mode').value,top_k:5,...(($('strategy').value!=='bm25'||$('mode').value!=='extractive')?{approved_revision:approvedRevision}:{})});if(!pending||pending.body!==body||pending.workspace!==w)pending={body,workspace:w,key:crypto.randomUUID()};try{const t=await api(`/workspaces/${w}/tasks`,{method:'POST',headers:{'Idempotency-Key':pending.key},body});if(rev===generation){selectedTask=t.id;renderTask(t);notice(t.reused?'已找到同一请求的任务。':'任务已保存，可以继续浏览资料。');await loadTasks(rev);}}catch(e){if(rev===generation){if(['approval_required','stale_approval'].includes(e.code)){invalidateConsent();pending=null;await loadDocuments(rev);notice('资料版本已变化，请核对当前资料并重新勾选发送许可后提交。',true);}else notice(e.message,true);}}finally{submitting=false;renderConditions();}};
for(const id of ['strategy','mode','question'])$(id).addEventListener('input',()=>{pending=null;if(id!=='question')invalidateConsent();renderConditions();});
$('consent').onchange=()=>{approvedRevision=$('consent').checked?workspaceRevision:null;pending=null;renderConditions();};
$('refresh').onclick=()=>loadTasks().catch(e=>notice(e.message,true));
async function systemStatus(){try{const s=await api('/system/status');$('system').textContent=JSON.stringify(s,null,2);$('connection').textContent=s.redis?.degraded?'已连接 · 缓存暂不可用':'本地服务已连接';$('connection-dot').classList.add('online');}catch(e){$('connection').textContent='本地服务暂不可用';$('connection-dot').classList.remove('online');}}
loadWorkspaces(localStorage.getItem('lablens.workspace')).catch(e=>notice(e.message,true));systemStatus();
setInterval(()=>{if(!document.hidden){loadTasks().catch(()=>{});systemStatus();}},2500);
