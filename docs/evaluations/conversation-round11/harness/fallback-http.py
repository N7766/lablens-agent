from pathlib import Path
import httpx,json,sys,time,uuid
P=Path(__file__).resolve().parent;D=P/'recovery';phase=sys.argv[1];out=D/('fallback-'+phase+'.json');assert not out.exists()
C=httpx.Client(base_url='http://127.0.0.1:8085',trust_env=False,timeout=10);records=[]
def req(method,path,body=None):
 r=C.request(method,path,json=body,headers={'Idempotency-Key':str(uuid.uuid4())} if method=='POST' and path.endswith('/tasks') else None)
 v=r.json() if r.content else None;records.append({'at':time.time(),'method':method,'path':path,'request':body,'status':r.status_code,'response':v});assert r.status_code<300;return v
def status():return req('GET','/api/system/status')
result={'phase':phase,'started':time.time(),'observations':records,'external_model_calls':0,'synthetic':True}
try:
 if phase=='prepare':
  assert not status()['workers']['enabled'];w=req('POST','/api/workspaces',{'name':'Round11 polling fallback '+str(uuid.uuid4())})['id']
  req('PUT',f'/api/workspaces/{w}/documents/poll',{'title':'Synthetic poll evidence','text':'poll_sentinel value RECOVERY_POLL, synthetic only.','source':'synthetic://round11/poll'})
  ids=[req('POST',f'/api/workspaces/{w}/tasks',{'question':f'Find poll_sentinel example {n}','strategy':'bm25','mode':'extractive','top_k':1})['id'] for n in range(2)]
  f={'workspace':w,'task_ids':ids};(D/'fallback-fixture.json').write_text(json.dumps(f)+'\n')
 else:f=json.loads((D/'fallback-fixture.json').read_text());w=f['workspace'];ids=f['task_ids']
 deadline=time.monotonic()+25
 while True:
  s=status();tasks=[req('GET',f'/api/workspaces/{w}/tasks/{i}') for i in ids];inbox=[r for r in s['mq']['inbox'] if r['task_id'] in ids];outbox=[r for r in s['mq']['outbox'] if r['task_id'] in ids]
  ready=all(t['status']==('QUEUED' if phase=='prepare' else 'SUCCEEDED') for t in tasks)
  if phase=='recovered':ready=ready and len(inbox)==2 and all(r['state']=='PUBLISHED' for r in outbox)
  if ready:break
  assert time.monotonic()<deadline;time.sleep(.25)
 if phase in ['prepare','broker-down']:assert len(inbox)==0
 if phase!='prepare':
  assert all(not t['cache_hit'] and not t['usage_unknown'] and t['result']['usage']['model_calls']==0 and t['result']['usage']['embedding_requests']==0 for t in tasks)
  m=req('GET','/actuator/metrics/lablens.worker.requests');assert m['measurements'][0]['value']==2;result['worker_requests']=m
 if phase=='recovered':assert all(r['disposition']=='NOT_QUEUED' for r in inbox)
 result.update(passed=True,fixture=f,status=s,tasks=tasks)
except Exception as e:result.update(passed=False,error=str(e));raise
finally:result['finished']=time.time();out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'phase':phase,'passed':result.get('passed')}))
