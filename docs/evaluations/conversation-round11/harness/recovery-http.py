from pathlib import Path
import httpx,json,sys,time,uuid,hashlib
P=Path(__file__).resolve().parent; D=P/'recovery';D.mkdir(exist_ok=True)
phase=sys.argv[1]; out=D/(phase+'.json');assert not out.exists()
C=httpx.Client(base_url='http://127.0.0.1:8085',trust_env=False,timeout=10)
observations=[]
def req(method,path,body=None,key=None):
 r=C.request(method,path,json=body,headers={'Idempotency-Key':key} if key else None)
 value=r.json() if r.content else None
 observations.append({'at':time.time(),'method':method,'path':path,'request':body,'status':r.status_code,'response':value})
 assert r.status_code<300,(path,r.status_code)
 return value
def status():return req('GET','/api/system/status')
def metric(name):
 r=C.get('/actuator/metrics/'+name)
 observations.append({'at':time.time(),'metric':name,'status':r.status_code,'response':r.json() if r.content else None})
 return r.json() if r.status_code==200 else None
record={'phase':phase,'started':time.time(),'synthetic':True,'external_model_calls':0,'observations':observations}
try:
 if phase=='prepare':
  initial=status();assert not initial['workers']['enabled']
  w=req('POST','/api/workspaces',{'name':'Round11 real broker restart '+str(uuid.uuid4())})['id']
  req('PUT',f'/api/workspaces/{w}/documents/restart',{'title':'Synthetic restart sentinel','text':'restart_sentinel is PERSISTED_QUEUED. Synthetic control only.','source':'synthetic://round11/restart'})
  ids=[]
  for n in range(4):
   ids.append(req('POST',f'/api/workspaces/{w}/tasks',{'question':f'Find restart_sentinel case {n}','strategy':'bm25','mode':'extractive','top_k':1},str(uuid.uuid4()))['id'])
  deadline=time.monotonic()+25
  while True:
   s=status(); rows=[r for r in s['mq']['outbox'] if r['task_id'] in ids]
   if len(rows)==4 and all(r['state']=='PUBLISHED' for r in rows):break
   assert time.monotonic()<deadline;time.sleep(.2)
  tasks=[req('GET',f'/api/workspaces/{w}/tasks/{i}') for i in ids]
  assert all(t['status']=='QUEUED' for t in tasks)
  assert not any(r['task_id'] in ids for r in s['mq']['inbox'])
  fixture={'workspace':w,'task_ids':ids,'event_ids':[r['event_id'] for r in rows]}
  (D/'fixture.json').write_text(json.dumps(fixture,indent=2)+'\n');record.update(fixture=fixture,tasks=tasks,status=s)
 else:
  f=json.loads((D/'fixture.json').read_text());w=f['workspace'];ids=f['task_ids']
  if phase=='broker-down':
   s=status();assert s['workers']['enabled'];time.sleep(2)
   tasks=[req('GET',f'/api/workspaces/{w}/tasks/{i}') for i in ids]
   assert all(t['status']=='QUEUED' for t in tasks)
   assert not any(r['task_id'] in ids for r in s['mq']['inbox'])
   assert metric('lablens.worker.requests') is None
  elif phase=='recovered':
   deadline=time.monotonic()+35
   while True:
    s=status();tasks=[req('GET',f'/api/workspaces/{w}/tasks/{i}') for i in ids]
    if all(t['status']=='SUCCEEDED' for t in tasks) and len([r for r in s['mq']['inbox'] if r['task_id'] in ids])==4:break
    assert time.monotonic()<deadline;time.sleep(.3)
   assert all(not t['cache_hit'] and not t['usage_unknown'] and t['result']['usage']['model_calls']==0 and t['result']['usage']['embedding_requests']==0 for t in tasks)
   m=metric('lablens.worker.requests');assert m['measurements'][0]['value']==4,m
   record['worker_requests']=m
  else:raise ValueError(phase)
  record.update(tasks=tasks,status=s)
 record['passed']=True
except Exception as e:
 record.update(passed=False,error={'type':type(e).__name__,'message':str(e)});raise
finally:
 record['finished']=time.time();out.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'phase':phase,'passed':record.get('passed'),'observations':len(observations)}))
