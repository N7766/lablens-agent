#!/usr/bin/env python3
"""Deterministic loopback-only protocol fixture. It is NOT a language model/evaluator."""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import threading

QUOTE='The measured accuracy was 86 percent.'

def response_for(payload):
    schema=payload['response_format']['json_schema']['name']
    data=[json.loads(m['content']) for m in payload['messages'] if m['role']=='user']
    if schema in ('evidence_review_by_id','evidence_assessments'):
        current=data[-1]
        rows=current['untrusted_draft']['assessments']
        # Deliberately scripted replacements, not an independent semantic review.
        replacements=[dict(assessment_id=r['assessment_id'],verdict='supported',basis='historical_observation',
            explanation='The synthetic source explicitly reports 86 percent.',evidence_ids=list(current['passages'])) for r in rows]
        return {'retained_assessment_ids':[],'assessments':replacements},'scripted_review'
    outputs=[d['untrusted_tool_result'] for d in data if 'untrusted_tool_result' in d]
    if not outputs:
        return {'decision':{'action':'search_workspace','arguments':{'query':'accuracy','strategy':'bm25','top_k':3}}},'scripted_search_choice'
    return {'decision':{'action':'verify_and_submit_report','arguments':{'assessments':[dict(statement=QUOTE,verdict='supported',
        explanation='The current synthetic source reports 86 percent.',basis='historical_observation',evidence_ids=list(outputs[-1]['passages']))]}}},'scripted_report_choice'

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,default=8771);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.touch(exist_ok=False);lock=threading.Lock();count=0
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            body=json.dumps({'fixture':'round17-scripted-protocol-only','requests':count,'external_calls':0}).encode();self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_POST(self):
            nonlocal count
            length=int(self.headers.get('Content-Length','0'))
            if self.path!='/v1/chat/completions' or not 0<length<=2_000_000:self.send_error(400);return
            payload=json.loads(self.rfile.read(length))
            try:
                content,kind=response_for(payload)
                body=json.dumps({'choices':[{'message':{'role':'assistant','content':json.dumps(content)},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}).encode()
                with lock:
                    count+=1
                    with a.output.open('a') as f:f.write(json.dumps({'index':count,'kind':kind,'request':payload,'response':json.loads(body),'synthetic_usage':True})+'\n')
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            except (KeyError,ValueError,TypeError):self.send_error(422,'Unsupported synthetic fixture payload')
    server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler)
    try:server.serve_forever()
    finally:server.server_close()
if __name__=='__main__':main()
