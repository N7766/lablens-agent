from pathlib import Path
from dotenv import dotenv_values
import subprocess,socket,time,httpx,os,signal,sys,json,re
R=Path.cwd();P=R/'.local/round14';action=sys.argv[1]
def open_port(port):
 with socket.socket() as s:s.settimeout(.2);return s.connect_ex(('127.0.0.1',port))==0
if action=='start':
 variant,size,label=sys.argv[2:];assert variant in ['baseline','candidate'];assert int(size) in [1000,10000,100000];assert re.fullmatch(r'[a-z0-9-]+',label);assert not open_port(8086)
 env=os.environ.copy();env.update({k:v for k,v in dotenv_values('/Users/nn/PycharmProjects/lablens-agent/.local/services.env').items() if v is not None})
 env['LABLENS_DB_URL']=re.sub(r'^(jdbc:mysql://[^/]+/)[^?]+',r'\g<1>lablens_round14_scale_'+size+'_20261108',env['LABLENS_DB_URL'])
 env.update(LABLENS_WORKSPACE_PORT='8086',LABLENS_WORKER_BASE_URL='http://127.0.0.1:8770',LABLENS_WORKER_TIMEOUT_SECONDS='30',LABLENS_TASK_LEASE_SECONDS='60',LABLENS_CACHE_NAMESPACE='round14-'+size,LABLENS_REDIS_PORT='6383',LABLENS_MQ_ENABLED='false',LABLENS_DISPATCH_ENABLED='false',LABLENS_MAX_PENDING_TASKS='8',LABLENS_MAX_PENDING_GLOBAL='12',LABLENS_RATE_LIMIT='10000',LABLENS_WORKERS='2',LLM_API_KEY='',EMBEDDING_API_KEY='')
 java='/Users/nn/Library/Java/JavaVirtualMachines/openjdk-24.0.2/Contents/Home/bin/java';jar=P/(variant+'.jar')
 with (P/(label+'-jvm.log')).open('x') as log:proc=subprocess.Popen([java,'-Xms128m','-Xmx512m','-jar',str(jar)],stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=env)
 (P/'jvm.pid').write_text(str(proc.pid));deadline=time.monotonic()+40
 with httpx.Client(trust_env=False,timeout=2) as c:
  while True:
   assert proc.poll() is None,'JVM exited: '+label
   try:
    response=c.get('http://127.0.0.1:8086/api/system/status')
    if response.status_code==200 and response.json().get('database')=='available':break
   except httpx.HTTPError:pass
   assert time.monotonic()<deadline,'JVM startup timeout';time.sleep(.2)
 print(json.dumps({'variant':variant,'rows':size,'label':label,'pid':proc.pid,'port':8086,'ready':True}))
elif action=='stop':
 pid=int((P/'jvm.pid').read_text());cmd=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True);assert str(P) in cmd and '.jar' in cmd
 os.kill(pid,signal.SIGTERM);deadline=time.monotonic()+15
 while open_port(8086):assert time.monotonic()<deadline;time.sleep(.2)
 print(json.dumps({'pid':pid,'port_closed':True}))
else:raise ValueError(action)
