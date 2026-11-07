'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {test}=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'../src/main/resources/static/memory.js'),'utf8');
const ids=['memory-panel','memory-summary','memory-notice','memory-list','memory-editor','memory-edit-title','memory-form','memory-key','memory-document','memory-quote','memory-note','memory-expires','memory-save','memory-reset','memory-refresh','mode'];
class Node{
 constructor(tag){this.tag=tag;this.children=[];this.value='';this.text='';this.dataset={};this.disabled=false;this.checked=false;}
 set textContent(text){this.text=String(text);this.children=[];}get textContent(){return this.text+this.children.map(n=>n.textContent).join('');}
 append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.text='';this.children=nodes;}
 setAttribute(key,value){this[key]=value;}querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)]);}
}
const row=(id='one',status='active')=>({id,key:id,version:1,status,scope:'workspace',note:'Untrusted note',expires_at:null,source:{document_id:'doc',document_version:2,quote:' exact quote '}});
const plain=value=>JSON.parse(JSON.stringify(value));
const reply=(data,status=200)=>({ok:status<400,status,json:async()=>plain(data)});
function harness(){
 const nodes=Object.fromEntries(ids.map(id=>[id,new Node('div')]));let workspace='w',canWrite=true,submitting=false,revision=3,rows=[row()],handler=null,changes=0;const requests=[];
 nodes.mode.value='agent';const context=vm.createContext({document:{getElementById:id=>nodes[id],createElement:tag=>new Node(tag)},
 LabLensAccess:{workspace:()=>workspace,canWrite:()=>canWrite,submitting:()=>submitting,documents:()=>[{id:'doc',title:'Document',version:2}],memoryChanged:()=>changes++},
 LabLensAuth:{request:async(url,options={})=>{const req={url,method:options.method||'GET',body:options.body?JSON.parse(options.body):undefined};requests.push(req);return handler?handler(req):reply({workspace_id:workspace,revision,memories:rows});}}});
 vm.runInContext(source,context);
 return {nodes,requests,refresh:options=>context.LabLensMemory.refresh(options),selection:()=>plain(context.LabLensMemory.selection()),
 conditions:()=>context.LabLensMemory.conditions(),setRows:value=>rows=value,setWorkspace:value=>workspace=value,setHandler:value=>handler=value,
 setWrite:value=>canWrite=value,setSubmitting:value=>submitting=value,changes:()=>changes,
 select:(index=0,value=true)=>{const check=nodes['memory-list'].querySelectorAll('input')[index];check.checked=value;check.onchange();},
 button:label=>nodes['memory-list'].querySelectorAll('button').find(b=>b['aria-label']===label),
 submit:()=>nodes['memory-form'].onsubmit({preventDefault(){}})};
}
test('memory is never auto-selected, selected IDs and revision are explicit, non-Agent excludes them',async()=>{
 const h=harness();await h.refresh();assert.deepEqual(h.selection(),{});h.select();assert.deepEqual(h.selection(),{memory_ids:['one'],approved_memory_revision:3});
 h.nodes.mode.value='extractive';h.conditions();assert.deepEqual(h.selection(),{});assert.equal(h.nodes['memory-list'].querySelectorAll('input')[0].disabled,true);
 await h.refresh();assert.deepEqual(h.selection(),{});
});
test('stale and expired memory cannot be selected; viewer cannot mutate or select',async()=>{
 const h=harness();h.setRows([row('stale','stale'),row('expired','expired'),row()]);await h.refresh();h.select(0);h.select(1);assert.deepEqual(h.selection(),{});
 h.setWrite(false);await h.refresh();h.select(2);assert.deepEqual(h.selection(),{});assert.equal(h.nodes['memory-editor'].hidden,true);assert.equal(h.nodes['memory-list'].querySelectorAll('button').length,0);
});
test('selection capped at eight and changes invalidate the containing approval context',async()=>{
 const h=harness();h.setRows(Array.from({length:9},(_,i)=>row('m'+i)));await h.refresh();const before=h.changes();for(let i=0;i<9;i++)h.select(i);
 assert.equal(h.selection().memory_ids.length,8);assert.equal(h.changes()-before,8);assert.match(h.nodes['memory-notice'].textContent,/最多选择 8/);
});
test('workspace switch drops delayed old reads and clears selection immediately',async()=>{
 const h=harness();await h.refresh();h.select();let release;h.setHandler(()=>new Promise(done=>release=done));const old=h.refresh();assert.deepEqual(h.selection(),{});
 h.setWorkspace('new');h.setHandler(()=>reply({revision:7,memories:[row('new')]}));await h.refresh();release(reply({revision:3,memories:[row('old')]}));await old;
 assert.match(h.nodes['memory-list'].textContent,/new/);assert.doesNotMatch(h.nodes['memory-list'].textContent,/old/);h.select();assert.equal(h.selection().approved_memory_revision,7);
});
test('creation binds exact source version and preserves quote spacing',async()=>{
 const h=harness();await h.refresh();h.nodes['memory-key'].value='new';h.nodes['memory-document'].value='doc';h.nodes['memory-quote'].value=' exact quote ';h.nodes['memory-note'].value=' note ';
 await h.submit();const post=h.requests.find(r=>r.method==='POST');assert.deepEqual(post.body,{key:'new',document_id:'doc',document_version:2,quote:' exact quote ',note:' note ',expires_at:null,expected_revision:3});assert.deepEqual(h.selection(),{});
});
test('explicit edit uses both guards; conflict reconciles read-only and preserves draft',async()=>{
 const h=harness();await h.refresh();h.button('编辑记忆 one').onclick();h.nodes['memory-note'].value='my edit';h.setHandler(req=>req.method==='GET'?reply({revision:4,memories:[row()]}):reply({error:'stale_memory',message:'Version conflict'},409));
 await h.submit();const put=h.requests.find(r=>r.method==='PUT');assert.equal(put.body.expected_revision,3);assert.equal(put.body.expected_version,1);assert.equal(put.body.note,'my edit');
 assert.equal(h.requests.filter(r=>r.method==='PUT').length,1);assert.equal(h.nodes['memory-note'].value,'my edit');assert.match(h.nodes['memory-notice'].textContent,/未自动重试/);
});
test('lost delete response is never retried; history retention remains visible',async()=>{
 const h=harness();await h.refresh();h.setHandler(req=>{if(req.method==='DELETE')throw new Error('Connection lost');return reply({revision:4,memories:[]});});
 await h.button('删除记忆 one').onclick();assert.equal(h.requests.filter(r=>r.method==='DELETE').length,1);assert.deepEqual(h.requests.find(r=>r.method==='DELETE').body,{expected_revision:3,expected_version:1});assert.match(h.nodes['memory-notice'].textContent,/未自动重试/);assert.deepEqual(h.selection(),{});
});
test('pending submission freezes selection and editing',async()=>{
 const h=harness();await h.refresh();h.setSubmitting(true);h.conditions();h.select();assert.deepEqual(h.selection(),{});assert.equal(h.nodes['memory-save'].disabled,true);h.button('编辑记忆 one').onclick();assert.equal(h.nodes['memory-key'].value,'');
});
test('memory text renders through textContent, never HTML execution',async()=>{
 const h=harness(),item=row();item.note='<img src=x onerror=alert(1)>';item.source.quote='<script>bad()</script>';h.setRows([item]);await h.refresh();assert.match(h.nodes['memory-list'].textContent,/<script>bad/);assert.equal(h.nodes['memory-list'].innerHTML,undefined);
});
