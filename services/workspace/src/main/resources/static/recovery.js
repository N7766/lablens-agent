(() => {
  'use strict';
  const make=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const busy=new Set();
  async function call(path,options={}){
    const response=await LabLensAuth.request('/api'+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
    const body=await response.json();
    if(!response.ok)throw new Error(body.message||'恢复状态未确认');
    return body;
  }
  function render(task,container,access){
    if(task.request?.mode!=='agent'||!['FAILED','CANCELLED'].includes(task.status))return;
    const path=`/workspaces/${task.workspace_id}/tasks/${task.id}`,box=make('details');
    box.append(make('summary','检查中断与恢复'));container.append(box);
    const message=make('p','检查点沿用原资料版本；已完成步骤不重复执行。结果未知时须先核对，不能直接恢复。');message.className='small';
    const status=make('p'),refresh=make('button','核对恢复状态');refresh.type='button';refresh.className='link';
    const actions=make('div');box.append(message,status,refresh,actions);
    let ticket=0,metadata=null,uncertain=false;
    const active=()=>access.current();
    const writable=()=>active()&&access.canWrite()&&!access.submitting();
    function show(data){
      metadata=data;actions.replaceChildren();
      const labels={resumable:'存在已确认的完整工具边界，可申请继续。',unknown_outcome:'中断时可能已发出请求，结果未知；恢复已阻止。请核对服务端请求及用量记录。',no_checkpoint:'没有完整检查点，无法从中途恢复。',active:'原任务仍在执行或等待停止确认。',already_resumed:'已创建恢复任务，原记录继续保留。',unavailable:'当前任务不能恢复。'};
      status.textContent=(uncertain?'上次提交未得到明确响应；这里只查询状态，未自动重发。 ':'')+(labels[data.status]||data.reason||'当前任务不能恢复。');
      if(data.resumed_task_id){
        const open=make('button','查看恢复任务');open.type='button';open.className='secondary';open.onclick=()=>{if(active())return access.select(data.resumed_task_id);};actions.append(open);return;
      }
      if(data.status!=='resumable'||!access.canWrite()||uncertain)return;
      const label=make('label'),check=make('input');label.className='consent';check.type='checkbox';
      label.append(check,make('span',`允许继续发送原任务资料快照 ${data.snapshot_id}、问题及已冻结上下文给模型；沿用剩余预算，可能产生新的调用费用。`));
      const resume=make('button','从检查点继续');resume.type='button';resume.className='secondary';resume.disabled=true;
      check.onchange=()=>{resume.disabled=!check.checked||!writable()||busy.has(task.id);};
      resume.onclick=async()=>{
        if(!check.checked||!writable()||busy.has(task.id)||metadata!==data)return;
        const key=crypto.randomUUID();busy.add(task.id);resume.disabled=true;check.disabled=true;
        try{
          const child=await call(path+'/resume',{method:'POST',headers:{'Idempotency-Key':key},body:JSON.stringify({approved_snapshot_id:data.snapshot_id,expected_checkpoint_revision:data.checkpoint_revision})});
          if(active())await access.select(child.id);
        }catch(error){
          if(active()){uncertain=true;await load();if(active())status.textContent=error.message+'。'+status.textContent;}
        }finally{busy.delete(task.id);}
      };
      actions.append(label,resume);
    }
    async function load(){
      const token=++ticket;refresh.disabled=true;actions.replaceChildren();status.textContent='正在核对持久记录…';
      try{const data=await call(path+'/recovery');if(active()&&token===ticket)show(data);}
      catch(error){if(active()&&token===ticket)status.textContent='读取失败：'+error.message+'。请手动再次核对。';}
      finally{if(active()&&token===ticket)refresh.disabled=false;}
    }
    refresh.onclick=()=>load();
    // Read-only on render; a separate unchecked approval is required for POST.
    return load();
  }
  globalThis.LabLensRecovery={render};
})();
