from pathlib import Path
import subprocess,socket,time,httpx,os,signal,sys,json
R=Path.cwd();P=R/'.local/round13';action=sys.argv[1];variant=sys.argv[2];assert variant in ['baseline','candidate']
def open_port(port):
 with socket.socket() as s:s.settimeout(.2);return s.connect_ex(('127.0.0.1',port))==0
if action=='start':
 for role,port,url,cmd in [('worker',8770,'http://127.0.0.1:8770/health',[str(R/'.venv/bin/python'),'scripts/controlled_admission_worker.py','--port','8770','--delay-ms','400','--log',str(P/(variant+'-worker.jsonl'))]),('jvm',8086,'http://127.0.0.1:8086/api/system/status',[str(R/'.venv/bin/python'),str(P/'run-jvm.py'),variant])]:
  assert not open_port(port)
  with (P/(variant+'-'+role+'.log')).open('w') as log:proc=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=os.environ|{'LLM_API_KEY':'','EMBEDDING_API_KEY':''})
  (P/(role+'.pid')).write_text(str(proc.pid));deadline=time.monotonic()+40
  with httpx.Client(trust_env=False,timeout=2) as c:
   while True:
    assert proc.poll() is None,role+' exited'
    try:
     if c.get(url).status_code==200:break
    except httpx.HTTPError:pass
    assert time.monotonic()<deadline,role+' startup timeout';time.sleep(.2)
  print(json.dumps({'variant':variant,'role':role,'pid':proc.pid,'port':port,'ready':True}))
elif action=='stop':
 for role,port in [('jvm',8086),('worker',8770)]:
  pid=int((P/(role+'.pid')).read_text());cmd=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True)
  assert ('.local/round11/qa-server.jar' in cmd or '.local/round13/candidate.jar' in cmd) if role=='jvm' else 'controlled_admission_worker.py' in cmd
  os.kill(pid,signal.SIGTERM);deadline=time.monotonic()+15
  while open_port(port):assert time.monotonic()<deadline;time.sleep(.2)
  print(json.dumps({'variant':variant,'role':role,'pid':pid,'port_closed':True}))
else:raise ValueError(action)
