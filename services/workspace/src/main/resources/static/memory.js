(() => {
  'use strict';
  const node=id=>document.getElementById(id);
  const make=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const selected=new Set();
  let epoch=0,busy=false,revision=null,rows=[],editing=null;
  const changed=()=>globalThis.LabLensAccess?.memoryChanged();
  const writable=()=>LabLensAccess.canWrite()&&!LabLensAccess.submitting();
  function selection(){
    if(node('mode').value!=='agent'||revision===null||!selected.size)return {};
    return {memory_ids:[...selected].sort(),approved_memory_revision:revision};
  }
  async function call(path,options){
    const response=await LabLensAuth.request('/api'+path,options);
    const data=response.status===204?null:await response.json();
    if(!response.ok){const error=new Error(data?.message||'记忆操作未完成');error.code=data?.error;throw error;}
    return data;
  }
  function reset(){
    editing=null;node('memory-edit-title').textContent='保存一条记忆';
    for(const id of ['memory-key','memory-quote','memory-note','memory-expires'])node(id).value='';
    node('memory-save').textContent='保存记忆';
  }
  function localTime(value){
    if(!value)return '';const date=new Date(value);
    return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);
  }
  function conditions(){
    node('memory-editor').hidden=!LabLensAccess.canWrite();
    for(const id of ['memory-save','memory-reset','memory-key','memory-document','memory-quote','memory-note','memory-expires'])node(id).disabled=busy||!writable()||revision===null;
    for(const input of node('memory-list').querySelectorAll('input'))input.disabled=busy||!writable()||node('mode').value!=='agent'||input.dataset.status!=='active';
    for(const button of node('memory-list').querySelectorAll('button'))button.disabled=busy||!writable();
    node('memory-summary').textContent=`${rows.length} 条 · 本次选用 ${node('mode').value==='agent'?selected.size:0} 条`;
  }
  function render(){
    node('memory-list').replaceChildren();
    for(const item of rows){
      const box=make('div'),label=make('label'),check=make('input');box.className='memory-row';label.className='consent';
      check.type='checkbox';check.checked=selected.has(item.id);check.dataset.status=item.status;
      check.setAttribute('aria-label',`选用记忆 ${item.key}`);
      check.onchange=()=>{
        if(!writable()||item.status!=='active'||node('mode').value!=='agent'){check.checked=selected.has(item.id);return;}
        if(check.checked&&selected.size>=8){check.checked=false;node('memory-notice').textContent='每次最多选择 8 条。';return;}
        if(check.checked)selected.add(item.id);else selected.delete(item.id);changed();conditions();
      };
      label.append(check,make('span',`${item.key} · v${item.version} · ${({active:'可选用',stale:'原文已变更',expired:'已过期'})[item.status]||'不可选用'}`));
      const details=make('details');details.append(make('summary','查看原文与备注'),make('blockquote',item.source.quote),make('p',item.note||'无备注'));
      details.append(make('small',`资料 ${item.source.document_id} · v${item.source.document_version} · ${item.expires_at?'到期 '+new Date(item.expires_at).toLocaleString():'无到期时间'}`));
      box.append(label,details);
      if(LabLensAccess.canWrite()){
        const edit=make('button','编辑'),remove=make('button','删除记忆');edit.type=remove.type='button';edit.className=remove.className='link';
        edit.setAttribute('aria-label',`编辑记忆 ${item.key}`);remove.setAttribute('aria-label',`删除记忆 ${item.key}`);
        edit.onclick=()=>{
          if(busy||!writable())return;editing=item;node('memory-editor').open=true;node('memory-edit-title').textContent=`编辑 ${item.key}`;
          node('memory-key').value=item.key;node('memory-document').value=item.source.document_id;node('memory-quote').value=item.source.quote;
          node('memory-note').value=item.note||'';node('memory-expires').value=localTime(item.expires_at);node('memory-save').textContent='保存修改';
        };
        remove.onclick=()=>mutate('DELETE',item.id,{expected_revision:revision,expected_version:item.version});
        box.append(edit,remove);
      }
      node('memory-list').append(box);
    }
    if(!rows.length)node('memory-list').append(make('p','尚未保存记忆。'));conditions();
  }
  async function refresh({preserveDraft=false}={}){
    const w=LabLensAccess.workspace(),ticket=++epoch;
    revision=null;rows=[];selected.clear();changed();
    node('memory-panel').hidden=!w;node('memory-notice').textContent='';
    if(!preserveDraft)reset();render();
    const oldDocument=node('memory-document').value;node('memory-document').replaceChildren(make('option','请选择资料'));
    node('memory-document').children[0].value='';
    for(const doc of LabLensAccess.documents()){const option=make('option',doc.title);option.value=doc.id;node('memory-document').append(option);}
    if(preserveDraft)node('memory-document').value=oldDocument;
    if(!w)return;
    try{const data=await call(`/workspaces/${w}/memories`);if(ticket!==epoch||w!==LabLensAccess.workspace())return;
      revision=data.revision;rows=data.memories;render();
    }catch(error){if(ticket===epoch){node('memory-notice').textContent='读取记忆失败：'+error.message;conditions();}}
  }
  async function mutate(method,id,body){
    if(busy||!writable()||revision===null)return;
    const w=LabLensAccess.workspace(),ticket=epoch;busy=true;conditions();
    try{
      await call(`/workspaces/${w}/memories${id?'/'+encodeURIComponent(id):''}`,{method,body:JSON.stringify(body)});
      if(ticket!==epoch||w!==LabLensAccess.workspace())return;
      await refresh();node('memory-notice').textContent=method==='DELETE'?'记忆已删除；已保存任务的历史快照继续保留。':'记忆已保存。请手动选择并重新确认发送许可。';
    }catch(error){
      if(ticket!==epoch||w!==LabLensAccess.workspace())return;
      // Lost response is ambiguous. Reconcile with GET, never replay a mutation.
      await refresh({preserveDraft:true});node('memory-notice').textContent=error.message+'。已重新读取列表，草稿保留；请核对后操作，未自动重试。';
    }finally{busy=false;conditions();}
  }
  node('memory-form').onsubmit=async event=>{
    event.preventDefault();if(busy||!writable()||revision===null)return;
    const doc=LabLensAccess.documents().find(d=>d.id===node('memory-document').value);
    if(!doc){node('memory-notice').textContent='请选择当前资料。';return;}
    const expires=node('memory-expires').value;
    if(expires&&Number.isNaN(new Date(expires).getTime())){node('memory-notice').textContent='请填写有效到期时间。';return;}
    await mutate(editing?'PUT':'POST',editing?.id,{key:node('memory-key').value.trim(),document_id:doc.id,document_version:doc.version,
      quote:node('memory-quote').value,note:node('memory-note').value,expires_at:expires?new Date(expires).toISOString():null,
      expected_revision:revision,...(editing?{expected_version:editing.version}:{})});
  };
  node('memory-refresh').onclick=()=>refresh({preserveDraft:true});node('memory-reset').onclick=reset;
  globalThis.LabLensMemory={refresh,selection,conditions};
})();
