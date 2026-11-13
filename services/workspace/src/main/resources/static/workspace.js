'use strict';
const $ = id => document.getElementById(id);
const el = (tag, text, cls) => {const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls;return n;};
let materialsLoaded=false,workspaceRoles=new Map();
function canWriteWorkspace(){return workspaceRoles.get(workspace)!=='VIEWER';}
let workspace='', workspaceRevision=null, approvedRevision=null, generation=0, selectedTask='', renderedTask='', docs=[], pending=null, submitting=false;
let conversation='', conversationRevision=null, conversationData=null, conversationGeneration=0, stateDirty=false, approvedContext=null, selectionGeneration=0, conversationLoadGeneration=0, taskLoadGeneration=0;
const taskPageSize=25, activeTaskStates=new Set(['QUEUED','RUNNING','CANCEL_REQUESTED']);
let taskRows=[], taskNextCursor=null, taskHasMore=false, taskHistoryInitialized=false, taskHistoryPaged=false, taskHistoryHasNew=false, taskHistoryError='', taskHistoryRetry=null, taskListRequest=null;
let taskDetailRecord=null, taskDetailRequest=null, taskDetailGeneration=0;
const states={QUEUED:'排队中',RUNNING:'处理中',CANCEL_REQUESTED:'正在停止',CANCELLED:'已取消',SUCCEEDED:'已完成',FAILED:'失败'};
const cancellingTasks=new Set();
const evidenceLabels={supported:'证据支持',contradicted:'存在矛盾',insufficient:'证据不足'};
async function api(path, options={}) {
  const r=await (globalThis.LabLensAuth?.request||fetch)('/api'+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
  if(r.status===204)return null;
  const body=await r.json();
  if(!r.ok){const error=new Error(body.message||body.error||`请求失败 (${r.status})`);error.code=body.error;error.status=r.status;throw error;}
  return body;
}
function notice(message,error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error);}
function invalidateConsent(){approvedRevision=null;approvedContext=null;$('consent').checked=false;}
function stateDraft(){return {goal:$('conversation-goal').value.trim(),constraints:$('conversation-constraints').value.split('\n').map(x=>x.trim()).filter(Boolean),focus:$('conversation-focus').value.trim()};}
function consentContext(){return JSON.stringify({workspace,revision:workspaceRevision,conversation,conversation_revision:conversationRevision,state:conversation?stateDraft():null,strategy:$('strategy').value,mode:$('mode').value,question:$('question').value.trim(),memory:globalThis.LabLensMemory?.selection()||{}});}
function abandonPending(){pending=null;$('check-pending').hidden=true;}
function conversationBusy(){return (conversationData?.turns||[]).some(t=>['QUEUED','RUNNING','CANCEL_REQUESTED'].includes(t.status));}
function renderConditions(){
  for(const id of ['bulk-submit','bulk-cancel','index-build']){const item=$(id);if(item)item.disabled=!workspace||!canWriteWorkspace();}
  if(conversation)$('mode').value='agent';
  $('mode').disabled=!!conversation;
  $('conversation').disabled=!workspace||submitting;
  $('create-conversation').disabled=!workspace||!canWriteWorkspace()||submitting;
  const remote=$('strategy').value!=='bm25'||$('mode').value!=='extractive';
  $('consent-wrap').hidden=!remote;
  $('mode-summary').textContent=`${({bm25:'关键词检索',dense:'语义检索',hybrid:'混合检索'})[$('strategy').value]} · ${({extractive:'原文摘录',live:'模型整理',agent:'Agent 查找与核验'})[$('mode').value]}`;
  if(!conversation)$('conversation-status').textContent='';
  else if(conversationRevision===null)$('conversation-status').textContent='正在读取所选会话…';
  $('consent-label').textContent=conversation?'允许将当前资料版本、问题、会话历史及会话目标与限制发送给已配置的模型服务，可能产生调用费用。':'允许将当前资料版本和问题发送给已配置的模型服务，可能产生调用费用。';
  if(globalThis.LabLensMemory?.selection().memory_ids?.length)$('consent-label').textContent+=' 包含本次选用的项目记忆、原文与备注。';
  $('cost-note').hidden=!remote;
  $('cost-note').textContent=$('mode').value==='agent'?'Agent 会调用生成模型：最多 6 次决策、1 次复核。语义检索另有嵌入调用。':remote?[$('strategy').value!=='bm25'?'语义/混合检索会调用嵌入模型。':'',$('mode').value==='live'?'模型整理会调用生成模型。':''].filter(Boolean).join(' '):'关键词检索与原文摘录在本机完成，不调用模型。';
  $('submit').disabled=!canWriteWorkspace()||!workspace||!docs.length||submitting||!!pending?.uncertain||(!!conversation&&(conversationRevision===null||conversationBusy()))||(remote&&(!$('consent').checked||approvedRevision!==workspaceRevision||approvedContext!==consentContext()));
  $('check-pending').hidden=!pending?.uncertain;$('check-pending').disabled=submitting;
  const retry=$('prepare-retry');if(retry)retry.disabled=submitting;
  globalThis.LabLensMemory?.conditions();
}
async function loadWorkspaces(preferred){
  const data=await api('/workspaces');$('workspace').replaceChildren(new Option('请选择项目',''));
  workspaceRoles=new Map(data.workspaces.map(w=>[w.id,w.role]));
  for(const w of data.workspaces)$('workspace').append(new Option(w.name+(w.role==='VIEWER'?' · 只读':''),w.id));
  if(preferred&&data.workspaces.some(w=>w.id===preferred))$('workspace').value=preferred;
  await changeWorkspace();
}
async function changeWorkspace(){
  materialsLoaded=false;$('materials-management').open=true;
  const rev=++generation;workspace=$('workspace').value;selectedTask='';renderedTask='';pending=null;docs=[];workspaceRevision=null;
  conversation='';conversationRevision=null;conversationData=null;stateDirty=false;conversationGeneration++;selectionGeneration++;resetTaskHistory();clearTaskDetail();
  $('conversation').replaceChildren(new Option('独立提问 · 不带入历史',''));$('conversation').value='';
  $('conversation-panel').hidden=true;$('conversation-turns').replaceChildren();setStateDraft({});$('question').value='';
  invalidateConsent();$('documents').replaceChildren();$('tasks').replaceChildren();$('history-summary').textContent='展开记录';$('answer').replaceChildren(el('p','提交问题后，在这里查看回答与来源。','empty'));
  $('file').disabled=!workspace||!canWriteWorkspace();$('sample').disabled=!workspace||!canWriteWorkspace();$('result-status').textContent='';renderConditions();
  localStorage.setItem('lablens.workspace',workspace);
  globalThis.LabLensMembers?.refresh();
  globalThis.LabLensMemory?.refresh();
  if(!workspace){$('revision').textContent='先创建或选择一个项目';return;}
  try{await loadDocuments(rev);await loadTasks(rev);await loadConversations(rev,localStorage.getItem(`lablens.conversation.${workspace}`));if(rev===generation)notice(docs.length?'':'添加资料后即可提问。');}catch(e){if(rev===generation)notice(e.message,true);}
}
async function loadDocuments(rev=generation){
  const data=await api(`/workspaces/${workspace}/documents`);if(rev!==generation)return;
  if(workspaceRevision!==null&&data.revision<workspaceRevision)return;
  // A revision poll may observe server acceptance before the POST response arrives.
  // Keep its exact request/key until submission resolves, including a lost response.
  const documentsChanged=workspaceRevision!==data.revision;
  if(documentsChanged){invalidateConsent();if(!submitting&&!pending?.uncertain)abandonPending();}workspaceRevision=data.revision;
  docs=data.documents;
  if(documentsChanged)globalThis.LabLensMemory?.refresh();
  if(!materialsLoaded||!docs.length)$('materials-management').open=!docs.length;
  materialsLoaded=true;
  $('revision').textContent=`${docs.length} 份资料 · 版本 ${data.revision}`;$('documents').replaceChildren();
  for(const d of docs){const row=el('div',undefined,'document');row.append(el('span',d.title));const b=el('button','删除');b.setAttribute('aria-label',`删除 ${d.title}`);b.onclick=async()=>{b.disabled=true;try{await api(`/workspaces/${workspace}/documents/${encodeURIComponent(d.id)}`,{method:'DELETE'});if(rev===generation){pending=null;invalidateConsent();await loadDocuments(rev);notice('资料已删除。新任务使用新版本，历史任务保留原始快照。');}}catch(e){notice(e.message,true);b.disabled=false;}};b.disabled=!canWriteWorkspace();row.append(b);$('documents').append(row);}
  if(!docs.length)$('documents').append(el('p','还没有资料。添加文件或导入示例后即可提问。','small'));
  renderConditions();
}
function clearTaskDetail(){
  taskDetailRecord=null;taskDetailGeneration++;taskDetailRequest=null;
  $('task-detail-notice').textContent='';$('retry-task-detail').hidden=true;
}
function resetTaskHistory(){
  taskLoadGeneration++;taskRows=[];taskNextCursor=null;taskHasMore=false;taskHistoryInitialized=false;
  taskHistoryPaged=false;taskHistoryHasNew=false;taskHistoryError='';taskHistoryRetry=null;taskListRequest=null;renderTaskHistory();
}
function renderTaskHistory(){
  const rev=generation,w=workspace,busy=!!taskListRequest;
  $('tasks').replaceChildren();$('tasks').setAttribute('aria-busy',String(busy));
  $('history-summary').textContent=taskHistoryInitialized?`${taskRows.length} 条记录`:'展开记录';
  for(const t of taskRows){
    const b=el('button',undefined,'task');b.type='button';b.setAttribute('aria-pressed',String(t.id===selectedTask));
    b.append(el('span',t.question),el('small',states[t.status]||t.status));
    b.onclick=()=>{if(rev!==generation||w!==workspace||t.workspace_id!==workspace)return;return selectTask(t.id);};
    $('tasks').append(b);
  }
  if(!taskRows.length)$('tasks').append(el('p',busy?'正在读取任务记录…':taskHistoryInitialized?'暂无任务记录。':'选择项目后读取任务记录。','small'));
  $('history-status').textContent=taskHistoryError||(busy?(taskListRequest.older?'正在加载更早的记录…':'正在刷新最近记录…'):
    taskHistoryHasNew?'有新任务记录。已载入的旧页继续保留，点击“重新载入最近记录”可查看最新记录。':
    taskHistoryInitialized?(taskHistoryPaged?'已保留载入的旧页；点击旧记录可核对最新状态。':taskHasMore?'自动刷新仅显示最近 25 条；可加载更早记录。':'已载入全部记录。'):'');
  $('history-status').classList.toggle('error',!!taskHistoryError);
  $('load-more-tasks').hidden=!taskHasMore;$('load-more-tasks').disabled=busy;
  $('retry-history').hidden=!taskHistoryError;$('retry-history').disabled=busy;
  $('reset-history').hidden=!taskHistoryInitialized;$('reset-history').disabled=busy;
}
function summaryFromTask(t){
  return {id:t.id,workspace_id:t.workspace_id,status:t.status,question:t.request?.question||'',created_at:t.created_at,
    snapshot_revision:t.snapshot_revision,finished_at:t.finished_at,error_code:t.error_code,usage_unknown:t.usage_unknown,
    cancel_requested_at:t.cancel_requested_at,cancel_acknowledged_at:t.cancel_acknowledged_at,cache_hit:t.cache_hit,worker_ms:t.worker_ms};
}
function taskVersion(t){return JSON.stringify([t?.status,t?.finished_at,t?.cancel_requested_at,t?.cancel_acknowledged_at,t?.error_code,t?.usage_unknown,t?.cache_hit,t?.worker_ms]);}
async function loadTaskDetail({showLoading=false}={}){
  const rev=generation,w=workspace,id=selectedTask,selection=selectionGeneration;
  if(!w||!id)return;
  if(taskDetailRequest?.id===id&&taskDetailRequest.selection===selection&&taskDetailRequest.rev===rev)return taskDetailRequest.promise;
  const request={id,selection,rev,load:++taskDetailGeneration};taskDetailRequest=request;
  const current=()=>rev===generation&&w===workspace&&id===selectedTask&&selection===selectionGeneration&&request.load===taskDetailGeneration;
  $('retry-task-detail').hidden=true;$('task-detail-notice').textContent=showLoading?'正在读取完整报告与来源…':'正在更新所选任务…';
  if(showLoading){renderedTask='';$('answer').replaceChildren();$('result-status').textContent='正在读取任务';}
  request.promise=(async()=>{
    try{
      const t=await api(`/workspaces/${w}/tasks/${encodeURIComponent(id)}`);if(!current())return;
      if(t.id!==id||t.workspace_id!==w)throw new Error('任务详情与当前选择不一致，请重试。');
      const row=taskRows.findIndex(item=>item.id===id);if(row>=0)taskRows[row]={...taskRows[row],...summaryFromTask(t)};
      renderTask(t);renderTaskHistory();
    }catch(e){if(current()){$('task-detail-notice').textContent=`完整报告读取失败，${taskDetailRecord?.id===id?'仍显示上次读取的内容。':'请重试。'} ${e.message}`;$('retry-task-detail').hidden=false;}}
    finally{if(taskDetailRequest===request)taskDetailRequest=null;}
  })();
  return request.promise;
}
async function selectTask(id){
  selectionGeneration++;selectedTask=id;clearTaskDetail();renderTaskHistory();await loadTaskDetail({showLoading:true});
}
async function loadTaskPage(rev=generation,{older=false,reset=false}={}){
  const w=workspace;if(!w||rev!==generation)return;
  if(taskListRequest)return taskListRequest.promise;
  if(older&&!taskNextCursor)return;
  const cursor=older?taskNextCursor:null,request={older,load:++taskLoadGeneration};taskListRequest=request;
  if(older)taskHistoryPaged=true;
  if(older||reset||!taskHistoryRetry?.older){taskHistoryError='';taskHistoryRetry=null;}
  renderTaskHistory();
  const current=()=>rev===generation&&w===workspace&&request.load===taskLoadGeneration;
  request.promise=(async()=>{
    try{
      const data=await api(`/workspaces/${w}/tasks?view=summary&limit=${taskPageSize}${cursor?'&cursor='+encodeURIComponent(cursor):''}`);if(!current())return;
      const incoming=data.tasks||[];
      if(reset||!taskHistoryInitialized||(!older&&!taskHistoryPaged)){
        taskRows=incoming;taskNextCursor=data.next_cursor;taskHasMore=data.has_more;taskHistoryPaged=false;taskHistoryHasNew=false;
      }else if(older){
        const existing=new Set(taskRows.map(t=>t.id));
        taskRows=[...taskRows,...incoming.filter(t=>!existing.has(t.id))];taskNextCursor=data.next_cursor;taskHasMore=data.has_more;taskHistoryPaged=true;
      }else{
        const existing=new Set(taskRows.map(t=>t.id)),updates=new Map(incoming.map(t=>[t.id,t]));
        if(incoming.some(t=>!existing.has(t.id)))taskHistoryHasNew=true;
        taskRows=taskRows.map(t=>updates.get(t.id)||t);
        // A head poll must not grow the displayed pages or rewind their continuation cursor.
      }
      taskHistoryInitialized=true;renderTaskHistory();
      const selected=taskRows.find(t=>t.id===selectedTask);
      if(selectedTask&&(!taskDetailRecord||activeTaskStates.has(taskDetailRecord.status)||(selected&&taskVersion(selected)!==taskVersion(taskDetailRecord))))await loadTaskDetail();
    }catch(e){if(current()){taskHistoryRetry={older,reset};taskHistoryError=`${older?'更早记录加载':'最近记录刷新'}失败，已载入内容已保留。${e.message}`;renderTaskHistory();}throw e;}
    finally{if(taskListRequest===request){taskListRequest=null;renderTaskHistory();}}
  })();
  return request.promise;
}
function loadTasks(rev=generation){return loadTaskPage(rev);}
function setStateDraft(state){
  $('conversation-goal').value=state.goal||'';$('conversation-constraints').value=(state.constraints||[]).join('\n');$('conversation-focus').value=state.focus||'';
  $('conversation-draft').textContent='';
}
async function loadConversations(rev=generation,preferred){
  const w=workspace;if(!w)return;
  const data=await api(`/workspaces/${w}/conversations`);if(rev!==generation||w!==workspace)return;
  $('conversation').replaceChildren(new Option('独立提问 · 不带入历史',''));
  for(const c of data.conversations||[])$('conversation').append(new Option(c.title,c.id));
  const selected=preferred===undefined?conversation:preferred;
  $('conversation').value=(data.conversations||[]).some(c=>c.id===selected)?selected:'';
  if(preferred!==undefined||conversation!==$('conversation').value)await changeConversation();
}
async function changeConversation(){
  const cg=++conversationGeneration;conversation=$('conversation').value;conversationRevision=null;conversationData=null;stateDirty=false;
  abandonPending();invalidateConsent();setStateDraft({});$('conversation-turns').replaceChildren();$('conversation-panel').hidden=!conversation;
  selectedTask='';renderedTask='';selectionGeneration++;clearTaskDetail();renderTaskHistory();$('answer').replaceChildren(el('p',conversation?'选择历史记录或继续提问。':'提交问题后，在这里查看回答与来源。','empty'));$('result-status').textContent='';
  localStorage.setItem(`lablens.conversation.${workspace}`,conversation);renderConditions();
  if(conversation)try{await loadConversation(generation,cg);}catch(e){if(cg===conversationGeneration)notice(e.message,true);}
}
async function loadConversation(rev=generation,cg=conversationGeneration,{preserveDraft=false}={}){
  const w=workspace,c=conversation,load=++conversationLoadGeneration;if(!w||!c)return;
  const data=await api(`/workspaces/${w}/conversations/${c}`);if(rev!==generation||cg!==conversationGeneration||c!==conversation||w!==workspace||load!==conversationLoadGeneration)return;
  if(conversationRevision!==null&&data.revision<conversationRevision)return;
  if(conversationRevision!==data.revision){invalidateConsent();if(!submitting&&!pending?.uncertain)abandonPending();}
  conversationRevision=data.revision;conversationData=data;
  if(!stateDirty&&!preserveDraft)setStateDraft(data.state||{});
  $('conversation-status').textContent=`${data.title} · 会话版本 ${data.revision}${conversationBusy()?' · 本轮正在处理，完成后可继续提问':''}`;
  $('conversation-turns').replaceChildren();
  if(data.omitted_turns)$('conversation-turns').append(el('p',`这里只显示最近的记录，另有 ${data.omitted_turns} 轮未展示。模型上下文还会受预算限制，具体见各轮运行记录。`,'small'));
  for(const turn of data.turns||[]){
    const row=el('div',undefined,'conversation-turn');const b=el('button',undefined,'task');b.type='button';
    b.append(el('span',`${turn.seq}. ${turn.question}`),el('small',turn.result_status==='clarification_required'?'待你澄清':states[turn.status]||turn.status));
    b.onclick=()=>{if(rev!==generation||cg!==conversationGeneration||c!==conversation)return;return selectTask(turn.task_id);};
    row.append(b);if(turn.answer)row.append(el('p',turn.answer,'turn-answer'));if(turn.answer_truncated)row.append(el('small','摘要已截断，点击本轮查看完整报告。'));$('conversation-turns').append(row);
  }
  if(!(data.turns||[]).length)$('conversation-turns').append(el('p','还没有对话。第一轮会保存上面的目标与限制。','small'));
  renderConditions();
}
function taskDetails(t){
  const r=t.result,usage=t.execution_usage??r?.usage??t.error_result?.usage??null;
  const detail=el('details');detail.append(el('summary','查看本次调用与运行记录'));
  detail.append(el('p','以下是后端保存的当前任务记录；null 表示未记录或未知，不能当作 0。','small'));
  detail.append(el('pre',JSON.stringify({task_id:t.id,status:t.status,snapshot_revision:t.snapshot_revision,
    cache_hit:t.cache_hit,worker_ms:t.worker_ms,execution_usage:usage,usage_unknown:t.usage_unknown,
    origin_usage:t.origin_usage??r?.origin_usage,error_code:t.error_code,error_result:t.error_result,
    conversation_id:t.conversation_id,conversation_seq:t.conversation_seq,context:r?.context??t.error_result?.context,
    retrieval:r?.retrieval,agent:r?.agent??t.error_result?.agent,assessment_validation:r?.assessment_validation},null,2)));
  return detail;
}
async function cancelTask(t,rev,w,selection,button){
  const current=()=>rev===generation&&w===workspace&&selection===selectionGeneration&&renderedTask.startsWith(t.id+':');
  if(!current()||!canWriteWorkspace()||cancellingTasks.has(t.id))return;
  cancellingTasks.add(t.id);button.disabled=true;
  try{
    const saved=await api(`/workspaces/${w}/tasks/${t.id}/cancel`,{method:'POST',body:'{}'});
    if(!current())return;
    renderTask(saved);
    notice(saved.status==='CANCELLED'?'任务已取消，历史记录已保留。':saved.status==='CANCEL_REQUESTED'?'已请求停止，正在等待 worker 确认。已发出的模型请求仍可能产生用量。':'任务已先于取消完成，原结果已保留。');
    await loadTasks(rev);
    if(rev===generation&&conversation&&t.conversation_id===conversation)await loadConversation(rev,conversationGeneration,{preserveDraft:true});
  }catch(e){if(current())notice('停止请求未得到确认，请刷新状态后再次尝试。'+e.message,true);}
  finally{cancellingTasks.delete(t.id);if(current())button.disabled=false;}
}
function prepareTaskRetry(t,rev,w){
  if(rev!==generation||w!==workspace||t.workspace_id!==workspace||submitting)return;
  // Preparing is a new user intent; only the later form submission creates its key and sends a request.
  $('question').value=t.request.question;$('strategy').value=t.request.strategy;$('mode').value=t.request.mode;
  if(conversation&&t.conversation_id!==conversation){conversation='';conversationRevision=null;conversationData=null;conversationGeneration++;$('conversation').value='';$('conversation-panel').hidden=true;localStorage.setItem(`lablens.conversation.${workspace}`,'');}
  abandonPending();invalidateConsent();renderConditions();
  const remote=t.request.strategy!=='bm25'||t.request.mode!=='extractive';
  notice('已填回原问题并准备新任务，尚未提交。新任务使用当前资料版本，原记录会保留。'+
    (remote?'请重新勾选模型发送许可，再点击“开始查找”；可能再次产生调用费用。':'请点击“开始查找”后再执行。'));
  $('question').focus();
}
function renderTask(t){
  if(t.workspace_id!==workspace)return;
  const rev=generation,w=workspace;
  taskDetailRecord=t;taskDetailGeneration++;$('task-detail-notice').textContent='';$('retry-task-detail').hidden=true;
  renderedTask=`${t.id}:${t.status}`;
  $('result-status').textContent=`${states[t.status]||t.status} · 资料版本 ${t.snapshot_revision}`;$('answer').replaceChildren();
  if(t.request?.resume_parent_task_id){
    const selection=selectionGeneration,parent=el('button','查看中断前的原任务','link');parent.type='button';
    parent.onclick=()=>{if(rev===generation&&w===workspace&&selection===selectionGeneration)return selectTask(t.request.resume_parent_task_id);};
    $('answer').append(el('p','此记录是原任务的显式恢复，沿用旧资料及累计调用预算。报告用量为累计值；执行用量只计本次新增调用，未知用量继续标记未知。','small'),parent);
  }
  if(['QUEUED','RUNNING'].includes(t.status)){
    const selection=selectionGeneration,cancel=el('button','停止此任务','secondary');cancel.id='cancel-task';cancel.type='button';cancel.disabled=!canWriteWorkspace()||cancellingTasks.has(t.id);
    cancel.onclick=()=>cancelTask(t,rev,w,selection,cancel);$('answer').append(cancel);
    $('answer').append(el('p','停止会保留历史。已发出的请求可能仍产生用量；本页会等待 worker 确认退出。','small'));
  }
  if(t.status==='CANCEL_REQUESTED')$('answer').append(el('p','正在等待 worker 确认停止。同一会话暂时不能继续提问；已发出的模型请求可能仍在处理，不能假定费用为零。','small'));
  if(t.status==='FAILED'||t.status==='CANCELLED'){
    $('answer').append(el('p',t.status==='CANCELLED'?'任务已取消，原始请求与历史记录已保留。':t.error_code==='context_budget_exceeded'?'上下文已超出本次预算，任务已停止。请缩短目标与限制、减少本轮问题范围，或新建会话后重新提交。':t.error_message||'任务失败，可查看运行记录。',t.status==='CANCELLED'?'small':'error'));
    if(t.usage_unknown||t.execution_usage?.provider_usage_unknown)$('answer').append(el('p','中断前的模型用量可能未知；未自动重试。','small'));
    const retry=el('button','准备新任务重试','secondary');retry.id='prepare-retry';retry.type='button';retry.disabled=submitting;
    retry.onclick=()=>prepareTaskRetry(t,rev,w);$('answer').append(retry);
    $('answer').append(el('p','只填回原请求，不会自动执行。重新提交使用当前资料版本，旧任务与旧快照继续保留。','small'));
    const selection=selectionGeneration;
    globalThis.LabLensRecovery?.render(t,$('answer'),{
      current:()=>rev===generation&&w===workspace&&selection===selectionGeneration&&selectedTask===t.id,
      canWrite:canWriteWorkspace,submitting:()=>submitting,select:selectTask});
  }
  if(!t.result){if(['QUEUED','RUNNING'].includes(t.status))$('answer').append(el('p',t.status==='RUNNING'?'正在查找资料…':'任务已保存，正在等待处理。','empty'));if(t.error_result?.agent)$('answer').append(agentTrace(t.error_result.agent));$('answer').append(taskDetails(t));if(t.error_result?.context)$('answer').append(contextDetails(t.error_result.context));return;}
  const r=t.result;
  if(r.status==='refused')$('result-status').textContent+=' · 模型拒答';
  if(r.evidence_verdict)$('result-status').textContent+=' · '+(evidenceLabels[r.evidence_verdict]||r.evidence_verdict);
  if(r.status==='clarification_required')$('result-status').textContent+=' · 待你澄清';
  $('answer').append(el('div',r.status==='excerpts'?`找到 ${(r.citations||[]).length} 段相关原文。`:typeof r.answer==='string'?r.answer:JSON.stringify(r.answer), 'answer-text'));
  if(r.status==='clarification_required'){
    const c=conversation,cg=conversationGeneration,box=el('div',undefined,'clarification');
    if(r.clarification?.question&&r.clarification.question!==r.answer)box.append(el('p',r.clarification.question));
    box.append(el('p','选择一个方向或补充说明后，再手动提交。选择不会调用模型。','small'));
    for(const choice of r.clarification?.choices||r.clarification?.options||[]){const text=typeof choice==='string'?choice:choice.label||choice.value;if(!text)continue;
      const b=el('button',text,'secondary');b.type='button';b.disabled=!!t.conversation_id&&t.conversation_id!==conversation;
      b.onclick=()=>{if(rev!==generation||cg!==conversationGeneration||c!==conversation||submitting||selectedTask!==t.id)return;
        $('question').value=text;abandonPending();invalidateConsent();renderConditions();$('question').focus();notice('已填入澄清内容，尚未提交。请核对后重新勾选发送许可。');};box.append(b);}
    $('answer').append(box);
  }
  if(r.status==='excerpts')$('answer').append(el('p','以下为原文摘录，尚未判断这些资料是否完整回答问题。','small'));
  if(r.status==='refused')$('answer').append(el('p','模型认为证据不足。以下是本次找到的原文，便于核对；拒答也可能有误。','small'));
  const citations=r.status==='refused'?(r.retrieval?.hits||[]):r.citations||[];
  if(citations.length||(r.assessments||[]).length){
    const sources=el('details');sources.open=r.status==='excerpts';sources.append(el('summary',`来源与逐条判定 · ${citations.length} 段原文`));
    for(const a of r.assessments||[]){
      const item=el('div',undefined,'assessment');item.append(el('strong',`${evidenceLabels[a.verdict]||a.verdict} · ${a.statement}`),el('p',a.explanation));
      for(const q of a.quotes||[]){item.append(el('blockquote',q.quote),el('small',q.chunk_id));}
      if((a.citation_ids||[]).length)item.append(el('small','引用：'+a.citation_ids.join(' · ')));
      sources.append(item);
    }
    for(const c of citations){const box=el('div',undefined,'citation');box.append(el('strong',c.title||c.document_id),el('p',c.text),el('small',`${c.source} · ${c.start}–${c.end}`));sources.append(box);}
    $('answer').append(sources);
  }
  if(r.agent)$('answer').append(agentTrace(r.agent));
  if(r.recovery){const recovery=el('details');recovery.append(el('summary','恢复来源与累计预算'),el('pre',JSON.stringify(r.recovery,null,2)));$('answer').append(recovery);}
  if(r.memory){const memory=el('details');memory.append(el('summary','本次选用的项目记忆'),el('p','这是任务接收时的记忆版本，可能与当前列表不同；记忆本身不构成证据。','small'),el('pre',JSON.stringify(r.memory,null,2)));$('answer').append(memory);}
  $('answer').append(taskDetails(t));
  if(r.context)$('answer').append(contextDetails(r.context));
}
function contextDetails(context){
  const box=el('details');box.append(el('summary','本轮使用的会话上下文'));
  box.append(el('p',`会话版本 ${context.conversation_revision??'未记录'} · 收到 ${context.input_turns??'未记录'} 轮历史 · 上游省略 ${context.omitted_turns??'未记录'} 轮`,'small'));
  const calls=context.context_budget?.calls||[],dropped=Math.max(0,...calls.map(c=>Number(c.dropped_turns)||0));
  if(dropped)box.append(el('p',context.summary_policy?`受上下文预算限制，${dropped} 轮较早回答从完整历史中移除，用户原话保存在有界摘要中。摘要只提供上下文，不是证据。`:`受上下文预算限制，部分调用未带入 ${dropped} 轮历史。请不要假定所有旧消息仍在本轮上下文中。`,'small'));
  if(context.unavailable_legacy_user_turns)box.append(el('p',`旧快照未保存 ${context.unavailable_legacy_user_turns} 轮用户原话，无法重建；请重新说明仍适用的限制。`,'small'));
  box.append(el('p','目标与限制由用户填写，不等同于已验证事实。预算按序列化文本估算，不是模型 tokenizer 的精确 token 数。','small'));
  box.append(el('pre',JSON.stringify(context,null,2)));return box;
}
function runtimeTimings(agent){
  const trace=(agent.trace||[]).filter(e=>['stage_started','stage_finished','request_started','request_finished'].includes(e.event));
  if(!trace.length)return null;
  const box=el('details');box.append(el('summary','分阶段耗时与预算'));
  box.append(el('p','这里记录客户端耗时；工具阶段可能包含复核请求，耗时不能直接相加。请求大小是 JSON 正文字节，不是 token；未返回的用量保持未知。整次调用到期会中断连接，仍需等待清理；旧记录可能只有网络阶段超时。','small'));
  const phase={decision:'模型决策',tool:'工具执行',review:'最终复核'};
  const outcome={returned:'已返回',timeout:'超时',cancelled:'已取消',http_error:'HTTP 错误',transport_error:'连接错误',error:'失败',rejected:'未通过检查'};
  const seconds=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0?(v/1000).toFixed(3)+' 秒':'未知';
  const size=v=>Number.isSafeInteger(v)&&v>=0?(v/1024).toFixed(1)+' KiB':'未知';
  const finished=new Set(trace.filter(e=>e.event.endsWith('_finished')).map(e=>(e.event.startsWith('request_')?'r:':'s:')+(e.request_id||e.stage_id)));
  const list=el('ul');
  for(const e of trace){
    const request=e.event.startsWith('request_'),key=(request?'r:':'s:')+(e.request_id||e.stage_id);
    if(e.event.endsWith('_started')&&finished.has(key))continue;
    const known=typeof e.usage?.total_tokens==='number'&&Number.isSafeInteger(e.usage.total_tokens)&&e.usage.total_tokens>=0&&e.usage_known===true;
    let text=`${phase[e.phase]||'阶段'}${request?' · 模型请求':''}${e.name?' · '+e.name:''}：${outcome[e.outcome]||'开始后未记录结束'}；耗时 ${seconds(e.duration_ms)}；剩余预算 ${seconds(e.remaining_ms)}`;
    if(e.request_body_bytes!==undefined)text+=`；请求正文 ${size(e.request_body_bytes)}`;
    if(e.timeout_ms!==undefined)text+=`；网络阶段超时 ${seconds(e.timeout_ms)}`;
    if(request&&e.event.endsWith('_finished'))text+=known?`；返回用量 ${e.usage.total_tokens} token`:'；本次用量未知，不能计为零';
    list.append(el('li',text));
  }
  box.append(list);return box;
}
function agentTrace(agent){
  const box=el('details');
  const labels={completed:'已完成',cancelled:'已取消',awaiting_clarification:'等待你澄清，已结束本轮',context_budget_exceeded:'上下文预算不足，已停止',insufficient_evidence:'证据不足，已停止',timeout:'超时，已停止',budget_exceeded:'达到调用上限',tool_error:'工具失败',model_error:'模型失败',review_error:'复核失败',protocol_error:'工具协议错误',invalid_report:'报告未通过校验'};
  box.append(el('summary',`Agent 运行轨迹 · ${labels[agent.state]||agent.state||'状态未知'}`));
  box.append(el('p',`${agent.steps??'未知'} 次决策 · ${agent.tool_calls??'未知'} 次工具调用`,'small'));
  const list=el('ol');
  for(const event of agent.trace||[]){let text='';
    if(event.event==='model_decision')text='模型选择：'+(event.tools||[]).join(' → ');
    if(event.event==='model_decision_rejected')text='模型没有返回可执行的动作，本次已明确停止。';
    if(event.event==='tool'&&event.name==='search_workspace')text=`查询“${event.arguments?.query||''}”：返回 ${event.output?.hits?.length||0} 段原文`;
    if(event.event==='tool'&&event.name==='verify_sources')text=`核对 ${event.output?.chunk_ids?.length||0} 段原文的来源、位置与内容摘要`;
    if(event.event==='source_validation')text=`服务端核对全部 ${event.output?.chunk_ids?.length||0} 段已检索原文的来源、位置与内容摘要（不代表结论已获证实）`;
    if(event.event==='answer_contract')text=event.output?.requires_clarification?'问题中的所指对象不明确，本轮先请求澄清':'已按当前问题和用户历史检查回答对象；该检查不能识别所有歧义';
    if(event.event==='evidence_quality')text=`逐条检查 ${event.output?.checked_assessments??0} 项判断的选定引用，发现 ${event.output?.findings?.length||0} 项问题（规则检查不代表语义正确）`;
    if(event.event==='review_started')text='用同一模型单独复核证据判断（并非独立人工评审）';
    if(event.event==='tool'&&['submit_report','verify_and_submit_report'].includes(event.name))text='报告已保存，可逐条查看判定、引文及未通过的检查';
    if(event.event==='tool_rejected')text='工具参数或报告未通过检查，原因已记录';
    if(event.event==='stopped')text='执行已停止：'+(labels[event.state]||event.state);
    if(text)list.append(el('li',text));
  }
  box.append(list);const timings=runtimeTimings(agent);if(timings)box.append(timings);
  box.append(el('p',`快照：${agent.snapshot_id} · 资料摘要：${agent.documents_sha256}`,'small'));
  return box;
}
$('create-form').onsubmit=async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;try{const w=await api('/workspaces',{method:'POST',body:JSON.stringify({name:$('new-name').value.trim()})});$('new-name').value='';await loadWorkspaces(w.id);}catch(x){notice(x.message,true);}finally{b.disabled=false;}};
$('workspace').onchange=changeWorkspace;
$('file').onchange=async()=>{const f=$('file').files[0];if(!f||!workspace)return;const rev=generation,w=workspace;$('file').disabled=true;try{if(f.size>200000)throw new Error('请选择小于 200 KB 的文本文件。');const text=await f.text();if(text.length>20000)throw new Error('每份资料最多 20,000 个字符，请拆分后导入。');await api(`/workspaces/${w}/documents/${crypto.randomUUID()}`,{method:'PUT',body:JSON.stringify({title:f.name,text,source:f.name})});if(rev===generation){pending=null;invalidateConsent();await loadDocuments(rev);notice('资料已保存。');}}catch(e){if(rev===generation)notice(e.message,true);}finally{$('file').value='';$('file').disabled=!workspace;}};
$('sample').onclick=async()=>{const rev=generation,w=workspace;pending=null;invalidateConsent();renderConditions();$('sample').disabled=true;try{const response=await fetch('/example-corpus.json');if(!response.ok)throw new Error('示例读取失败');const data=await response.json();let count=0;for(const d of data.documents){await api(`/workspaces/${w}/documents/${encodeURIComponent(d.id)}`,{method:'PUT',body:JSON.stringify({title:d.title,text:d.text,source:d.source})});count++;if(rev===generation)notice(`正在导入 ${count}/${data.documents.length} 份公开资料…`);}if(rev===generation){pending=null;invalidateConsent();await loadDocuments(rev);notice(`已保存 ${count} 份公开资料。`);}}catch(e){if(rev===generation)notice(e.message,true);}finally{if(rev===generation)await loadDocuments(rev);$('sample').disabled=!workspace;}};
function turnBody(){
  return JSON.stringify({question:$('question').value.trim(),strategy:$('strategy').value,mode:conversation?'agent':$('mode').value,top_k:5,
    ...(($('strategy').value!=='bm25'||$('mode').value!=='extractive')?{approved_revision:approvedRevision}:{}),
    ...(globalThis.LabLensMemory?.selection()||{}),
    ...(conversation?{expected_conversation_revision:conversationRevision,state:stateDraft()}:{})});
}
async function sendIntent(intent){
  if(submitting)return;submitting=true;renderConditions();const rev=generation,w=workspace,c=conversation,cg=conversationGeneration,selection=selectionGeneration;let accepted=null;
  try{
    const path=c?`/workspaces/${w}/conversations/${c}/turns`:`/workspaces/${w}/tasks`;
    const t=await api(path,{method:'POST',headers:{'Idempotency-Key':intent.key},body:intent.body});
    accepted=t;
    if(rev===generation&&cg===conversationGeneration){
      if(pending===intent)pending.uncertain=false;
      if(selection===selectionGeneration){selectedTask=t.id;selectionGeneration++;renderTask(t);}
      if(c&&JSON.stringify(JSON.parse(intent.body).state)===JSON.stringify(stateDraft()))stateDirty=false;
      await loadTasks(rev);if(c)await loadConversation(rev,cg);
      notice(t.reused?'已找到同一请求的任务，没有重复启动。':'任务已保存，可以继续浏览资料。');
    }
  }catch(e){
    if(rev===generation&&cg===conversationGeneration){
      if(accepted){notice('任务已保存，但历史暂时未能刷新。请稍后点击“刷新”查看；无需重新提交。',true);}
      else if(['approval_required','stale_approval'].includes(e.code)){
        invalidateConsent();abandonPending();await loadDocuments(rev);notice('资料版本已变化，请核对当前资料并重新勾选发送许可后提交。',true);
      }else if(['memory_approval_required','stale_memory_approval','stale_memory_revision','stale_memory','memory_unavailable','invalid_memory_selection','memory_source_stale','memory_expired','memory_conflict'].includes(e.code)){
        invalidateConsent();abandonPending();await globalThis.LabLensMemory?.refresh();notice('选用的记忆已变化或不可使用。请刷新原文、重新选择记忆并确认发送许可。',true);
      }else if(e.code==='stale_conversation'){
        invalidateConsent();abandonPending();await loadConversation(rev,cg,{preserveDraft:true});stateDirty=true;
        $('conversation-draft').textContent='会话已有更新；你的草稿已保留，尚未覆盖已保存状态。';
        notice('会话已被其他页面更新。已刷新历史并保留你的问题与状态草稿，请核对后重新勾选发送许可。',true);
      }else if(e.code==='conversation_busy'){
        invalidateConsent();abandonPending();await loadConversation(rev,cg,{preserveDraft:true});
        notice('这个会话已有一轮正在处理。本次请求未排入队列，请等待完成后再手动提交。',true);
      }else if(e.code==='context_history_requires_new_conversation'){
        invalidateConsent();abandonPending();notice('用户历史已超过摘要容量，或存在缺失记录。本次未创建任务。请新建会话，明确重述目标、限制和未解决问题；当前草稿已保留。',true);
      }else if(!e.status||e.status>=500){
        if(pending===intent)pending.uncertain=true;
        notice('尚未确认提交结果。点击“确认上次提交”会使用原请求和原编号查询或完成同一次提交；不会自动重试。修改草稿将视为新的请求。',true);
      }else{abandonPending();notice(e.code==='context_budget_exceeded'?'上下文超出预算，请缩短目标与限制或新建会话后重新提交。':e.message,true);}
    }
  }finally{submitting=false;renderConditions();}
}
$('question-form').onsubmit=async e=>{
  e.preventDefault();if(submitting||$('submit').disabled)return;
  if(!$('question').value.trim()){notice('请先填写问题。',true);return;}
  if(conversation){const state=stateDraft();if(state.goal.length>2000||state.focus.length>300||state.constraints.length>12||state.constraints.some(x=>x.length>300)){
    notice('目标最多 2,000 字，讨论对象最多 300 字；限制条件最多 12 条，每条最多 300 字。请缩短后提交。',true);return;}}
  const body=turnBody();if(!pending||pending.body!==body||pending.workspace!==workspace||pending.conversation!==conversation)pending={body,workspace,conversation,key:crypto.randomUUID(),uncertain:false};
  await sendIntent(pending);
};
$('check-pending').onclick=async()=>{if(pending?.uncertain&&pending.workspace===workspace&&pending.conversation===conversation)await sendIntent(pending);};
for(const id of ['strategy','mode','question'])$(id).addEventListener('input',()=>{abandonPending();invalidateConsent();renderConditions();});
for(const id of ['conversation-goal','conversation-constraints','conversation-focus'])$(id).addEventListener('input',()=>{
  stateDirty=true;abandonPending();invalidateConsent();$('conversation-draft').textContent='有未保存的修改，将随下一轮提问保存。';renderConditions();
});
$('consent').onchange=()=>{approvedRevision=$('consent').checked?workspaceRevision:null;approvedContext=$('consent').checked?consentContext():null;if(!pending?.uncertain)abandonPending();renderConditions();};
$('conversation').onchange=changeConversation;
$('conversation-form').onsubmit=async e=>{
  e.preventDefault();if(!workspace||submitting)return;const rev=generation,w=workspace,b=$('create-conversation');b.disabled=true;
  try{const c=await api(`/workspaces/${w}/conversations`,{method:'POST',body:JSON.stringify({title:$('conversation-title').value.trim()})});
    if(rev===generation){$('conversation-title').value='';await loadConversations(rev,c.id);notice('会话已创建。填写目标与限制，再开始第一轮。');}}
  catch(x){if(rev===generation)notice(x.message,true);}finally{renderConditions();}
};
$('retry-task-detail').onclick=()=>loadTaskDetail({showLoading:taskDetailRecord?.id!==selectedTask});
$('load-more-tasks').onclick=()=>loadTaskPage(generation,{older:true}).catch(()=>{});
$('reset-history').onclick=()=>loadTaskPage(generation,{reset:true}).catch(()=>{});
$('retry-history').onclick=()=>loadTaskPage(generation,taskHistoryRetry||{}).catch(()=>{});
$('refresh').onclick=async()=>{try{await loadDocuments();await loadTasks();if(conversation)await loadConversation();}catch(e){notice(e.message,true);}};
function connectionStatus(message,state){
  $('connection').textContent=message;
  for(const name of ['online','degraded','offline'])$('connection-dot').classList.toggle(name,name===state);
}
async function systemStatus(){
  if(globalThis.LabLensAuth?.enabled&&!globalThis.LabLensAuth.user?.admin){
    if($('system-panel'))$('system-panel').hidden=true;connectionStatus('工作区已连接','online');return;
  }
  try{
    const s=await api('/system/status');$('system').textContent=JSON.stringify(s,null,2);
    if(s.database==='unavailable')connectionStatus('本地服务不可用 · 数据库连接失败','offline');
    else if(s.database!=='available')connectionStatus('本地服务状态未确认','unknown');
    else if(s.redis?.available===false||s.redis?.degraded===true)connectionStatus('本地服务已连接 · 缓存降级','degraded');
    else if(s.redis?.available===true)connectionStatus('本地服务已连接','online');
    else connectionStatus('本地服务已连接 · 缓存状态未知','unknown');
  }catch(e){connectionStatus('本地服务暂不可用','offline');}
}
globalThis.LabLensAccess={role:()=>workspaceRoles.get(workspace),workspace:()=>workspace,canWrite:canWriteWorkspace,documents:()=>docs,submitting:()=>submitting,
  memoryChanged:()=>{invalidateConsent();if(!submitting&&!pending?.uncertain)abandonPending();renderConditions();}};
async function initializeWorkspace(){
  if(globalThis.LabLensAuth&&!await globalThis.LabLensAuth.ready)return;
  await loadWorkspaces(localStorage.getItem('lablens.workspace')).catch(e=>notice(e.message,true));systemStatus();
}
initializeWorkspace();
setInterval(()=>{if(!document.hidden&&(!globalThis.LabLensAuth||globalThis.LabLensAuth.signedIn)){loadTasks().catch(()=>{});if(conversation)loadConversation().catch(()=>{});systemStatus();}},2500);
