'use strict';
// Dependency-free DOM harness for the actual page script. No network, database or model calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../src/main/resources/static/workspace.js'), 'utf8');
const ids = ['notice', 'strategy', 'mode', 'consent-wrap', 'cost-note', 'submit', 'consent', 'workspace',
  'documents', 'tasks', 'answer', 'file', 'sample', 'result-status', 'revision', 'create-form', 'new-name',
  'question-form', 'question', 'refresh', 'system', 'connection', 'connection-dot'];

class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.value=''; this.disabled=false; this.checked=false;
    this.classList={toggle(){},add(){},remove(){}}; this.listeners={}; this._text=''; }
  set textContent(value) { this._text=String(value); this.children=[]; }
  get textContent() { return this._text+this.children.map(n=>n.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text=''; this.children=nodes; }
  setAttribute(name,value) { this[name]=value; }
  addEventListener(name,fn) { this.listeners[name]=fn; }
  focus() { this.focused=true; }
}
function walk(node, predicate) {
  if(predicate(node)) return node;
  for(const child of node.children) { const found=walk(child,predicate); if(found)return found; }
  return null;
}
function plain(value) { return JSON.parse(JSON.stringify(value)); }
function usage(unknown=false) {
  const count=unknown?null:0;
  return {model_calls:count,embedding_requests:count,embedded_inputs:count,embedding_cache_hits:count,
    embedding_tokens:count,generation_tokens:{prompt_tokens:count,completion_tokens:count,total_tokens:count},
    provider_usage_unknown:unknown,budget_exhausted:false,cache_reused:false};
}
function failed(overrides={}) {
  return {id:'failed-1',workspace_id:'workspace-1',status:'FAILED',snapshot_revision:1,result:null,
    request:{question:'What is measured?',strategy:'bm25',mode:'extractive',top_k:5},
    error_code:'provider_failed',error_message:'Safe worker failure',execution_usage:usage(),
    usage_unknown:false,error_result:null,cache_hit:false,worker_ms:5,...overrides};
}
function harness() {
  const elements=Object.fromEntries(ids.map(id=>{const node=new Element('div');node.id=id;return[id,node];}));
  const posts=[]; let nextId=0,taskRows=[],postFailure=false;
  const get=id=>elements[id]||Object.values(elements).map(root=>walk(root,n=>n.id===id)).find(Boolean)||null;
  const context=vm.createContext({document:{getElementById:get,createElement:tag=>new Element(tag),hidden:false},
    localStorage:{getItem(){return null;},setItem(){}},Option:function(text,value){const n=new Element('option');n.textContent=text;n.value=value;return n;},
    crypto:{randomUUID:()=>`key-${++nextId}`},setInterval(){},
    fetch:async(url,options={})=>{
      let body={};
      if(options.method==='POST') {
        posts.push({url,key:options.headers['Idempotency-Key'],body:JSON.parse(options.body)});
        if(postFailure)throw new Error('Connection lost after submission');
        body=failed({request:JSON.parse(options.body),reused:posts.length>1});
      } else if(url.endsWith('/documents')) body={documents:[{id:'doc',title:'Document'}],revision:1};
      else if(url.endsWith('/tasks')) body={tasks:taskRows};
      return {status:200,ok:true,json:async()=>body};
    }});
  // Do not boot the page's polling loop. Every function and event handler is otherwise the real source.
  vm.runInContext(source.slice(0,source.indexOf("loadWorkspaces(localStorage.getItem")),context);
  vm.runInContext("workspace='workspace-1'; docs=[{id:'doc'}];",context);
  get('workspace').value='workspace-1';get('strategy').value='bm25';get('mode').value='extractive';
  get('question').value='What is measured?';vm.runInContext('renderConditions()',context);
  const invoke=(name,value)=>{context.argument=value;return vm.runInContext(`${name}(argument)`,context);};
  return {get,posts,render:task=>invoke('renderTask',task),
    details:()=>JSON.parse(walk(get('answer'),n=>n.tagName==='pre').textContent),
    submit:()=>get('question-form').onsubmit({preventDefault(){}}),
    retry:()=>get('prepare-retry').onclick(),
    pending:()=>plain(vm.runInContext('pending',context)),
    selected:()=>vm.runInContext('selectedTask',context),
    setSubmitting:value=>{context.argument=value;vm.runInContext('submitting=argument;renderConditions()',context);},
    switchWorkspace:async value=>{get('workspace').value=value;await vm.runInContext('changeWorkspace()',context);},
    loadTasks:async tasks=>{taskRows=tasks;await vm.runInContext('loadTasks()',context);},
    failPost:value=>{postFailure=value;}};
}

test('failed task without a result exposes known partial usage and safe error details',()=>{
  const h=harness(),known={...usage(),embedding_requests:2,embedding_tokens:132};
  const safe={status:'provider_error',usage:known,error:'Provider request failed; no automatic retry was sent.'};
  h.render(failed({execution_usage:known,error_result:safe}));
  assert.equal(h.details().task_id,'failed-1');assert.equal(h.details().status,'FAILED');
  assert.equal(h.details().execution_usage.embedding_requests,2);assert.deepEqual(h.details().error_result,safe);
});

test('unknown failure usage remains null with an explicit uncertainty message',()=>{
  const h=harness();h.render(failed({execution_usage:usage(true),usage_unknown:true}));
  assert.equal(h.details().execution_usage.model_calls,null);
  assert.equal(h.details().execution_usage.generation_tokens.total_tokens,null);
  assert.match(h.get('answer').textContent,/用量可能未知/);assert.match(h.get('answer').textContent,/不能当作 0/);
});

test('known zero usage stays zero and missing usage is not fabricated',()=>{
  const h=harness();h.render(failed());assert.equal(h.details().execution_usage.model_calls,0);
  h.render(failed({execution_usage:null}));assert.equal(h.details().execution_usage,null);
});

test('successful cache hits retain distinct current and historical usage',()=>{
  const h=harness(),current={...usage(),cache_reused:true},origin={...usage(),embedding_requests:3};
  h.render(failed({status:'SUCCEEDED',cache_hit:true,execution_usage:current,origin_usage:origin,
    result:{status:'excerpts',citations:[],usage:current,origin_usage:origin,retrieval:{hits:[]}}}));
  assert.equal(h.details().execution_usage.embedding_requests,0);assert.equal(h.details().origin_usage.embedding_requests,3);
  assert.equal(h.get('prepare-retry'),null);
});

test('replaying the same intent keeps the same idempotency key after failure',async()=>{
  const h=harness();await h.submit();await h.submit();
  assert.equal(h.posts.length,2);assert.equal(h.posts[0].key,h.posts[1].key);
});

test('preparing retry sends nothing; the later manual submission uses a new stable key',async()=>{
  const h=harness();await h.submit();const oldKey=h.posts[0].key;
  h.retry();assert.equal(h.posts.length,1);assert.equal(h.pending(),null);
  assert.match(h.get('notice').textContent,/尚未提交/);assert.match(h.get('notice').textContent,/当前资料版本/);
  await h.submit();assert.equal(h.posts.length,2);assert.notEqual(h.posts[1].key,oldKey);
  await h.submit();assert.equal(h.posts[2].key,h.posts[1].key);
});

test('remote retry restores the original fields and requires renewed consent',async()=>{
  const h=harness(),request={question:'Original remote question',strategy:'hybrid',mode:'live',top_k:5};
  h.render(failed({request}));h.get('consent').checked=true;h.retry();
  assert.equal(h.get('question').value,request.question);assert.equal(h.get('strategy').value,'hybrid');assert.equal(h.get('mode').value,'live');
  assert.equal(h.get('consent').checked,false);assert.equal(h.get('submit').disabled,true);
  assert.match(h.get('notice').textContent,/可能再次产生调用费用/);
  await h.submit();assert.equal(h.posts.length,0);
  h.get('consent').checked=true;h.get('consent').onchange();await h.submit();
  assert.equal(h.posts.length,1);assert.deepEqual(h.posts[0].body,request);
});

test('a retained retry button cannot alter a different workspace or a later return to the same workspace',async()=>{
  const h=harness();h.render(failed());const oldButton=h.get('prepare-retry');
  await h.switchWorkspace('workspace-2');h.get('question').value='New workspace question';
  oldButton.onclick();assert.equal(h.get('question').value,'New workspace question');assert.equal(h.posts.length,0);
  await h.switchWorkspace('workspace-1');h.get('question').value='New visit question';
  oldButton.onclick();assert.equal(h.get('question').value,'New visit question');assert.equal(h.posts.length,0);
});

test('a retained task selector and an old result cannot cross workspace boundaries',async()=>{
  const h=harness();await h.loadTasks([failed()]);const oldSelector=h.get('tasks').children[0];
  await h.switchWorkspace('workspace-2');const before=h.get('answer').textContent;
  oldSelector.onclick();h.render(failed());assert.equal(h.selected(),'');assert.equal(h.get('answer').textContent,before);
});

test('preparing retry during submission cannot invalidate the active intent',async()=>{
  const h=harness();await h.submit();const pending=h.pending();h.get('question').value='Do not replace';
  h.setSubmitting(true);assert.equal(h.get('prepare-retry').disabled,true);h.retry();
  assert.deepEqual(h.pending(),pending);assert.equal(h.get('question').value,'Do not replace');assert.equal(h.posts.length,1);
});

test('uncertain POST transport failure reuses its key on manual resubmission',async()=>{
  const h=harness();h.failPost(true);await h.submit();const original=h.pending();
  h.failPost(false);await h.submit();assert.equal(h.posts[1].key,original.key);
});

test('diagnostics render as text, including markup inside the safe stored payload',()=>{
  const h=harness();h.render(failed({error_result:{error:'<img src=x onerror=alert(1)>'}}));
  assert.equal(h.details().error_result.error,'<img src=x onerror=alert(1)>');
  assert.equal(walk(h.get('answer'),n=>n.tagName==='img'),null);
});
