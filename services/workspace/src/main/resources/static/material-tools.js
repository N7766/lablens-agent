/* Separate, opt-in material operations. Refresh never initiates provider work. */
(() => {
  'use strict';
  const node=id=>document.getElementById(id), selected=()=>node('workspace').value;
  let busy=false,selectionEpoch=0,operationEpoch=0;
  const key=(w,type)=>`lablens.import.${type}.${w}`;
  const current=w=>w===selected()&&operationEpoch===selectionEpoch;
  function message(id,text,w){if(current(w))node(id).textContent=text;}
  async function request(path,options={}){
    const response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
    const body=await response.json();if(!response.ok){const error=new Error(body.message||body.error||`请求失败 (${response.status})`);error.status=response.status;throw error;}return body;
  }
  function renderBatch(batch,w){
    if(!current(w))return;
    const labels={QUEUED:'排队中',APPLIED:'整批已保存',FAILED:'导入失败',CANCELLED:'已取消'};
    message('bulk-status',`${labels[batch.status]||batch.status} · ${batch.document_count} 份资料${batch.applied_revision!==null?` · 版本 ${batch.applied_revision}`:''}${batch.error_message?` · ${batch.error_message}`:''}。索引需另行检查。`,w);
    node('bulk-cancel').hidden=batch.status!=='QUEUED';
  }
  async function operate(id,fn){
    const w=selected();if(!w){node(id).textContent='请先选择项目。';return;}
    if(busy)return;busy=true;operationEpoch=selectionEpoch;
    try{await fn(w);}catch(error){message(id,error.message,w);}finally{busy=false;}
  }
  async function inspectBatch(w){
    if(sessionStorage.getItem(key(w,'pending'))){message('bulk-status','上次提交结果不明，点击“提交批次”使用原幂等键确认。',w);return;}
    const id=localStorage.getItem(key(w,'last'));
    if(!id){message('bulk-status',sessionStorage.getItem(key(w,'pending'))?'上次提交结果不明，点击“提交批次”使用原幂等键确认。':'当前浏览器没有此项目的批次记录。',w);return;}
    renderBatch(await request(`/workspaces/${w}/imports/${id}`),w);
  }
  node('bulk-submit').onclick=()=>operate('bulk-status',async w=>{
    let pending=JSON.parse(sessionStorage.getItem(key(w,'pending'))||'null');
    if(!pending){
      const files=Array.from(node('bulk-files').files);
      if(!files.length||files.length>25)throw new Error('请选择 1–25 份文本文件。');
      const documents=[];let total=0;
      for(const file of files){
        if(file.size>200000)throw new Error('单个文件不能超过 200 KB。');
        const text=await file.text();total+=text.length;
        if(!text.trim()||text.length>20000||total>100000)throw new Error('每份资料须为 1–20,000 字，整批不超过 100,000 字。');
        documents.push({id:crypto.randomUUID(),title:file.name,text,source:file.name});
      }
      const snapshot=await request(`/workspaces/${w}/documents`);
      if(!current(w))return;
      pending={key:crypto.randomUUID(),body:{expected_revision:snapshot.revision,documents}};
      sessionStorage.setItem(key(w,'pending'),JSON.stringify(pending));
    }
    message('bulk-status','正在确认批次提交…',w);
    let batch;
    try{batch=await request(`/workspaces/${w}/imports`,{method:'POST',headers:{'Idempotency-Key':pending.key},body:JSON.stringify(pending.body)});}
    catch(error){
      if([400,404,409,413,422].includes(error.status)){sessionStorage.removeItem(key(w,'pending'));throw new Error(`${error.message}。请求未受理，请核对文件与当前资料版本后重新提交。`);}
      throw new Error(`${error.message}。本页保留原请求；再次点击提交会使用同一幂等键。`);
    }
    localStorage.setItem(key(w,'last'),batch.id);sessionStorage.removeItem(key(w,'pending'));renderBatch(batch,w);
    if(current(w))node('bulk-files').value='';
  });
  node('bulk-refresh').onclick=()=>operate('bulk-status',async w=>{await inspectBatch(w);if(current(w))node('refresh').click();});
  node('bulk-cancel').onclick=()=>operate('bulk-status',async w=>{
    const id=localStorage.getItem(key(w,'last'));if(!id)return;
    renderBatch(await request(`/workspaces/${w}/imports/${id}/cancel`,{method:'POST'}),w);
  });
  function renderIndex(result,w){
    const labels={ready:'索引已就绪',missing:'当前版本尚无索引',building:'索引正在建立',failed:'索引建立失败',disabled:'持久索引未启用',stale:'资料已变化，旧索引不适用于当前版本'};
    const state=result.workspace_index_status||result.status;
    message('index-status',`${labels[state]||state} · 资料版本 ${result.revision}${result.chunk_count!==undefined?` · ${result.chunk_count} 个片段`:''}`,w);
  }
  node('index-refresh').onclick=()=>operate('index-status',async w=>renderIndex(await request(`/workspaces/${w}/index?strategy=bm25`),w));
  node('index-build').onclick=()=>operate('index-status',async w=>{
    const snapshot=await request(`/workspaces/${w}/documents`);
    if(!current(w))return;
    message('index-status','正在建立当前版本的关键词索引…',w);
    renderIndex(await request(`/workspaces/${w}/index`,{method:'POST',body:JSON.stringify({strategy:'bm25',expected_revision:snapshot.revision})}),w);
  });
  node('workspace').addEventListener('change',()=>{
    selectionEpoch++;
    node('bulk-status').textContent='点击查看上次批次，或提交新的批次。';node('bulk-cancel').hidden=true;
    node('index-status').textContent='尚未检查当前资料版本的索引。';node('bulk-files').value='';
  });
})();
