#!/usr/bin/env python3
"""Isolated synthetic history seed, SQL plans and read-only HTTP measurements.

Never starts services or uses a provider. Database writes require an empty,
explicitly named Round14 schema; failed output directories cannot be reused.
"""
from __future__ import annotations
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import urlsplit
from uuid import UUID

import httpx

ROOT=Path(__file__).resolve().parents[1]
SIZES=(1000,10000,100000)
WORKSPACES=[str(UUID(int=140000+i)) for i in range(4)]
EPOCH=datetime(2026,1,1,tzinfo=timezone.utc)
ZERO={'model_calls':0,'cache_reused':False,'embedded_inputs':0,'budget_exhausted':False,
      'embedding_tokens':0,'generation_tokens':{'total_tokens':0,'prompt_tokens':0,'completion_tokens':0},
      'embedding_requests':0,'embedding_cache_hits':0,'provider_usage_unknown':False}
SNAPSHOT_PADDING='synthetic-history-snapshot ' * 160
RESULT_PADDING='synthetic-report ' * 128
SUMMARY_COLUMNS=("id,workspace_id,status,JSON_UNQUOTE(JSON_EXTRACT(request_json,'$.question')) AS question,"
    "JSON_UNQUOTE(JSON_EXTRACT(request_json,'$.mode')) AS mode,JSON_UNQUOTE(JSON_EXTRACT(request_json,'$.strategy')) AS strategy,"
    "snapshot_revision,snapshot_document_count,conversation_id,conversation_seq,cache_hit,usage_unknown,worker_ms,error_code,error_message,"
    "created_at,started_at,finished_at,cancel_requested_at,cancel_acknowledged_at")

def dump(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def digest(value):return hashlib.sha256(value).hexdigest()
def task_id(i):return str(UUID(int=140000000+i))
def workspace_index(i):return 0 if i%10 else 1+(i//10)%3
def timestamp(i):return EPOCH+timedelta(seconds=i//8)
def hot_ids(size):return [task_id(i) for i in range(size,0,-1) if workspace_index(i)==0]
def db_name(size):return f'lablens_round14_scale_{size}_20261108'
def sql_json(value):return 'CAST(CONVERT(0x'+json.dumps(value,separators=(',',':')).encode().hex()+" USING utf8mb4) AS JSON)"
def sql_text(value):return 'CONVERT(0x'+str(value).encode().hex()+' USING utf8mb4)'

def mysql(args,statement,timeout=30):
    cmd=[args.mysql,'--no-defaults','--socket='+args.socket,'-uroot','--batch','--skip-column-names','--raw',args.database]
    result=subprocess.run(cmd,input=statement,text=True,capture_output=True,timeout=timeout)
    if result.returncode:raise RuntimeError('MySQL command failed; stderr retained only in local operator logs')
    return result.stdout

def seed(args):
    assert args.database==db_name(args.size),'Only the dedicated named schema is writable'
    assert mysql(args,'SELECT COUNT(*) FROM tasks;SELECT COUNT(*) FROM workspaces;').split()==['0','0'],'Refuse nonempty schema'
    assert shutil.disk_usage(ROOT).free>5*1024**3,'Require 5 GiB free before seeding'
    started=time.monotonic();stream_hash=hashlib.sha256();logical_bytes=0
    for i,w in enumerate(WORKSPACES):
        mysql(args,f"INSERT INTO workspaces(id,name,revision) VALUES ('{w}','Synthetic Round14 {i}',1);")
        mysql(args,f"INSERT INTO documents(workspace_id,id,title,body,source,version) VALUES ('{w}','history','Synthetic history',{sql_text(SNAPSHOT_PADDING)},'synthetic:round14',1);")
    for first in range(1,args.size+1,100):
        if time.monotonic()-started>240:raise TimeoutError('Seed wall-clock budget exhausted; partial schema retained')
        if shutil.disk_usage(ROOT).free<3*1024**3:raise RuntimeError('Stop seeding before disk reserve is exhausted')
        rows=[]
        for i in range(first,min(args.size+1,first+100)):
            w=WORKSPACES[workspace_index(i)];created=timestamp(i).strftime('%Y-%m-%d %H:%M:%S.%f')
            request={'question':f'Synthetic history question {i}','strategy':'bm25','mode':'extractive','top_k':4}
            snapshot={'workspace_id':w,'revision':1,'documents':[{'id':'history','title':'Synthetic history','text':SNAPSHOT_PADDING,'source':'synthetic:round14','version':1}]}
            result={'status':'excerpts','mode':'extractive','answer':RESULT_PADDING,'usage':ZERO,'retrieval':{'snapshot_id':w+':1','strategy':'bm25','hits':[]}}
            logical_bytes+=sum(len(json.dumps(x,separators=(',',':')).encode()) for x in (request,snapshot,result,ZERO))
            rows.append('('+','.join([sql_text(task_id(i)),sql_text(w),sql_text(f'seed-{i}'),sql_text(digest(f'seed-{i}'.encode())),sql_json(request),'1',sql_json(snapshot),'1',"'SUCCEEDED'",sql_json(result),sql_json(ZERO),'0','0','0',sql_text(created),sql_text(created),sql_text(created)])+')')
        statement='INSERT INTO tasks(id,workspace_id,idempotency_key,request_hash,request_json,snapshot_revision,snapshot_json,snapshot_document_count,status,result_json,execution_usage_json,cache_hit,usage_unknown,worker_ms,created_at,started_at,finished_at) VALUES '+','.join(rows)+';'
        stream_hash.update(statement.encode());mysql(args,statement)
    mysql(args,'ANALYZE TABLE tasks;')
    dump(args.output/'seed.json',{'rows':args.size,'workspace_rows':dict(Counter(workspace_index(i) for i in range(1,args.size+1))),
        'sql_stream_sha256':stream_hash.hexdigest(),'logical_json_bytes':logical_bytes,'elapsed_seconds':time.monotonic()-started,
        'synthetic_terminal_rows_not_executed_tasks':True,'workspaces':WORKSPACES,'size':args.size,
        'table_storage':mysql(args,"SELECT TABLE_NAME,TABLE_ROWS,DATA_LENGTH,INDEX_LENGTH FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='tasks';"),
        'actual_counts':mysql(args,'SELECT workspace_id,COUNT(*) FROM tasks GROUP BY workspace_id;'),
        'server_variables':mysql(args,"SHOW VARIABLES WHERE Variable_name IN ('version','innodb_buffer_pool_size','max_allowed_packet','max_connections');")})

def plans(args):
    ids=hot_ids(args.size);offset=int(len(ids)*.8);boundary=ids[offset-1]
    i=UUID(boundary).int-140000000;t=timestamp(i).strftime('%Y-%m-%d %H:%M:%S.%f');w=WORKSPACES[0]
    # Project both SQL pagination alternatives identically; OFFSET is a SQL reference,
    # never falsely presented as an API the historical build supported.
    queries={'legacy_first':f"SELECT * FROM tasks WHERE workspace_id='{w}' ORDER BY created_at DESC,id DESC LIMIT 100",
        'summary_first':f"SELECT {SUMMARY_COLUMNS} FROM tasks WHERE workspace_id='{w}' ORDER BY created_at DESC,id DESC LIMIT 101",
        'summary_deep_offset_reference':f"SELECT {SUMMARY_COLUMNS} FROM tasks WHERE workspace_id='{w}' ORDER BY created_at DESC,id DESC LIMIT 101 OFFSET {offset}",
        'summary_deep_keyset':f"SELECT {SUMMARY_COLUMNS} FROM tasks WHERE workspace_id='{w}' AND (created_at<'{t}' OR (created_at='{t}' AND id<'{boundary}')) ORDER BY created_at DESC,id DESC LIMIT 101",
        'status_exact_count':'SELECT status,COUNT(*) AS total FROM tasks GROUP BY status',
        'detail_by_id':f"SELECT * FROM tasks WHERE workspace_id='{w}' AND id='{ids[0]}'"}
    result={}
    for name,sql in queries.items():
        result[name]={'sql':sql,'explain_json':mysql(args,'EXPLAIN FORMAT=JSON '+sql+';'),
                      'explain_analyze':mysql(args,'EXPLAIN ANALYZE '+sql+';')}
    dump(args.output/'plans.json',{'queries':result,'rows':args.size,'deep_offset':offset,'boundary':boundary,
        'expected_deep_ids':ids[offset:offset+100],'indexes':mysql(args,'SHOW INDEX FROM tasks;'),
        'scope':'Single server execution observations; not latency percentiles or a cold-cache benchmark'})

def percentile(values,f):
    values=sorted(values)
    if not values:return None
    at=(len(values)-1)*f;lo=int(at);hi=min(lo+1,len(values)-1)
    return round(values[lo]+(values[hi]-values[lo])*(at-lo),3)

async def measure(args):
    url=urlsplit(args.endpoint)
    if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost') or url.username or url.query or url.path not in ('','/'):
        raise ValueError('Measurements require a loopback origin')
    w=WORKSPACES[0];ids=hot_ids(args.size);rows=[];inflight=0;peak=0
    scenarios={'list':f'/api/workspaces/{w}/tasks'+('?view=summary&limit=100' if args.view=='summary' else ''),
        'detail':f'/api/workspaces/{w}/tasks/{ids[0]}','status':'/api/system/status'}
    async with httpx.AsyncClient(base_url=args.endpoint,trust_env=False,timeout=10,limits=httpx.Limits(max_connections=8)) as client:
        async def one(scenario,phase,index):
            nonlocal inflight,peak
            start=time.perf_counter();inflight+=1;peak=max(peak,inflight)
            row={'scenario':scenario,'phase':phase,'index':index,'offset_s':start-began}
            try:
                response=await client.get(scenarios[scenario]);row.update(http_status=response.status_code,elapsed_ms=(time.perf_counter()-start)*1000,body_bytes=len(response.content),body_sha256=digest(response.content))
                body=response.json();valid=response.status_code==200
                if scenario=='list':
                    actual=[x['id'] for x in body.get('tasks',[])];valid=valid and actual==ids[:100]
                    if args.view=='summary':valid=valid and body.get('has_more') is True and all('result' not in x and 'snapshot_json' not in x for x in body['tasks'])
                    row['ids_sha256']=digest(json.dumps(actual).encode())
                elif scenario=='detail':valid=valid and body.get('id')==ids[0] and body.get('result',{}).get('answer')==RESULT_PADDING
                else:valid=valid and sum(body.get('task_counts',{}).values())==args.size and body.get('database')=='available'
                row['valid']=valid
            except Exception as exc:row.update(valid=False,error_type=type(exc).__name__,elapsed_ms=(time.perf_counter()-start)*1000)
            finally:inflight-=1;rows.append(row)
        began=time.perf_counter()
        for scenario in scenarios:
            for i in range(5):await one(scenario,'warmup',i)
            for i in range(20):await one(scenario,'sequential',i)
        async def reader(n):
            for i in range(10):await one('list','concurrent',n*10+i)
        await asyncio.gather(*(reader(n) for n in range(8)))
        metrics={}
        for name in ('hikaricp.connections.acquire','hikaricp.connections.timeout','hikaricp.connections.active','tomcat.threads.busy','jvm.memory.used'):
            res=await client.get('/actuator/metrics/'+name);metrics[name]={'http_status':res.status_code,'body':res.json()}
        dump(args.output/'resources-after.json',metrics)
    with (args.output/'requests.jsonl').open('x') as f:
        for row in rows:f.write(json.dumps(row)+'\n')
    grouped={}
    for scenario,phase in {(x['scenario'],x['phase']) for x in rows}:
        subset=[x for x in rows if (x['scenario'],x['phase'])==(scenario,phase)];values=[x['elapsed_ms'] for x in subset]
        grouped[scenario+':'+phase]={'n':len(subset),'valid':sum(x['valid'] for x in subset),'p50_ms':percentile(values,.5),'p95_ms':percentile(values,.95),'max_ms':max(values),'response_bytes':sorted({x.get('body_bytes') for x in subset if 'body_bytes' in x})}
    dump(args.output/'summary.json',{'size':args.size,'variant':args.variant,'view':args.view,'passed':all(x['valid'] for x in rows),'max_client_in_flight':peak,'requests':len(rows),'groups':grouped,
        'scope':'Same-host warmed closed-loop read workload, eight concurrent list clients; no open-arrival or production SLA claim'})

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('seed','plans','measure'))
    parser.add_argument('--size',type=int,choices=SIZES,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--database');parser.add_argument('--mysql',default='/opt/homebrew/opt/mysql/bin/mysql');parser.add_argument('--socket',default='/Users/nn/PycharmProjects/lablens-agent/.local/mysql.sock')
    parser.add_argument('--endpoint',default='http://127.0.0.1:8086');parser.add_argument('--variant',choices=('baseline','candidate'),default='candidate');parser.add_argument('--view',choices=('full','summary'),default='summary')
    parser.add_argument('--jar',type=Path);parser.add_argument('--source-commit')
    args=parser.parse_args();args.database=args.database or db_name(args.size)
    if args.database!=db_name(args.size):parser.error('Only the dedicated size-specific Round14 database is supported')
    if args.action=='measure' and (not args.jar or not args.jar.is_file() or not args.source_commit or not re.fullmatch('[a-f0-9]{40}',args.source_commit)):
        parser.error('measure requires a real jar and exact source commit')
    args.output.mkdir(parents=True,exist_ok=False)
    before=digest(Path(__file__).read_bytes())
    dump(args.output/'manifest.json',{'action':args.action,'size':args.size,'variant':args.variant,'view':args.view,'database':args.database,'script_sha256':before,'source_commit':args.source_commit,'jar_sha256':digest(args.jar.read_bytes()) if args.jar else None,'started_utc':datetime.now(timezone.utc).isoformat(),'runtime':'No provider calls; deterministic seed 140000000; terminal synthetic fixtures are not executed jobs'})
    try:
        if args.action=='measure':
            asyncio.run(measure(args))
            if not json.loads((args.output/'summary.json').read_text())['passed']:raise RuntimeError('Response correctness failed; original observations retained')
        elif args.action=='seed':seed(args)
        else:plans(args)
        if digest(Path(__file__).read_bytes())!=before:raise RuntimeError('Harness source changed during operation')
    except BaseException as exc:
        dump(args.output/'failure.json',{'type':type(exc).__name__,'message':str(exc) if type(exc) in (AssertionError,ValueError,RuntimeError,TimeoutError) else 'Operation failed; partial state retained'})
        raise

if __name__=='__main__':main()
