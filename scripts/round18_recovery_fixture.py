#!/usr/bin/env python3
"""Loopback fault fixture: scripted responses and a checkpoint ACK gate, never a real model.

The proxy forwards only checkpoint callbacks to a fixed loopback JVM. It records
no identity headers. Controls are solely for an isolated engineering harness.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit
import httpx
from round17_local_model_fixture import response_for


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8771)
    parser.add_argument('--proxy-port',type=int,default=8772)
    parser.add_argument('--jvm',default='http://127.0.0.1:8086')
    parser.add_argument('--worker',default='http://127.0.0.1:8770')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--events',type=Path,required=True)
    args=parser.parse_args()
    for target in (args.jvm,args.worker):
        url=urlsplit(target)
        if url.scheme!='http' or url.hostname!='127.0.0.1' or url.path or url.username or url.query or url.fragment:
            parser.error('Targets must be fixed loopback HTTP origins')
    for path in (args.output,args.events):path.parent.mkdir(parents=True,exist_ok=True);path.touch(exist_ok=False)
    lock=threading.Lock();release=threading.Event();release.set()
    state={'fixture':'round18-scripted-recovery-only','requests':0,'tool_requests':0,'external_calls':0,'armed':None,'gated':False,'task_id':None}

    def record(path,event):
        with lock:
            with path.open('a') as file:file.write(json.dumps(event,ensure_ascii=False)+'\n')

    class Base(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def send(self,body,status=200):
            body=json.dumps(body).encode() if not isinstance(body,bytes) else body
            try:
                self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
        def read(self):
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=2_000_000:raise ValueError('body limit')
            return json.loads(self.rfile.read(length))
        def gate(self,kind,task=None):
            with lock:
                if state['armed']!=kind:return
                state.update(armed=None,gated=True,task_id=task)
            record(args.events,{'event':'gate_waiting','kind':kind,'task_id':task})
            release.wait(45)
            record(args.events,{'event':'gate_released','kind':kind,'task_id':task})

    class Model(Base):
        def do_GET(self):
            with lock:body=dict(state)
            self.send(body)
        def do_POST(self):
            try:
                payload=self.read()
                if self.path=='/control':
                    kind=payload.get('arm')
                    if kind not in (None,'boundary','inflight_report'):raise ValueError('unknown gate')
                    if kind:release.clear()
                    else:release.set()
                    with lock:state.update(armed=kind,gated=False,task_id=None)
                    self.send({'armed':kind});return
                if self.path!='/v1/chat/completions':self.send({},404);return
                content,kind=response_for(payload)
                body={'choices':[{'message':{'role':'assistant','content':json.dumps(content)},'finish_reason':'stop'}],
                      'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}
                with lock:state['requests']+=1;index=state['requests']
                record(args.output,{'index':index,'kind':kind,'request':payload,'scripted_response':body,'synthetic_usage':True})
                if kind=='scripted_report_choice':self.gate('inflight_report')
                self.send(body)
                record(args.events,{'event':'scripted_response_attempted','index':index,'kind':kind})
            except (KeyError,ValueError,TypeError):self.send({'error':'unsupported fixture request'},422)

    class Proxy(Base):
        def do_POST(self):
            if self.path=='/api/rag/answer':
                try:
                    body=self.read()
                    if body.get('mode')!='extractive' or body.get('strategy')!='bm25' or not self.headers.get('X-LabLens-Parent-Task-Id'):
                        raise ValueError('Only parent-bound BM25 retrieval allowed')
                    headers={key:self.headers[key] for key in ('X-LabLens-Service-Key','X-LabLens-Parent-Task-Id','X-LabLens-Budget-Seconds') if self.headers.get(key)}
                    with lock:state['tool_requests']+=1;index=state['tool_requests']
                    with httpx.Client(trust_env=False,timeout=5) as client:response=client.post(args.worker+self.path,json=body,headers=headers)
                    record(args.events,{'event':'retrieval_http','index':index,'parent_task_id':headers['X-LabLens-Parent-Task-Id'],'snapshot_id':body.get('snapshot_id'),'http_status':response.status_code})
                    self.send(response.content,response.status_code)
                except (httpx.HTTPError,ValueError,TypeError):self.send({'error':'fixture retrieval proxy failure'},502)
                return
            match=re.fullmatch(r'/api/internal/tasks/([0-9a-f-]{36})/checkpoint',self.path)
            if not match:self.send({},404);return
            try:
                body=self.read()
                headers={key:self.headers[key] for key in ('X-LabLens-Service-Key','X-LabLens-Execution-Owner') if self.headers.get(key)}
                with httpx.Client(trust_env=False,timeout=5) as client:response=client.post(args.jvm+self.path,json=body,headers=headers)
                record(args.events,{'event':'checkpoint_persisted','task_id':match[1],'revision':body.get('revision'),'kind':body.get('kind'),'http_status':response.status_code})
                if response.status_code==200 and body.get('kind')=='boundary' and body.get('revision',0)>1:self.gate('boundary',match[1])
                self.send(response.content,response.status_code)
            except (httpx.HTTPError,ValueError,TypeError):self.send({'error':'fixture proxy failure'},502)

    proxy=ThreadingHTTPServer(('127.0.0.1',args.proxy_port),Proxy)
    threading.Thread(target=proxy.serve_forever,daemon=True).start()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Model)
    try:server.serve_forever()
    finally:release.set();proxy.shutdown();proxy.server_close();server.server_close()


if __name__=='__main__':main()
