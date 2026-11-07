'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../src/main/resources/static/auth.js'),'utf8');
const signed={enabled:true,authenticated:true,user:{id:'alice-id',username:'alice',admin:false},csrf_token:'fixture-csrf'};
function storage(values={}){return Object.assign({...values},{getItem(k){return this[k]??null;},setItem(k,v){this[k]=v;},removeItem(k){delete this[k];}});}
function harness(initial={enabled:true,authenticated:false},saved={}){
  const ids=['auth-panel','workspace-app','logout','account-label','auth-message','connection','auth-retry','login-form','login-submit','login-username','login-password'];
  const nodes=Object.fromEntries(ids.map(id=>[id,{hidden:false,value:'',disabled:false,textContent:''}]));
  const listeners={},calls=[],navigation=[],local=storage(saved),tab=storage({'lablens.import.pending.old':'private draft'});
  let next=null;
  const context=vm.createContext({addEventListener:(name,fn)=>listeners[name]=fn,document:{getElementById:id=>nodes[id]},localStorage:local,sessionStorage:tab,
    location:{replace:url=>navigation.push(url),reload:()=>navigation.push('reload')},
    fetch:async(url,options)=>{calls.push({url,options});const planned=next;next=null;const outcome=await planned;if(outcome instanceof Error)throw outcome;
      const status=outcome?.status||200,data=outcome?.data??initial;return {status,ok:status<400,json:async()=>data};}});
  vm.runInContext(source,context);
  return {api:context.LabLensAuth,listeners,nodes,calls,navigation,local,tab,next:value=>next=value};
}
test('anonymous startup gates the workspace and does not fetch any project',async()=>{
  const h=harness();assert.equal(await h.api.ready,false);assert.equal(h.nodes['workspace-app'].hidden,true);
  assert.deepEqual(h.calls.map(c=>c.url),['/api/auth/session']);assert.equal(h.nodes['auth-panel'].hidden,false);
});
test('switching identities removes project preferences and pending imported text',async()=>{
  const h=harness(signed,{'lablens.identity':'bob-id','lablens.workspace':'bob-workspace','unrelated':'keep'});
  assert.equal(await h.api.ready,true);assert.equal(h.local.getItem('lablens.workspace'),null);assert.equal(h.tab.getItem('lablens.import.pending.old'),null);
  assert.equal(h.local.getItem('unrelated'),'keep');assert.equal(h.local.getItem('lablens.identity'),'alice-id');
});
test('cookie writes bind CSRF and use same-origin credentials without service keys',async()=>{
  const h=harness(signed);await h.api.ready;await h.api.request('/api/workspaces/x/imports',{method:'POST',headers:{'Idempotency-Key':'same-intent'},body:'{}'});
  const o=h.calls.at(-1).options;assert.equal(o.headers['X-CSRF-Token'],'fixture-csrf');assert.equal(o.headers['Idempotency-Key'],'same-intent');
  assert.equal(o.credentials,'same-origin');assert.equal(o.redirect,'error');assert.equal(o.cache,'no-store');assert.equal(o.headers['X-LabLens-Service-Key'],undefined);
  await assert.rejects(h.api.request('https://elsewhere.invalid/api/run'),/地址无效/);
  await assert.rejects(h.api.request('/api/\\evil'),/地址无效/);
});
test('login clears entered password and reloads only after a valid response',async()=>{
  const h=harness();await h.api.ready;h.nodes['login-username'].value='alice';h.nodes['login-password'].value='test password';
  h.next({data:signed});await h.nodes['login-form'].onsubmit({preventDefault(){}});
  assert.equal(h.nodes['login-password'].value,'');assert.deepEqual(h.navigation,['/']);assert.equal(h.api.user.id,'alice-id');
});
test('failed login leaves protected content hidden and clears password',async()=>{
  const h=harness();await h.api.ready;h.nodes['login-password'].value='bad';h.next({status:401,data:{message:'凭据无效'}});
  await h.nodes['login-form'].onsubmit({preventDefault(){}});assert.equal(h.nodes['login-password'].value,'');assert.equal(h.nodes['workspace-app'].hidden,true);assert.deepEqual(h.navigation,[]);
});
test('expired session clears browser-held drafts once and reloads',async()=>{
  const h=harness(signed,{'lablens.identity':'alice-id','lablens.workspace':'private'});await h.api.ready;
  h.next({status:401,data:{}});await assert.rejects(h.api.request('/api/workspaces'),/身份/);assert.equal(h.api.signedIn,false);assert.equal(h.nodes['workspace-app'].hidden,true);
  assert.equal(h.local.getItem('lablens.workspace'),null);assert.deepEqual(h.navigation,['/']);
});
test('logout success clears state; unknown outcome preserves identity for explicit retry',async()=>{
  const h=harness(signed);await h.api.ready;h.next(new Error('network lost'));await h.nodes.logout.onclick();
  assert.equal(h.api.signedIn,true);assert.equal(h.nodes.logout.disabled,false);assert.deepEqual(h.navigation,[]);
  h.next({status:204});await h.nodes.logout.onclick();assert.equal(h.api.signedIn,false);assert.deepEqual(h.navigation,['/']);
});
test('session fetch failure and malformed identity fail closed',async()=>{
  for(const data of [{}, {enabled:true,authenticated:true,user:{id:'x',username:'x'}}]){
    const h=harness(data);assert.equal(await h.api.ready,false);assert.equal(h.nodes['workspace-app'].hidden,true);assert.equal(h.nodes['auth-retry'].hidden,false);
  }
});
test('changed permission discards old project DOM through reload before accepting more results',async()=>{
  const h=harness(signed);await h.api.ready;h.next({status:403,data:{message:'role changed'}});
  await assert.rejects(h.api.request('/api/workspaces/old/documents'),/身份/);assert.equal(h.api.signedIn,false);assert.equal(h.nodes['workspace-app'].hidden,true);assert.deepEqual(h.navigation,['/']);
});

test('another tab changing identity invalidates old account and stops pending request consumers',async()=>{
  const h=harness(signed);await h.api.ready;
  h.listeners.storage({storageArea:h.local,key:'lablens.identity',newValue:'bob-id'});
  assert.equal(h.api.signedIn,false);assert.equal(h.nodes['workspace-app'].hidden,true);
  await assert.rejects(h.api.request('/api/workspaces'),/身份/);assert.deepEqual(h.navigation,['/']);
});

test('an already in-flight response is rejected after another tab changes identity',async()=>{
  const h=harness(signed,{'lablens.identity':'alice-id','lablens.workspace':'alice-private'});await h.api.ready;
  let release;
  h.next(new Promise(resolve=>{release=resolve;}));
  let consumerRan=false;
  const inFlight=h.api.request('/api/workspaces/alice-private/tasks').then(response=>{
    consumerRan=true;return response.json();
  });
  const rejection=assert.rejects(inFlight,/已忽略先前的响应/);
  assert.equal(h.calls.at(-1).url,'/api/workspaces/alice-private/tasks');
  h.listeners.storage({storageArea:h.local,key:'lablens.identity',newValue:'bob-id'});
  release({status:200,data:{private_report:'alice old response'}});
  await rejection;
  assert.equal(consumerRan,false);assert.equal(h.api.signedIn,false);
  assert.equal(h.nodes['workspace-app'].hidden,true);assert.equal(h.local.getItem('lablens.workspace'),null);
  assert.deepEqual(h.navigation,['/']);
});
