'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {test}=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'../src/main/resources/static/recovery.js'),'utf8');
class Node{
  constructor(tag){this.tag=tag;this.children=[];this.disabled=false;this.checked=false;this.text='';}
  set textContent(value){this.text=String(value);this.children=[];}get textContent(){return this.text+this.children.map(n=>n.textContent).join('');}
  append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.text='';this.children=nodes;}
  all(tag){return this.children.flatMap(n=>[...(n.tag===tag?[n]:[]),...n.all(tag)]);}
}
const reply=(data,status=200)=>({ok:status<400,status,json:async()=>data});
function harness(){
  const requests=[],selected=[],container=new Node('div');let active=true,write=true,submitting=false,handler=null;
  const metadata={status:'resumable',snapshot_id:'w:1',checkpoint_revision:3};
  const ctx=vm.createContext({document:{createElement:tag=>new Node(tag)},crypto:{randomUUID:()=> 'fixed-request-key'},LabLensAuth:{request:async(url,options)=>{
    const req={url,method:options.method||'GET',body:options.body?JSON.parse(options.body):undefined,headers:options.headers};requests.push(req);
    return handler?handler(req):reply(req.method==='GET'?metadata:{id:'child'});
  }}});vm.runInContext(source,ctx);
  return {container,requests,selected,metadata,setActive:value=>active=value,setWrite:value=>write=value,setSubmitting:value=>submitting=value,setHandler:value=>handler=value,
    render:()=>ctx.LabLensRecovery.render({id:'parent',workspace_id:'w',status:'FAILED',request:{mode:'agent'}},container,{current:()=>active,canWrite:()=>write,submitting:()=>submitting,select:id=>selected.push(id)}),
    button:text=>container.all('button').find(b=>b.textContent===text),check:()=>container.all('input')[0]};
}
test('render only reads; unchecked separate consent blocks resume',async()=>{
  const h=harness();await h.render();assert.equal(h.requests.length,1);assert.equal(h.requests[0].method,'GET');
  await h.button('从检查点继续').onclick();assert.equal(h.requests.length,1);assert.equal(h.check().checked,false);
  h.check().checked=true;h.check().onchange();await h.button('从检查点继续').onclick();
  const post=h.requests[1];assert.equal(post.method,'POST');assert.deepEqual(post.body,{approved_snapshot_id:'w:1',expected_checkpoint_revision:3});assert.equal(post.headers['Idempotency-Key'],'fixed-request-key');assert.deepEqual(h.selected,['child']);
});
test('unknown outcome and viewers cannot resume',async()=>{
  for(const viewer of [false,true]){const h=harness();if(viewer)h.setWrite(false);else h.metadata.status='unknown_outcome';await h.render();assert.equal(h.check(),undefined);assert.equal(h.button('从检查点继续'),undefined);assert.equal(h.requests.length,1);}
});
test('lost write response reconciles via GET and finds unique child without replay',async()=>{
  const h=harness();await h.render();h.setHandler(req=>{if(req.method==='POST')throw new Error('lost response');return reply({...h.metadata,status:'already_resumed',resumed_task_id:'child'});});
  h.check().checked=true;await h.button('从检查点继续').onclick();assert.deepEqual(h.requests.map(r=>r.method),['GET','POST','GET']);
  assert.match(h.container.textContent,/未自动重发/);await h.button('查看恢复任务').onclick();assert.deepEqual(h.selected,['child']);
});
test('uncertain write with no reconciled child remains read-only until fresh view',async()=>{
  const h=harness();await h.render();h.setHandler(req=>{if(req.method==='POST')throw new Error('lost response');return reply(h.metadata);});h.check().checked=true;await h.button('从检查点继续').onclick();
  await h.button('核对恢复状态').onclick();assert.equal(h.requests.filter(r=>r.method==='POST').length,1);assert.equal(h.button('从检查点继续'),undefined);
});
test('cross-workspace delayed reads and clicks cannot mutate or navigate',async()=>{
  const h=harness();let release;h.setHandler(()=>new Promise(done=>release=done));const reading=h.render();h.setActive(false);release(reply(h.metadata));await reading;assert.equal(h.check(),undefined);
  const k=harness();await k.render();k.check().checked=true;k.setActive(false);await k.button('从检查点继续').onclick();assert.equal(k.requests.length,1);
});
test('repeated clicks while pending send one POST and cannot reset approval',async()=>{
  const h=harness();await h.render();let release;h.setHandler(()=>new Promise(done=>release=done));h.check().checked=true;const button=h.button('从检查点继续'),pending=button.onclick();await button.onclick();assert.equal(h.requests.filter(r=>r.method==='POST').length,1);release(reply({id:'child'}));await pending;
});
test('role loss and concurrent ordinary submission block a stale resume button',async()=>{
  for(const lostRole of [false,true]){const h=harness();await h.render();h.check().checked=true;if(lostRole)h.setWrite(false);else h.setSubmitting(true);await h.button('从检查点继续').onclick();assert.equal(h.requests.length,1);}
});
test('untrusted reason renders as text only',async()=>{const h=harness();h.metadata.status='new-code';h.metadata.reason='<img src=x onerror=alert(1)>';await h.render();assert.match(h.container.textContent,/<img/);assert.equal(h.container.innerHTML,undefined);});
