/* The browser receives a session cookie, never the worker service credential. */
(() => {
  'use strict';
  const node=id=>document.getElementById(id);
  let session=null, invalidating=false;
  function clearSavedWorkspace(){
    for(const storage of [localStorage,sessionStorage]){
      for(const key of Object.keys(storage))if(key.startsWith('lablens.'))storage.removeItem(key);
    }
  }
  function display(message=''){
    const signed=!!session?.authenticated;
    node('auth-panel').hidden=signed;node('workspace-app').hidden=!signed;
    node('logout').hidden=!signed||session.enabled===false;
    node('account-label').textContent=signed?(session.enabled===false?'本机开发模式':session.user.username):'尚未登录';
    node('auth-message').textContent=message;
    if(!signed)node('connection').textContent='请登录工作区';
  }
  function accept(data){
    if(typeof data?.enabled!=='boolean'||typeof data?.authenticated!=='boolean'||
       (data.enabled&&data.authenticated&&(!data.user?.id||!data.user?.username||!data.csrf_token)))throw new Error('身份服务返回不完整，请重新连接。');
    session=data;
    const user=data.authenticated?(data.enabled===false?'local-development':data.user.id):'';
    if(localStorage.getItem('lablens.identity')!==user){clearSavedWorkspace();if(user)localStorage.setItem('lablens.identity',user);}
    display();
  }
  function expire(){
    if(invalidating)return;invalidating=true;session=null;clearSavedWorkspace();display('会话已结束，请重新登录。');
    // Reload discards in-flight callbacks, report DOM and drafts from this identity.
    location.replace('/');
  }
  async function request(path,options={}){
    if(invalidating)throw new Error('身份已变化，正在重新读取。');
    if(typeof path!=='string'||!path.startsWith('/api/')||path.includes('\\'))throw new Error('请求地址无效。');
    const method=(options.method||'GET').toUpperCase(),headers={'Content-Type':'application/json',...options.headers};
    if(!['GET','HEAD','OPTIONS'].includes(method)&&session?.csrf_token)headers['X-CSRF-Token']=session.csrf_token;
    const response=await fetch(path,{...options,method,headers,credentials:'same-origin',cache:'no-store',redirect:'error'});
    if(invalidating)throw new Error('身份已变化，已忽略先前的响应。');
    const responseUser=response.headers?.get('X-LabLens-User-Id');
    if(session?.authenticated&&((responseUser&&session.user?.id&&responseUser!==session.user.id)||(response.status===401&&path!=='/api/auth/login')||([403,404].includes(response.status)&&path.startsWith('/api/workspaces/')))){
      expire();throw new Error('身份或访问权限已变化，请重新查看项目。');
    }
    return response;
  }
  async function parsed(path,options){
    const r=await request(path,options),data=r.status===204?null:await r.json();
    if(!r.ok)throw new Error(data?.message||'请求未完成，请稍后重试。');return data;
  }
  const ready=(async()=>{
    try{accept(await parsed('/api/auth/session'));return !!session.authenticated;}
    catch(error){display(error.message);node('auth-retry').hidden=false;return false;}
  })();
  node('login-form').onsubmit=async event=>{
    event.preventDefault();const button=node('login-submit');if(button.disabled)return;button.disabled=true;
    try{
      const data=await parsed('/api/auth/login',{method:'POST',body:JSON.stringify({username:node('login-username').value.trim(),password:node('login-password').value})});
      node('login-password').value='';accept(data);location.replace('/');
    }catch(error){node('login-password').value='';display(error.message);}finally{button.disabled=false;}
  };
  node('logout').onclick=async()=>{
    node('logout').disabled=true;
    try{await parsed('/api/auth/logout',{method:'POST'});clearSavedWorkspace();session=null;display();location.replace('/');}
    catch(error){node('account-label').textContent='退出未确认，请重试';node('logout').disabled=false;}
  };
  node('auth-retry').onclick=()=>location.reload();
  globalThis.addEventListener?.('storage',event=>{
    const identity=session?.enabled===false?'local-development':session?.user?.id;
    if(session?.authenticated&&event.storageArea===localStorage&&(event.key===null||(event.key==='lablens.identity'&&event.newValue!==identity)))expire();
  });
  globalThis.LabLensAuth={ready,request,get signedIn(){return !!session?.authenticated;},get user(){return session?.user;},get enabled(){return session?.enabled;}};
})();
