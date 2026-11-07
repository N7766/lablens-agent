from pathlib import Path
import subprocess,os,socket,time,httpx,json
r=Path.cwd();p=r/'.local/round14';assert not (p/'mixed-worker.pid').exists()
with socket.socket() as s:s.bind(('127.0.0.1',8770))
with (p/'mixed-worker.log').open('x') as log:
 process=subprocess.Popen([str(r/'.venv/bin/python'),'scripts/controlled_admission_worker.py','--port','8770','--delay-ms','400','--log',str(p/'mixed-worker.jsonl')],stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=os.environ|{'LLM_API_KEY':'','EMBEDDING_API_KEY':''})
(p/'mixed-worker.pid').write_text(str(process.pid));deadline=time.monotonic()+15
with httpx.Client(trust_env=False,timeout=2) as c:
 while True:
  assert process.poll() is None
  try:
   if c.get('http://127.0.0.1:8770/health').status_code==200:break
  except httpx.HTTPError:pass
  assert time.monotonic()<deadline;time.sleep(.1)
source=(p/'qa.py').read_text();assert "LABLENS_DISPATCH_ENABLED='false'" in source
(p/'qa-mixed.py').write_text(source.replace("LABLENS_DISPATCH_ENABLED='false'","LABLENS_DISPATCH_ENABLED='true'"))
print(json.dumps({'worker_pid':process.pid,'port':8770,'fixed_delay_ms':400}))
