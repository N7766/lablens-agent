from pathlib import Path
from dotenv import dotenv_values
import os,sys,subprocess,json,time,socket,signal,re,httpx
R=Path.cwd();P=R/'.local/round15';action=sys.argv[1]
def port_open(port):
 with socket.socket() as s:s.settimeout(.2);return s.connect_ex(('127.0.0.1',port))==0
def start(kind):
 port=8770 if kind=='worker' else 8086
 assert not port_open(port),'Port occupied'
 env=os.environ.copy();env.update({k:v for k,v in dotenv_values('/Users/nn/PycharmProjects/lablens-agent/.local/services.env').items() if v is not None})
 if kind=='worker':
  env.update({k:v for k,v in dotenv_values('/Users/nn/PycharmProjects/lablens-agent/.env').items() if k in ('LLM_BASE_URL','LLM_MODEL','LLM_API_KEY') and v is not None})
  env.update(LABLENS_INDEX_DB=str(P/'indexes.sqlite'),EMBEDDING_BASE_URL='https://api.deepinfra.com/v1/openai',EMBEDDING_MODEL='BAAI/bge-m3',EMBEDDING_MODEL_REVISION='deepinfra-bge-m3-observed-2026-11-08',EMBEDDING_BATCH_SIZE='32',RAG_TIMEOUT_SECONDS='20',RAG_TOTAL_SECONDS='80',LABLENS_AGENT_TOOL_BASE_URL='http://127.0.0.1:8770',LABLENS_AGENT_TOTAL_SECONDS='80',LLM_TIMEOUT_SECONDS='45')
  cmd=[str(R/'.venv/bin/python'),'-m','uvicorn','lablens.api:app','--host','127.0.0.1','--port','8770']
 else:
  env.update(json.loads((P/'mq-env.json').read_text()))
  env['LABLENS_DB_URL']=re.sub(r'^(jdbc:mysql://[^/]+/)[^?]+',r'\g<1>lablens_round15_runtime_20261108',env['LABLENS_DB_URL'])
  env.update(LABLENS_WORKSPACE_PORT='8086',LABLENS_WORKER_BASE_URL='http://127.0.0.1:8770',LABLENS_REDIS_PORT='6383',LABLENS_WORKER_TIMEOUT_SECONDS='90',LABLENS_TASK_LEASE_SECONDS='180',LABLENS_CACHE_NAMESPACE='round15-runtime',LABLENS_MQ_ENABLED='true',LABLENS_RATE_LIMIT='1000',LABLENS_DISPATCH_ENABLED='true')
  cmd=['/Users/nn/Library/Java/JavaVirtualMachines/openjdk-24.0.2/Contents/Home/bin/java','-Xms128m','-Xmx512m','-jar',str(P/'candidate.jar')]
 label=sys.argv[3] if len(sys.argv)>3 else 'initial'
 with (P/(kind+'-'+label+'.log')).open('x') as log:p=subprocess.Popen(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 (P/(kind+'.pid')).write_text(str(p.pid));deadline=time.monotonic()+45
 with httpx.Client(trust_env=False,timeout=2) as c:
  while True:
   assert p.poll() is None,'Owned service exited; see private log'
   try:
    r=c.get(f'http://127.0.0.1:{port}'+('/health' if kind=='worker' else '/api/system/status'))
    if r.status_code==200 and (kind=='worker' or r.json().get('database')=='available'):break
   except httpx.HTTPError:pass
   assert time.monotonic()<deadline,'Startup deadline';time.sleep(.2)
 print(json.dumps({'kind':kind,'pid':p.pid,'port':port,'ready':True}))
def stop(kind):
 port=8770 if kind=='worker' else 8086;pid=int((P/(kind+'.pid')).read_text());cmd=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True)
 assert ('uvicorn lablens.api:app' in cmd and '--port 8770' in cmd) if kind=='worker' else str(P/'candidate.jar') in cmd
 os.kill(pid,signal.SIGTERM);deadline=time.monotonic()+20
 while port_open(port):assert time.monotonic()<deadline;time.sleep(.1)
 print(json.dumps({'kind':kind,'pid':pid,'stopped':True}))
(start if action=='start' else stop)(sys.argv[2])
