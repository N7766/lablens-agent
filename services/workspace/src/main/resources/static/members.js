(() => {
  'use strict';
  const node=id=>document.getElementById(id);
  let epoch=0,busy=false;
  async function call(path,options){
    const response=await LabLensAuth.request('/api'+path,options);
    const data=response.status===204?null:await response.json();
    if(!response.ok)throw new Error(data?.message||'成员操作未完成');return data;
  }
  async function refresh(){
    const w=LabLensAccess.workspace(),ticket=++epoch;
    node('members-panel').hidden=LabLensAccess.role()!=='OWNER';node('members-list').replaceChildren();
    node('members-notice').textContent='';node('member-username').value='';
    if(node('members-panel').hidden)return;
    try{
      const data=await call(`/workspaces/${w}/members`);if(ticket!==epoch)return;
      for(const item of data.members){
        const row=document.createElement('div'),label=document.createElement('span');
        label.textContent=`${item.username} · ${({OWNER:'所有者',EDITOR:'可编辑',VIEWER:'只读'})[item.role]}`;row.append(label);
        if(item.role!=='OWNER'){
          const button=document.createElement('button');button.type='button';button.className='link';button.textContent='移除';button.setAttribute('aria-label',`移除成员 ${item.username}`);
          button.onclick=async()=>{
            if(busy||ticket!==epoch)return;busy=true;button.disabled=true;
            try{await call(`/workspaces/${w}/members/${encodeURIComponent(item.username)}`,{method:'DELETE'});if(ticket===epoch)await refresh();}
            catch(error){if(ticket===epoch)node('members-notice').textContent=error.message;}
            finally{busy=false;button.disabled=false;}
          };row.append(button);
        }
        node('members-list').append(row);
      }
    }catch(error){if(ticket===epoch)node('members-notice').textContent=error.message;}
  }
  node('member-form').onsubmit=async event=>{
    event.preventDefault();if(busy||LabLensAccess.role()!=='OWNER')return;
    const w=LabLensAccess.workspace(),ticket=epoch,username=node('member-username').value.trim(),role=node('member-role').value;
    if(!username)return;busy=true;node('member-save').disabled=true;
    try{await call(`/workspaces/${w}/members/${encodeURIComponent(username)}`,{method:'PUT',body:JSON.stringify({role})});if(ticket===epoch){await refresh();node('members-notice').textContent='成员权限已保存。';}}
    catch(error){if(ticket===epoch)node('members-notice').textContent=error.message;}
    finally{busy=false;node('member-save').disabled=false;}
  };
  globalThis.LabLensMembers={refresh};
})();
