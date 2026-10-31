'use strict';
// Dependency-free DOM harness for the actual page script. No network, database or model calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const {createHash} = require('node:crypto');

const source = fs.readFileSync(path.join(__dirname, '../src/main/resources/static/workspace.js'), 'utf8');
const ids = ['notice', 'strategy', 'mode', 'consent-wrap', 'cost-note', 'submit', 'consent', 'workspace',
  'documents', 'tasks', 'answer', 'file', 'sample', 'result-status', 'revision', 'create-form', 'new-name',
  'question-form', 'question', 'refresh', 'system', 'connection', 'connection-dot', 'conversation',
  'conversation-form','conversation-title','create-conversation','conversation-panel','conversation-status',
  'conversation-goal','conversation-constraints','conversation-focus','conversation-draft','conversation-turns',
  'consent-label','check-pending','materials-management','conversation-management','search-options','mode-summary','task-history','history-summary'];

class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.value=''; this.disabled=false; this.checked=false; this.hidden=false; this.open=false;
    const classes=new Set();this.classList={toggle(name,force){if(force===undefined? !classes.has(name):force)classes.add(name);else classes.delete(name);},add(name){classes.add(name);},remove(name){classes.delete(name);},contains(name){return classes.has(name);}}; this.listeners={}; this._text=''; }
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
test('quality trace distinguishes clarification and mechanical checks from semantic correctness',()=>{
  const h=harness();
  h.render(failed({status:'SUCCEEDED',result:{status:'clarification_required',answer:'Which worker?',
    agent:{state:'awaiting_clarification',trace:[
      {event:'answer_contract',output:{requires_clarification:true}},
      {event:'evidence_quality',output:{checked_assessments:2,findings:[{rule:'unquoted_value'}]}}
    ]}}}));
  const text=h.get('answer').textContent;
  assert.match(text,/所指对象不明确/);
  assert.match(text,/检查 2 项判断/);
  assert.match(text,/发现 1 项问题/);
  assert.match(text,/不代表语义正确/);
  assert.equal(h.posts.length,0);
});
function harness(savedStorage={}) {
  const elements=Object.fromEntries(ids.map(id=>{const node=new Element('div');node.id=id;return[id,node];}));
  const posts=[],storage={...savedStorage},conversations=new Map(),responses=new Map(); let nextId=0,taskRows=[],postFailure=false,postRejection=null,documentRevision=1,documentRows=[{id:'doc',title:'Document'}],failAfterAccept=false,nextConversationGate=null,nextAcceptedPostGate=null,taskReadFailure=false,nextCancelGate=null,statusPayload={database:'available',redis:{available:true,degraded:false}},statusFailure=false;
  const get=id=>elements[id]||Object.values(elements).map(root=>walk(root,n=>n.id===id)).find(Boolean)||null;
  const context=vm.createContext({document:{getElementById:get,createElement:tag=>new Element(tag),hidden:false},
    localStorage:{getItem:key=>storage[key]??null,setItem:(key,value)=>{storage[key]=value;}},Option:function(text,value){const n=new Element('option');n.textContent=text;n.value=value;return n;},
    crypto:{randomUUID:()=>`key-${++nextId}`},setInterval(){},
    fetch:async(url,options={})=>{
      let body={},status=200;
      if(url.endsWith('/system/status')){if(statusFailure)throw new Error('Status unavailable');body=statusPayload;}
      if(options.method==='POST') {
        posts.push({url,key:options.headers['Idempotency-Key'],body:JSON.parse(options.body)});
        if(postFailure)throw new Error('Connection lost after submission');
        if(postRejection){body={error:postRejection,message:'Documents changed'};status=409;}
        else if(url.endsWith('/cancel')){
          body=taskRows.find(t=>t.id===url.split('/').at(-2));
          if(body.status==='QUEUED')body.status='CANCELLED';
          else if(body.status==='RUNNING')body.status='CANCEL_REQUESTED';
          if(body.status==='CANCEL_REQUESTED')status=202;
          for(const c of conversations.values())for(const turn of c.turns)if(turn.task_id===body.id)turn.status=body.status;
          if(nextCancelGate){const gate=nextCancelGate;nextCancelGate=null;await gate;}
        }
        else if(url.endsWith('/conversations')){body={id:'conversation-new',workspace_id:'workspace-1',title:JSON.parse(options.body).title,revision:0,state:{},turns:[]};conversations.set(body.id,body);}
        else if(url.endsWith('/turns')){
          const c=conversations.get(url.split('/').at(-2)),request=JSON.parse(options.body),key=options.headers['Idempotency-Key'];
          if(responses.has(key))body={...responses.get(key),reused:true};
          else{c.revision++;c.state=request.state;body=failed({id:`turn-task-${c.revision}`,conversation_id:c.id,conversation_seq:c.revision,conversation_revision:c.revision,status:'SUCCEEDED',request,result:{status:'answered',answer:'Saved answer'}});
            c.turns.push({seq:c.revision,task_id:body.id,question:request.question,status:body.status,answer:'Saved answer',result_status:'answered'});taskRows.push(body);responses.set(key,body);}
          if(nextAcceptedPostGate){const gate=nextAcceptedPostGate;nextAcceptedPostGate=null;await gate;}
          if(failAfterAccept)throw new Error('Connection lost after acceptance');
        }else body=failed({request:JSON.parse(options.body),reused:posts.length>1});
      } else if(url.endsWith('/documents')) body={documents:documentRows,revision:documentRevision};
      else if(url.endsWith('/tasks')) {if(taskReadFailure)throw new Error('Task list unavailable');body={tasks:taskRows};}
      else if(url.endsWith('/conversations'))body={conversations:[...conversations.values()].filter(c=>url.includes(c.workspace_id))};
      else if(url.includes('/conversations/'))body=conversations.get(url.split('/').at(-1));
      else if(url.includes('/tasks/'))body=taskRows.find(t=>t.id===url.split('/').at(-1));
      const snapshot=plain(body);
      if(!options.method&&url.includes('/conversations/')&&nextConversationGate){const gate=nextConversationGate;nextConversationGate=null;await gate;}
      return {status,ok:status>=200&&status<300,json:async()=>snapshot};
    }});
  // Do not boot the page's polling loop. Every function and event handler is otherwise the real source.
  vm.runInContext(source.slice(0,source.indexOf("loadWorkspaces(localStorage.getItem")),context);
  vm.runInContext("workspace='workspace-1'; workspaceRevision=1; docs=[{id:'doc'}];",context);
  get('materials-management').open=true;
  get('workspace').value='workspace-1';get('strategy').value='bm25';get('mode').value='extractive';
  get('question').value='What is measured?';vm.runInContext('renderConditions()',context);
  const invoke=(name,value)=>{context.argument=value;return vm.runInContext(`${name}(argument)`,context);};
  return {get,posts,storage,render:task=>invoke('renderTask',task),
    systemStatus:async (payload,fail=false)=>{statusPayload=payload;statusFailure=fail;await vm.runInContext('systemStatus()',context);},
    details:()=>JSON.parse(walk(get('answer'),n=>n.tagName==='pre').textContent),
    submit:()=>get('question-form').onsubmit({preventDefault(){}}),
    retry:()=>get('prepare-retry').onclick(),
    pending:()=>plain(vm.runInContext('pending',context)),
    selected:()=>vm.runInContext('selectedTask',context),
    addConversation:(overrides={})=>{const c={id:'conversation-1',workspace_id:'workspace-1',title:'DPO investigation',revision:0,state:{goal:'Find evidence',constraints:['No retraining'],focus:'Stage 4'},turns:[],...overrides};conversations.set(c.id,c);return c;},
    chooseConversation:async id=>{get('conversation').value=id;await vm.runInContext('changeConversation()',context);},
    refreshConversation:()=>vm.runInContext('loadConversation()',context),
    delayNextConversation:()=>{let release;nextConversationGate=new Promise(resolve=>{release=resolve;});return release;},
    delayNextAcceptedPost:()=>{let release;nextAcceptedPostGate=new Promise(resolve=>{release=resolve;});return release;},
    delayNextCancel:()=>{let release;nextCancelGate=new Promise(resolve=>{release=resolve;});return release;},
    replay:()=>get('check-pending').onclick(),
    setSubmitting:value=>{context.argument=value;vm.runInContext('submitting=argument;renderConditions()',context);},
    switchWorkspace:async value=>{get('workspace').value=value;await vm.runInContext('changeWorkspace()',context);},
    loadTasks:async tasks=>{taskRows=tasks;await vm.runInContext('loadTasks()',context);},
    refreshDocuments:async (revision,rows)=>{documentRevision=revision;if(rows!==undefined)documentRows=rows;await vm.runInContext('loadDocuments()',context);},
    rejectPost:(code,revision)=>{postRejection=code;documentRevision=revision;},
    failAfterAccept:value=>{failAfterAccept=value;},
    failTaskRead:value=>{taskReadFailure=value;},
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
  assert.equal(h.posts.length,1);assert.deepEqual(h.posts[0].body,{...request,approved_revision:1});
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
  assert.equal(h.get('submit').disabled,true);assert.equal(h.get('check-pending').hidden,false);
  h.failPost(false);await h.replay();assert.equal(h.posts[1].key,original.key);
});

test('diagnostics render as text, including markup inside the safe stored payload',()=>{
  const h=harness();h.render(failed({error_result:{error:'<img src=x onerror=alert(1)>'}}));
  assert.equal(h.details().error_result.error,'<img src=x onerror=alert(1)>');
  assert.equal(walk(h.get('answer'),n=>n.tagName==='img'),null);
});


test('remote consent is bound to the displayed revision and absent from offline requests',async()=>{
  const h=harness();await h.submit();assert.equal('approved_revision' in h.posts[0].body,false);
  h.get('mode').value='agent';h.get('mode').listeners.input();
  assert.equal(h.get('submit').disabled,true);
  h.get('consent').checked=true;h.get('consent').onchange();await h.submit();
  assert.equal(h.posts[1].body.approved_revision,1);assert.equal(h.posts[1].body.mode,'agent');
});

test('document refresh invalidates consent only when the revision changes',async()=>{
  const h=harness();h.get('mode').value='agent';h.get('mode').listeners.input();
  h.get('consent').checked=true;h.get('consent').onchange();
  await h.refreshDocuments(1);assert.equal(h.get('consent').checked,true);
  await h.refreshDocuments(2);assert.equal(h.get('consent').checked,false);assert.equal(h.get('submit').disabled,true);
  await h.submit();assert.equal(h.posts.length,0);
});

test('server stale approval clears consent and refreshes version before another manual submission',async()=>{
  const h=harness();h.get('mode').value='agent';h.get('mode').listeners.input();
  h.get('consent').checked=true;h.get('consent').onchange();h.rejectPost('stale_approval',2);await h.submit();
  const oldKey=h.posts[0].key;assert.equal(h.posts[0].body.approved_revision,1);
  assert.equal(h.get('consent').checked,false);assert.equal(h.pending(),null);
  assert.match(h.get('notice').textContent,/重新勾选/);assert.match(h.get('revision').textContent,/版本 2/);
  await h.submit();assert.equal(h.posts.length,1);
  h.rejectPost(null,2);h.get('consent').checked=true;h.get('consent').onchange();await h.submit();
  assert.notEqual(h.posts[1].key,oldKey);assert.equal(h.posts[1].body.approved_revision,2);
});

test('changing mode or strategy invalidates the previous sending permission',async()=>{
  const h=harness();h.get('mode').value='agent';h.get('mode').listeners.input();
  h.get('consent').checked=true;h.get('consent').onchange();
  h.get('strategy').value='dense';h.get('strategy').listeners.input();
  assert.equal(h.get('consent').checked,false);await h.submit();assert.equal(h.posts.length,0);
});

function consent(h){h.get('consent').checked=true;h.get('consent').onchange();}
function edit(h,id,value){h.get(id).value=value;h.get(id).listeners.input();}

test('conversation selection forces Agent and sends user state with both revisions',async()=>{
  const h=harness();h.addConversation();await h.chooseConversation('conversation-1');
  assert.equal(h.get('mode').value,'agent');assert.equal(h.get('mode').disabled,true);
  assert.equal(h.get('conversation-goal').value,'Find evidence');
  assert.match(h.get('consent-label').textContent,/会话历史/);consent(h);await h.submit();
  assert.equal(h.posts[0].url,'/api/workspaces/workspace-1/conversations/conversation-1/turns');
  assert.deepEqual(h.posts[0].body,{question:'What is measured?',strategy:'bm25',mode:'agent',top_k:5,
    approved_revision:1,expected_conversation_revision:0,state:{goal:'Find evidence',constraints:['No retraining'],focus:'Stage 4'}});
  assert.equal(h.get('consent').checked,false);assert.match(h.get('conversation-turns').textContent,/Saved answer/);
});

test('workspace refresh restores its conversation selection and persisted turn history',async()=>{
  const h=harness({'lablens.conversation.workspace-1':'conversation-1'});
  h.addConversation({revision:3,turns:[{seq:3,task_id:'old-task',question:'Previous question',status:'SUCCEEDED',answer:'Historical answer',answer_truncated:true}]});
  await h.switchWorkspace('workspace-1');
  assert.equal(h.get('conversation').value,'conversation-1');assert.equal(h.get('mode').value,'agent');
  assert.match(h.get('conversation-turns').textContent,/Historical answer/);assert.match(h.get('conversation-turns').textContent,/摘要已截断/);
  assert.equal(h.get('consent').checked,false);assert.equal(h.get('submit').disabled,true);
});

test('editing pinned state or question invalidates consent and does not write facts automatically',async()=>{
  const h=harness();h.addConversation();await h.chooseConversation('conversation-1');consent(h);
  edit(h,'conversation-focus','Corrected experiment');assert.equal(h.get('consent').checked,false);
  await h.submit();assert.equal(h.posts.length,0);consent(h);edit(h,'question','Follow-up after correction');
  assert.equal(h.get('submit').disabled,true);consent(h);await h.submit();
  assert.equal(h.posts[0].body.state.focus,'Corrected experiment');assert.equal(h.get('conversation-focus').value,'Corrected experiment');
});

test('polling keeps unsaved pinned state and invalidates consent when another turn advances revision',async()=>{
  const h=harness(),c=h.addConversation();await h.chooseConversation(c.id);
  edit(h,'conversation-goal','My unsaved goal');consent(h);c.revision=2;c.state.goal='Other tab goal';
  await h.refreshConversation();assert.equal(h.get('conversation-goal').value,'My unsaved goal');assert.equal(h.get('consent').checked,false);
});

test('stale conversation refresh preserves draft and requires a new manual intent',async()=>{
  const h=harness(),c=h.addConversation();await h.chooseConversation(c.id);edit(h,'conversation-focus','My draft');
  consent(h);c.revision=4;c.state.focus='Other tab focus';h.rejectPost('stale_conversation',1);await h.submit();
  const key=h.posts[0].key;assert.equal(h.get('conversation-focus').value,'My draft');assert.equal(h.get('question').value,'What is measured?');
  assert.equal(h.get('consent').checked,false);assert.equal(h.pending(),null);assert.match(h.get('notice').textContent,/草稿/);
  await h.submit();assert.equal(h.posts.length,1);h.rejectPost(null,1);consent(h);await h.submit();
  assert.notEqual(h.posts[1].key,key);assert.equal(h.posts[1].body.expected_conversation_revision,4);assert.equal(h.posts[1].body.state.focus,'My draft');
});

test('uncertain accepted conversation POST replays exact body and key despite refreshed revisions',async()=>{
  const h=harness(),c=h.addConversation();await h.chooseConversation(c.id);consent(h);h.failAfterAccept(true);await h.submit();
  const original=h.pending();assert.equal(c.turns.length,1);await h.refreshConversation();await h.refreshDocuments(2);
  assert.deepEqual(h.pending(),original);assert.equal(h.get('check-pending').hidden,false);assert.equal(h.get('submit').disabled,true);
  h.failAfterAccept(false);await h.replay();assert.equal(c.turns.length,1);assert.equal(h.posts[1].key,h.posts[0].key);
  assert.deepEqual(h.posts[1].body,h.posts[0].body);assert.match(h.get('notice').textContent,/没有重复启动/);
});

for(const refresh of ['conversation','documents'])test(`an in-flight accepted POST survives ${refresh} polling before a lost response`,async()=>{
  const h=harness(),c=h.addConversation();await h.chooseConversation(c.id);consent(h);h.failAfterAccept(true);
  const release=h.delayNextAcceptedPost(),submitted=h.submit(),original=h.pending();
  assert.equal(c.turns.length,1);assert.equal(original.uncertain,false);
  if(refresh==='conversation')await h.refreshConversation();else await h.refreshDocuments(2);
  const duringPoll=h.pending();release();await submitted;
  assert.deepEqual(duringPoll,original);
  assert.deepEqual(h.pending(),{...original,uncertain:true});
  assert.equal(h.get('check-pending').hidden,false);assert.equal(h.get('submit').disabled,true);
  h.failAfterAccept(false);await h.replay();
  assert.equal(c.turns.length,1);assert.equal(h.posts[1].key,original.key);assert.deepEqual(h.posts[1].body,JSON.parse(original.body));
  assert.match(h.get('notice').textContent,/没有重复启动/);
});

test('switching conversations or workspaces isolates state and retained turn controls',async()=>{
  const h=harness();h.addConversation({turns:[{seq:1,task_id:'old',question:'Old question',status:'SUCCEEDED',answer:'Old answer'}]});
  h.addConversation({id:'conversation-2',state:{goal:'Another goal',constraints:[],focus:''}});
  await h.chooseConversation('conversation-1');const old=walk(h.get('conversation-turns'),n=>n.tagName==='button');
  await h.chooseConversation('conversation-2');await old.onclick();assert.equal(h.selected(),'');assert.equal(h.get('conversation-goal').value,'Another goal');
  await h.switchWorkspace('workspace-2');assert.equal(h.get('conversation').value,'');assert.equal(h.get('conversation-goal').value,'');assert.equal(h.get('conversation-panel').hidden,true);
  assert.equal(h.get('conversation-turns').textContent,'');assert.equal(h.storage['lablens.conversation.workspace-1'],'conversation-2');
});

test('clarification choices fill a fresh follow-up without any request and require consent',async()=>{
  const h=harness();h.addConversation();await h.chooseConversation('conversation-1');consent(h);
  const task=failed({status:'SUCCEEDED',conversation_id:'conversation-1',result:{status:'clarification_required',answer:'Which run?',clarification:{question:'Which run?',choices:['SFT run','DPO run']}}});
  await h.loadTasks([task]);h.get('tasks').children[0].onclick();
  const choice=walk(h.get('answer'),n=>n.tagName==='button'&&n.textContent==='DPO run');choice.onclick();
  assert.equal(h.get('question').value,'DPO run');assert.equal(h.posts.length,0);assert.equal(h.get('consent').checked,false);
  assert.match(h.get('result-status').textContent,/待你澄清/);assert.match(h.get('notice').textContent,/尚未提交/);
  await h.chooseConversation('');edit(h,'question','Different intent');choice.onclick();assert.equal(h.get('question').value,'Different intent');
});

test('context limits and omissions are visible without claiming exact tokens',()=>{
  const h=harness();h.render(failed({error_code:'context_budget_exceeded',error_result:{context:{conversation_revision:2,input_turns:12,omitted_turns:3,
    context_budget:{exact_tokenizer:false,calls:[{dropped_turns:5}]}}}}));
  assert.match(h.get('answer').textContent,/上下文已超出/);assert.match(h.get('answer').textContent,/上游省略 3 轮/);
  assert.match(h.get('answer').textContent,/未带入 5 轮/);assert.match(h.get('answer').textContent,/不是模型 tokenizer 的精确 token 数/);
});

test('busy conversations clearly stop another submission instead of silently queueing',async()=>{
  const h=harness(),c=h.addConversation();await h.chooseConversation(c.id);consent(h);
  h.rejectPost('conversation_busy',1);c.turns=[{seq:1,task_id:'running',question:'Active question',status:'RUNNING'}];await h.submit();
  assert.match(h.get('notice').textContent,/未排入队列/);assert.equal(h.get('submit').disabled,true);assert.equal(h.pending(),null);
  c.turns[0].status='SUCCEEDED';h.rejectPost(null,1);await h.refreshConversation();assert.equal(h.get('submit').disabled,true);
});

test('creating a conversation selects it without invoking a model task',async()=>{
  const h=harness();h.get('conversation-title').value='New investigation';await h.get('conversation-form').onsubmit({preventDefault(){}});
  assert.equal(h.posts.length,1);assert.equal(h.posts[0].url,'/api/workspaces/workspace-1/conversations');
  assert.deepEqual(h.posts[0].body,{title:'New investigation'});assert.equal(h.get('conversation').value,'conversation-new');assert.equal(h.get('consent').checked,false);
});

test('out-of-order conversation responses cannot restore stale state or busy status',async()=>{
  const h=harness(),c=h.addConversation({revision:1,turns:[{seq:1,task_id:'active',question:'Question',status:'RUNNING'}]});await h.chooseConversation(c.id);
  const release=h.delayNextConversation(),old=h.refreshConversation();
  c.revision=2;c.state.goal='Current goal';c.turns[0].status='SUCCEEDED';await h.refreshConversation();release();await old;
  assert.match(h.get('conversation-status').textContent,/版本 2/);assert.doesNotMatch(h.get('conversation-status').textContent,/正在处理/);
  assert.equal(h.get('conversation-goal').value,'Current goal');
});

test('oversized pinned constraints produce a local explanation without a task request',async()=>{
  const h=harness();h.addConversation();await h.chooseConversation('conversation-1');
  edit(h,'conversation-constraints',Array.from({length:13},(_,i)=>`Constraint ${i}`).join('\n'));consent(h);await h.submit();
  assert.equal(h.posts.length,0);assert.match(h.get('notice').textContent,/最多 12 条/);
});

test('a failed history refresh after successful POST does not mark submission unknown',async()=>{
  const h=harness();h.failTaskRead(true);await h.submit();
  assert.equal(h.posts.length,1);assert.equal(h.pending().uncertain,false);assert.equal(h.get('check-pending').hidden,true);
  assert.match(h.get('notice').textContent,/任务已保存/);assert.match(h.get('notice').textContent,/无需重新提交/);
});

test('queued cancellation saves terminal state without submitting another task',async()=>{
  const h=harness(),t=failed({status:'QUEUED'});await h.loadTasks([t]);h.render(t);
  await h.get('cancel-task').onclick();
  assert.equal(h.posts.length,1);assert.equal(h.posts[0].url,'/api/workspaces/workspace-1/tasks/failed-1/cancel');
  assert.deepEqual(h.posts[0].body,{});assert.match(h.get('result-status').textContent,/已取消/);
  assert.equal(h.get('cancel-task'),null);assert.ok(h.get('prepare-retry'));
  assert.equal(h.details().execution_usage.model_calls,0);
});

test('running cancellation stays busy until worker acknowledgement and retains history',async()=>{
  const h=harness(),t=failed({status:'RUNNING',conversation_id:'conversation-1'});
  const c=h.addConversation({turns:[{seq:1,task_id:t.id,question:'Active question',status:'RUNNING'}]});
  await h.chooseConversation(c.id);await h.loadTasks([t]);h.render(t);
  await h.get('cancel-task').onclick();
  assert.match(h.get('result-status').textContent,/正在停止/);assert.equal(h.get('prepare-retry'),null);
  assert.equal(h.get('submit').disabled,true);assert.match(h.get('answer').textContent,/确认/);
  t.status='CANCELLED';c.turns[0].status='CANCELLED';await h.refreshConversation();h.render(t);
  assert.match(h.get('conversation-turns').textContent,/已取消/);assert.ok(h.get('prepare-retry'));
  assert.equal(h.posts.length,1);
});

test('double cancellation clicks make one request and stale response cannot change another workspace',async()=>{
  const h=harness(),t=failed({status:'RUNNING'});await h.loadTasks([t]);h.render(t);
  const button=h.get('cancel-task'),release=h.delayNextCancel(),request=button.onclick();
  await button.onclick();assert.equal(h.posts.length,1);assert.equal(button.disabled,true);
  await h.switchWorkspace('workspace-2');const before=h.get('answer').textContent;
  release();await request;assert.equal(h.get('answer').textContent,before);assert.equal(h.selected(),'');
});

test('retained cancel control cannot cancel a newly selected task',async()=>{
  const h=harness(),one=failed({status:'RUNNING'}),two=failed({id:'another',status:'RUNNING'});
  await h.loadTasks([one,two]);h.render(one);const old=h.get('cancel-task');h.render(two);
  await old.onclick();assert.equal(h.posts.length,0);
});

test('uncertain cancellation is not called complete and supports a manual check',async()=>{
  const h=harness(),t=failed({status:'RUNNING'});await h.loadTasks([t]);h.render(t);h.failPost(true);
  await h.get('cancel-task').onclick();assert.match(h.get('notice').textContent,/未得到确认/);
  assert.match(h.get('result-status').textContent,/处理中/);assert.equal(h.get('cancel-task').disabled,false);
  assert.equal(h.get('prepare-retry'),null);
});

test('retrying a cancelled remote task only prepares a new explicitly approved request',async()=>{
  const h=harness();h.render(failed({status:'CANCELLED',request:{question:'Cancelled question',mode:'agent',strategy:'bm25',top_k:5}}));
  h.retry();assert.equal(h.posts.length,0);assert.equal(h.get('question').value,'Cancelled question');
  assert.equal(h.get('consent').checked,false);assert.equal(h.get('submit').disabled,true);
  await h.submit();assert.equal(h.posts.length,0);consent(h);await h.submit();assert.equal(h.posts.length,1);
});

test('failed review timings preserve unknown usage and distinguish JSON bytes from tokens',()=>{
  const h=harness();h.render(failed({error_result:{agent:{state:'timeout',telemetry_version:'runtime-stages-v1',trace:[
    {event:'request_started',request_id:'r2',stage_id:'s3',phase:'review',elapsed_ms:55000,remaining_ms:20000,request_body_bytes:8192,timeout_ms:20000},
    {event:'request_finished',request_id:'r2',stage_id:'s3',phase:'review',elapsed_ms:75000,remaining_ms:0,duration_ms:20000,request_body_bytes:8192,timeout_ms:20000,outcome:'timeout',usage_known:false,usage:{total_tokens:null}}
  ]}}}));
  const text=h.get('answer').textContent;
  assert.match(text,/分阶段耗时与预算/);assert.match(text,/最终复核 · 模型请求：超时/);
  assert.match(text,/20\.000 秒/);assert.match(text,/8\.0 KiB/);assert.match(text,/本次用量未知，不能计为零/);
  assert.doesNotMatch(text,/返回用量 0 token/);assert.doesNotMatch(text,/开始后未记录结束/);assert.equal(h.posts.length,0);
});
test('nested timing rows show observed returned usage without summing overlapping stages',()=>{
  const h=harness();h.render(failed({status:'SUCCEEDED',result:{status:'answered',answer:'Reviewed answer',agent:{state:'completed',trace:[
    {event:'stage_finished',stage_id:'s2',phase:'tool',name:'verify_and_submit_report',elapsed_ms:15000,remaining_ms:60000,duration_ms:9000,outcome:'returned'},
    {event:'request_finished',stage_id:'s3',request_id:'r2',phase:'review',elapsed_ms:14000,remaining_ms:61000,duration_ms:8000,outcome:'returned',usage_known:true,usage:{total_tokens:123}}
  ]}}}));
  const text=h.get('answer').textContent;assert.match(text,/耗时不能直接相加/);assert.match(text,/返回用量 123 token/);
  assert.match(text,/9\.000 秒/);assert.match(text,/8\.000 秒/);assert.doesNotMatch(text,/总耗时 17/);
});
test('incomplete timing and malformed numbers remain visibly unknown instead of zero',()=>{
  const h=harness();h.render(failed({error_result:{agent:{state:'timeout',trace:[
    {event:'request_started',request_id:'r1',stage_id:'s1',phase:'decision',remaining_ms:null,request_body_bytes:-1},
    {event:'stage_finished',stage_id:'s2',phase:'tool',duration_ms:'10',remaining_ms:-1,outcome:'error'}
  ]}}}));
  const text=h.get('answer').textContent;assert.match(text,/开始后未记录结束/);assert.match(text,/剩余预算 未知/);
  assert.match(text,/请求正文 未知/);assert.match(text,/耗时 未知/);assert.doesNotMatch(text,/NaN|Infinity/);
});


test('page disclosures keep project selection, permission, result and refresh outside collapsed content',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../src/main/resources/static/index.html'),'utf8');
  // Inspect actual markup ancestry, not style rules; collapsing a native details hides its descendants.
  const stack=[],ancestors=new Map(),voidTags=new Set(['meta','link','input','br']);
  for(const match of html.matchAll(/<\/?([a-z][a-z0-9-]*)\b([^>]*)>/gi)){
    const tag=match[1].toLowerCase();
    if(match[0].startsWith('</')){assert.equal(stack.pop()?.tag,tag,`unbalanced ${tag}`);continue;}
    const id=match[2].match(/\bid="([^"]+)"/)?.[1];
    if(id){assert.equal(ancestors.has(id),false,`duplicate ${id}`);ancestors.set(id,stack.filter(n=>n.tag==='details').map(n=>n.id));}
    if(!voidTags.has(tag))stack.push({tag,id});
  }
  for(const id of ids)assert.ok(ancestors.has(id),`missing ${id}`);
  for(const id of ['workspace','conversation','conversation-status','mode-summary','consent-wrap','cost-note','notice','answer','refresh']){
    assert.deepEqual(ancestors.get(id),id==='mode-summary'?['search-options']:[],`${id} must stay discoverable`);
  }
  assert.deepEqual(ancestors.get('strategy'),['search-options']);
  assert.deepEqual(ancestors.get('documents'),['materials-management']);
  assert.deepEqual(ancestors.get('tasks'),['task-history']);
});

test('empty workspaces expand document management while polling preserves an existing disclosure choice',async()=>{
  const h=harness();await h.refreshDocuments(1,[]);
  assert.equal(h.get('materials-management').open,true);assert.equal(h.get('submit').disabled,true);
  assert.match(h.get('documents').textContent,/还没有资料/);
  await h.refreshDocuments(2,[{id:'doc',title:'Added document'}]);
  assert.equal(h.get('materials-management').open,true); // Keep the import workflow open.
  await h.switchWorkspace('workspace-2');assert.equal(h.get('materials-management').open,false);
  h.get('materials-management').open=true;await h.refreshDocuments(2);
  assert.equal(h.get('materials-management').open,true);
  h.get('materials-management').open=false;await h.refreshDocuments(3,[]);
  assert.equal(h.get('materials-management').open,true);assert.equal(h.posts.length,0);
});

test('collapsed settings expose the active remote mode and keep permission required',async()=>{
  const h=harness();assert.equal(h.get('search-options').open,false);
  edit(h,'mode','live');edit(h,'strategy','hybrid');
  assert.match(h.get('mode-summary').textContent,/混合检索 · 模型整理/);
  assert.equal(h.get('consent-wrap').hidden,false);assert.equal(h.get('cost-note').hidden,false);
  assert.match(h.get('cost-note').textContent,/调用生成模型/);
  await h.submit();assert.equal(h.posts.length,0);
  consent(h);edit(h,'question','Changed question');assert.equal(h.get('consent').checked,false);
  await h.submit();assert.equal(h.posts.length,0);
  h.addConversation();await h.chooseConversation('conversation-1');
  assert.equal(h.get('conversation-management').open,false);assert.match(h.get('conversation-status').textContent,/DPO investigation/);
  assert.match(h.get('mode-summary').textContent,/Agent 查找与核验/);assert.equal(h.get('consent-wrap').hidden,false);
});

test('report text and evidence verdict precede folded sources and terminal agent trace',()=>{
  const h=harness();h.render(failed({status:'SUCCEEDED',result:{status:'refused',answer:'There is not enough evidence.',evidence_verdict:'insufficient',
    assessments:[{statement:'Historical runtime',verdict:'insufficient',explanation:'Only configuration is available.',citation_ids:['c1'],quotes:[{chunk_id:'c1',quote:'configured value'}]}],
    retrieval:{hits:[{title:'Config',text:'configured value',source:'config.md',start:0,end:16}]},
    agent:{state:'insufficient_evidence',trace:[]}}}));
  assert.equal(h.get('answer').children[0].textContent,'There is not enough evidence.');
  assert.match(h.get('result-status').textContent,/证据不足/);
  const source=walk(h.get('answer'),n=>n.tagName==='details'&&n.children[0]?.textContent.startsWith('来源与逐条判定'));
  assert.equal(source.open,false);assert.match(source.textContent,/Only configuration is available/);assert.match(source.textContent,/configured value/);
  const trace=walk(h.get('answer'),n=>n.tagName==='details'&&n.children[0]?.textContent.startsWith('Agent 运行轨迹'));
  assert.equal(trace.open,false);assert.match(trace.children[0].textContent,/证据不足，已停止/);
});

test('extractive results show the original passages without expanding diagnostic details',()=>{
  const h=harness();h.render(failed({status:'SUCCEEDED',result:{status:'excerpts',citations:[{title:'Document',text:'Original passage',source:'source.md',start:0,end:16}]}}));
  const sources=walk(h.get('answer'),n=>n.tagName==='details'&&n.children[0]?.textContent.startsWith('来源与逐条判定'));
  assert.equal(sources.open,true);assert.match(sources.textContent,/Original passage/);
  assert.match(h.get('answer').textContent,/尚未判断/);assert.equal(h.posts.length,0);
});

test('collapsed history still refreshes the selected task and exposes cancellation',async()=>{
  const h=harness(),task=failed({status:'RUNNING'});await h.loadTasks([task]);h.get('tasks').children[0].onclick();
  assert.equal(h.get('task-history').open,false);assert.equal(h.selected(),task.id);
  assert.ok(h.get('cancel-task'));assert.match(h.get('history-summary').textContent,/1 条/);
  task.status='FAILED';task.error_message='Request timed out';await h.get('refresh').onclick();
  assert.equal(h.selected(),task.id);assert.equal(h.get('task-history').open,false);
  assert.match(h.get('result-status').textContent,/失败/);assert.match(h.get('answer').children[0].textContent,/Request timed out/);
  assert.ok(h.get('prepare-retry'));assert.equal(h.posts.length,0);
});


for(const scenario of [
  {name:'healthy database and cache',payload:{database:'available',redis:{available:true,degraded:false}},label:/本地服务已连接$/,state:'online'},
  {name:'database outage despite HTTP 200',payload:{database:'unavailable',redis:{available:true,degraded:false}},label:/服务不可用 · 数据库连接失败/,state:'offline'},
  {name:'database and cache outage',payload:{database:'unavailable',redis:{available:false,degraded:true}},label:/服务不可用 · 数据库连接失败/,state:'offline'},
  {name:'cache only outage',payload:{database:'available',redis:{available:false,degraded:true}},label:/已连接 · 缓存降级/,state:'degraded'},
  {name:'degraded flag without available field',payload:{database:'available',redis:{degraded:true}},label:/缓存降级/,state:'degraded'},
  {name:'unknown database status',payload:{redis:{available:true}},label:/服务状态未确认/,state:'unknown'},
  {name:'unknown cache status',payload:{database:'available'},label:/缓存状态未知/,state:'unknown'},
  {name:'status request failure',payload:null,fail:true,label:/本地服务暂不可用/,state:'offline'},
])test(`connection indicator classifies ${scenario.name} and clears stale healthy color`,async()=>{
  const h=harness();await h.systemStatus({database:'available',redis:{available:true,degraded:false}});
  assert.equal(h.get('connection-dot').classList.contains('online'),true);
  await h.systemStatus(scenario.payload,scenario.fail);
  assert.match(h.get('connection').textContent,scenario.label);
  for(const state of ['online','degraded','offline'])assert.equal(h.get('connection-dot').classList.contains(state),state===scenario.state);
  assert.equal(h.posts.length,0);
});

test('connection recovery removes the previous outage and degraded indicators',async()=>{
  const h=harness();await h.systemStatus({database:'unavailable',redis:{available:false,degraded:true}});
  await h.systemStatus({database:'available',redis:{available:false,degraded:true}});
  assert.equal(h.get('connection-dot').classList.contains('offline'),false);
  await h.systemStatus({database:'available',redis:{available:true,degraded:false}});
  assert.equal(h.get('connection-dot').classList.contains('degraded'),false);
  assert.equal(h.get('connection-dot').classList.contains('online'),true);
  assert.match(h.get('connection').textContent,/本地服务已连接$/);
});


test('HTML asset URLs change with the exact CSS and JavaScript bytes',()=>{
  const staticRoot=path.join(__dirname,'../src/main/resources/static');
  const html=fs.readFileSync(path.join(staticRoot,'index.html'),'utf8');
  for(const [filename,attribute] of [['workspace.css','href'],['workspace.js','src']]){
    const match=html.match(new RegExp(attribute+'="(/'+filename.replace('.','\\.')+'[^\"]*)"'));
    assert.ok(match,`missing ${filename} URL`);
    const url=new URL(match[1],'http://local.test');
    const hash=createHash('sha256').update(fs.readFileSync(path.join(staticRoot,filename))).digest('hex').slice(0,12);
    assert.equal(url.pathname,'/'+filename);
    assert.equal(url.search,'?v='+hash,`${filename} content changed: update the HTML asset version`);
  }
});
