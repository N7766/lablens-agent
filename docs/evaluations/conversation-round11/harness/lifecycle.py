from pathlib import Path
import subprocess,json,os,sys,time,socket,signal,httpx
P=Path(__file__).resolve().parent;R=P.parents[1];D=P/'recovery';D.mkdir(exist_ok=True)
action=sys.argv[1];record={'action':action,'started':time.time()}
def port_open(port):
 with socket.socket() as s:s.settimeout(.3);return s.connect_ex(('127.0.0.1',port))==0
def wait_until(fn,seconds=40):
 end=time.monotonic()+seconds
 while not fn():
  assert time.monotonic()<end,'deadline';time.sleep(.2)
try:
 if action=='stop-jvm':
  pid=int((P/'jvm-qa.pid').read_text());cmd=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True);assert str(P/'qa-server.jar') in cmd
  record['pid']=pid;os.kill(pid,signal.SIGTERM);wait_until(lambda:not port_open(8085))
 elif action=='start-jvm':
  phase=sys.argv[2];assert not port_open(8085)
  log=(P/f'jvm-{phase}-{int(time.time())}.log').open('w');proc=subprocess.Popen([str(R/'.venv/bin/python'),str(P/'run-server.py'),'jvm',phase],stdout=log,stderr=subprocess.STDOUT,start_new_session=True);log.close();(P/'jvm-qa.pid').write_text(str(proc.pid));record.update(pid=proc.pid,phase=phase,poll_enabled=phase=='normal',dispatcher_enabled=phase!='prepare',consumer_enabled=phase!='prepare')
  with httpx.Client(trust_env=False,timeout=2) as c:
   def ready():
    assert proc.poll() is None,'JVM exited'
    try:return c.get('http://127.0.0.1:8085/api/system/status').status_code==200
    except httpx.HTTPError:return False
   wait_until(ready)
 elif action in ['stop-broker','start-broker']:
  pidfile=P/'rabbitmq/rabbitmq.pid';record['pid_before']=int(pidfile.read_text()) if pidfile.exists() else None
  r=subprocess.run([str(R/'.venv/bin/python'),str(P/'broker.py'),action.split('-')[0]],text=True,capture_output=True,timeout=45);record.update(exit=r.returncode,output=r.stdout);assert r.returncode==0
  wait_until(lambda:port_open(5673)==(action=='start-broker'))
  record.update(amqp_port_open=port_open(5673),pid_after=int(pidfile.read_text()) if pidfile.exists() else None)
  if action=='stop-broker':
   wait_until(lambda:subprocess.run(['ps','-p',str(record['pid_before']),'-o','command='],text=True,capture_output=True).returncode!=0);record['prior_process_exists']=False
 else:raise ValueError(action)
 record['passed']=True
finally:
 record['finished']=time.time()
 with (D/'lifecycle.jsonl').open('a') as out:out.write(json.dumps(record)+'\n')
 print(json.dumps(record))
