#!/usr/bin/env python3
"""Real JVM/MySQL/worker crash-and-resume checks; local scripted provider only.

Owns the worker it starts, SIGKILLs that process at two declared fault points,
and keeps request outcomes. No externally supplied PID can be terminated.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json,os,socket,subprocess,sys,time,uuid
from pathlib import Path
from urllib.parse import urlsplit
import httpx

QUOTE='The measured accuracy was 86 percent.'


def run(args,checks):
    for value in (args.base_url,args.fixture_url):
        parsed=urlsplit(value)
        if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.path:raise ValueError('isolated loopback services required')
    if os.getenv('LLM_BASE_URL')!=args.fixture_url+'/v1' or os.getenv('EMBEDDING_API_KEY'):
        raise ValueError('explicit local model and disabled embedding credentials required')
    process=None;serial=0
    def check(name,value,**extra):
        checks.append(dict(name=name,passed=bool(value),**extra));assert value,name
    def wait(fn,label,seconds=20):
        end=time.monotonic()+seconds
        while True:
            value=fn()
            if value:return value
            assert time.monotonic()<end,label
            time.sleep(.05)
    def open_port():
        with socket.socket() as conn:return conn.connect_ex(('127.0.0.1',args.worker_port))==0
    def start():
        nonlocal process,serial
        assert not open_port(),'refuse occupied worker port';serial+=1
        with (args.output.parent/f'worker-{serial}.private.log').open('x') as log:
            process=subprocess.Popen([sys.executable,'-m','uvicorn','lablens.api:app','--host','127.0.0.1','--port',str(args.worker_port)],stdout=log,stderr=subprocess.STDOUT)
        wait(open_port,'owned worker startup');assert process.poll() is None
    def stop(crash=False):
        nonlocal process
        if process is None:return
        if process.poll() is None:
            (process.kill if crash else process.terminate)();process.wait(timeout=10)
        wait(lambda:not open_port(),'owned worker exit');process=None
    def response(name,value,status):check(name,value.status_code==status,http_status=value.status_code);return value.json()
    try:
        with ExitStack() as stack:
            client=stack.enter_context(httpx.Client(base_url=args.base_url,trust_env=False,timeout=10,headers={'Origin':args.base_url}))
            fixture=stack.enter_context(httpx.Client(base_url=args.fixture_url,trust_env=False,timeout=5))
            marker=fixture.get('/').json();check('explicit scripted recovery fixture',marker.get('fixture')=='round18-scripted-recovery-only');before=marker['requests'];tools_before=marker['tool_requests']
            session=response('owner login',client.post('/api/auth/login',json={'username':os.environ['LABLENS_AUTH_BOOTSTRAP_USERNAME'],'password':os.environ['LABLENS_AUTH_BOOTSTRAP_PASSWORD']}),200);client.headers['X-CSRF-Token']=session['csrf_token']
            def make_workspace(label):
                wid=response('create '+label,client.post('/api/workspaces',json={'name':label}),201)['id'];path='/api/workspaces/'+wid
                revision=response('create synthetic source',client.put(path+'/documents/source',json={'title':'Synthetic measured result','text':QUOTE,'source':'round18 synthetic recovery fixture'}),200)['revision']
                return wid,path,revision
            def submit(path,revision):
                return response('admit original task',client.post(path+'/tasks',json={'question':'What is the measured accuracy?','strategy':'bm25','mode':'agent','top_k':3,'approved_revision':revision},headers={'Idempotency-Key':str(uuid.uuid4())}),202)
            def terminal(path,tid):
                def read():
                    data=client.get(path+'/tasks/'+tid).json();return data if data['status'] not in ('QUEUED','RUNNING','CANCEL_REQUESTED') else None
                result=wait(read,'terminal task')
                with (args.output.parent/'tasks.jsonl').open('a') as log:log.write(json.dumps(result,ensure_ascii=False)+'\n')
                return result
            def fault(path,revision,gate):
                fixture.post('/control',json={'arm':gate}).raise_for_status();task=submit(path,revision)
                wait(lambda:fixture.get('/').json()['gated'],'declared fault gate')
                # The process is directly owned by this harness, not discovered by PID.
                stop(crash=True);ended=terminal(path,task['id'])
                fixture.post('/control',json={'arm':None}).raise_for_status()
                check('crashed task ends explicitly',ended['status']=='FAILED',task_id=task['id'],error_code=ended.get('error_code'))
                return ended
            wid,path,revision=make_workspace('Round18 crash recovery matrix');start()
            original=fault(path,revision,'boundary');tid=original['id']
            recovery=response('read durable recovery boundary',client.get(path+'/tasks/'+tid+'/recovery'),200)
            check('complete boundary resumable after worker death',recovery['status']=='resumable',metadata=recovery)
            check('only one decision before first interruption',fixture.get('/').json()['requests']-before==1)
            request={'approved_snapshot_id':recovery['snapshot_id'],'expected_checkpoint_revision':recovery['checkpoint_revision']}
            response('stale checkpoint approval rejected',client.post(path+'/tasks/'+tid+'/resume',json={**request,'expected_checkpoint_revision':0},headers={'Idempotency-Key':str(uuid.uuid4())}),400)
            response('changed snapshot approval rejected',client.post(path+'/tasks/'+tid+'/resume',json={**request,'approved_snapshot_id':wid+':999'},headers={'Idempotency-Key':str(uuid.uuid4())}),409)
            response('change current source independently',client.put(path+'/documents/source',json={'title':'New current source','text':'The measured accuracy was 99 percent.','source':'new version'}),200)
            key=str(uuid.uuid4())
            # The JVM correctly dispatches immediately. Restore service readiness
            # before admitting the child; a down worker must not be retried by it.
            start()
            def resume(_):return client.post(path+'/tasks/'+tid+'/resume',json=request,headers={'Idempotency-Key':key})
            with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(resume,range(2)))
            check('concurrent idempotent resume admitted exactly once',sorted(r.status_code for r in responses)==[200,202],http_statuses=[r.status_code for r in responses])
            children=[r.json() for r in responses];child=children[0]
            check('same child id returned',children[0]['id']==children[1]['id'])
            check('old task snapshot retained',client.get(path+'/tasks/'+tid).json()==original)
            completed=terminal(path,child['id'])
            check('restored worker completed report',completed['status']=='SUCCEEDED',task_id=child['id'],error_code=completed.get('error_code'))
            result=completed['result'];trace=result['agent']['trace']
            check('completed retrieval not replayed',sum(e.get('event')=='tool' and e.get('name')=='search_workspace' for e in trace)==1)
            check('original source used after current document edit','86 percent' in result['answer'] and '99 percent' not in result['answer'] and result['retrieval']['snapshot_id']==recovery['snapshot_id'])
            check('three total provider-shaped requests across crash and resume',fixture.get('/').json()['requests']-before==3)
            check('retrieval proxy received exactly one HTTP call across restart',fixture.get('/').json()['tool_requests']-tools_before==1)
            check('cumulative model usage preserved',result['usage']['model_calls']==3 and result['usage']['generation_tokens']['total_tokens']==45)
            check('child execution usage excludes inherited calls',completed['execution_usage']['model_calls']==2 and completed['execution_usage']['generation_tokens']['total_tokens']==30)
            check('parent now points to child',client.get(path+'/tasks/'+tid+'/recovery').json()['resumed_task_id']==child['id'])
            check('report survives fresh GET',client.get(path+'/tasks/'+child['id']).json()['result']==result)
            response('cannot recursively resume a completed child',client.post(path+'/tasks/'+child['id']+'/resume',json=request,headers={'Idempotency-Key':str(uuid.uuid4())}),409)
            unknown=fault(path,revision+1,'inflight_report');unknown_id=unknown['id']
            state=response('read in-flight interruption',client.get(path+'/tasks/'+unknown_id+'/recovery'),200)
            check('unknown request blocks recovery',state['status']=='unknown_outcome',metadata=state)
            response('unknown cannot be replayed',client.post(path+'/tasks/'+unknown_id+'/resume',json={'approved_snapshot_id':state['snapshot_id'],'expected_checkpoint_revision':state['checkpoint_revision']},headers={'Idempotency-Key':str(uuid.uuid4())}),409)
            check('unknown rejection makes no additional model request',fixture.get('/').json()['requests']-before==5)
            check('unknown usage retained',unknown['usage_unknown'] is True)
            browser=None
            if args.prepare_browser:
                bw,bp,br=make_workspace('Round18 browser recovery fixture');start();bt=fault(bp,br,'boundary');browser={'workspace_id':bw,'task_id':bt['id']}
                check('browser fixture has safe boundary',client.get(bp+'/tasks/'+bt['id']+'/recovery').json()['status']=='resumable')
            return dict(passed=True,checks=checks,workspace_id=wid,parent_task=original,resumed_task=completed,unknown_task=unknown,
                browser_fixture=browser,scripted_local_requests=fixture.get('/').json()['requests']-before,external_calls=0,
                scope='Owned worker SIGKILL, real authenticated HTTP and MySQL. Fixed responses are not model semantic acceptance.')
    finally:stop()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8086');parser.add_argument('--fixture-url',default='http://127.0.0.1:8771')
    parser.add_argument('--worker-port',type=int,default=8770);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--prepare-browser',action='store_true')
    args=parser.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True);args.output.touch(exist_ok=False);checks=[]
    try:result=run(args,checks)
    except Exception as error:
        result={'passed':False,'checks':checks,'error_type':type(error).__name__,'error':str(error) if isinstance(error,AssertionError) else 'Inspect private runner log'}
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');raise
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'passed':True,'checks':len(checks),'scripted_local_requests':result['scripted_local_requests']}))


if __name__=='__main__':main()
