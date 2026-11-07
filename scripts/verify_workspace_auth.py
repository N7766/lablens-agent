#!/usr/bin/env python3
"""Real JVM/worker identity smoke. Synthetic data, no provider-capable submissions.

Use isolated services. This creates test accounts/workspaces and retains them as
evidence; it never writes credentials, session cookies or CSRF tokens to output.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import time
import uuid
from contextlib import ExitStack
from urllib.parse import urlsplit
import httpx


def run(base: str, worker: str, username: str, password: str, service_key: str) -> dict:
    for url in (base, worker):
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path not in ('', '/'):
            raise ValueError('Smoke requires explicit isolated loopback services')
    checks = []
    def check(name, response, expected):
        ok = response.status_code in (expected if isinstance(expected, tuple) else (expected,))
        checks.append({'name': name, 'status': response.status_code, 'passed': ok})
        if not ok:
            raise AssertionError(f'{name}: HTTP {response.status_code}')
        return response
    with ExitStack() as stack:
        def client():
            return stack.enter_context(httpx.Client(base_url=base, trust_env=False, timeout=20,
                                                   headers={'Origin': base}, follow_redirects=False))
        anonymous = client()
        check('anonymous workspace list', anonymous.get('/api/workspaces'), 401)
        check('anonymous operational status', anonymous.get('/api/system/status'), 401)
        admin = client()
        def login(c, user, pw):
            r = check('login', c.post('/api/auth/login', json={'username': user, 'password': pw}), 200)
            data = r.json()
            assert data['authenticated'] and data['user']['username'] == user
            c.headers['X-CSRF-Token'] = data['csrf_token']
            cookie = r.headers.get('set-cookie', '').lower()
            assert 'httponly' in cookie and 'samesite=strict' in cookie
            return data['user']
        login(admin, username, password)
        check('administrator status', admin.get('/api/system/status'), 200)
        prefix = 'qa' + uuid.uuid4().hex[:10]
        actors = {}
        for role in ('owner', 'editor', 'viewer', 'outsider'):
            name = f'{prefix}_{role}'
            pw = secrets.token_urlsafe(24)
            check('admin provisions account', admin.post('/api/auth/users', json={'username': name, 'password': pw, 'admin': False}), 201)
            c = client()
            identity = login(c, name, pw)
            actors[role] = (c, name, pw, identity)
        owner, ownername, ownerpw, _ = actors['owner']
        editor, editorname, _, _ = actors['editor']
        viewer, viewername, _, _ = actors['viewer']
        outsider, outsidername, _, _ = actors['outsider']
        workspace = check('owner creates workspace', owner.post('/api/workspaces', json={'name': 'Round16 identity smoke'}), 201).json()['id']
        path = f'/api/workspaces/{workspace}'
        check('anonymous known UUID', anonymous.get(path+'/documents'), 401)
        check('admin cannot implicitly read owner documents', admin.get(path+'/documents'), 404)
        check('outsider cannot read known UUID', outsider.get(path+'/documents'), 404)
        assert not outsider.get('/api/workspaces').json()['workspaces']
        owner.headers.pop('X-CSRF-Token')
        check('cookie write requires CSRF', owner.put(path+'/documents/source', json={'title':'source','text':'SFT baseline exists.','source':'synthetic'}), 403)
        owner.headers['X-CSRF-Token'] = owner.get('/api/auth/session').json()['csrf_token']
        check('cross-origin write rejected', owner.post('/api/workspaces', headers={'Origin':'https://untrusted.invalid'},json={'name':'must not exist'}),403)
        check('owner writes document', owner.put(path+'/documents/source', json={'title':'source','text':'The controlled SFT baseline is 72 percent.','source':'round16 synthetic fixture'}), (200,201))
        for name, role in ((editorname,'EDITOR'),(viewername,'VIEWER')):
            check('owner grants membership',owner.put(path+f'/members/{name}',json={'role':role}),200)
        check('viewer reads docs', viewer.get(path+'/documents'), 200)
        check('editor reads docs', editor.get(path+'/documents'), 200)
        check('member cannot administer users', editor.post('/api/auth/users',json={'username':'denied','password':'irrelevant'}),403)
        check('member cannot see system metrics', viewer.get('/api/system/status'),403)
        check('member cannot grant access', editor.put(path+f'/members/{outsidername}',json={'role':'VIEWER'}),403)
        for method, suffix, body in (
            ('PUT','/documents/denied',{'title':'x','text':'x'}),('DELETE','/documents/source',None),
            ('POST','/tasks',{'question':'baseline?','strategy':'bm25','mode':'extractive'}),
            ('POST','/tasks/'+str(uuid.uuid4())+'/cancel',{}),('POST','/conversations',{'title':'no'}),
            ('POST','/conversations/'+str(uuid.uuid4())+'/turns',{}),
            ('POST','/imports',{'documents':[],'expected_revision':1}),
            ('POST','/imports/'+str(uuid.uuid4())+'/cancel',{}),
            ('POST','/index',{'strategy':'bm25','expected_revision':1}),
        ):
            check('viewer denied '+method+' '+suffix.split('/')[1], viewer.request(method,path+suffix,json=body,headers={'Idempotency-Key':str(uuid.uuid4())}),403)
        task = check('editor submits free retrieval',editor.post(path+'/tasks',headers={'Idempotency-Key':str(uuid.uuid4())},json={'question':'What is the SFT baseline?','strategy':'bm25','mode':'extractive','top_k':3}),202).json()
        deadline=time.monotonic()+30
        while task['status'] in ('QUEUED','RUNNING','CANCEL_REQUESTED'):
            if time.monotonic()>deadline:raise AssertionError('free retrieval deadline')
            time.sleep(.1)
            task=editor.get(path+'/tasks/'+task['id']).json()
        assert task['status']=='SUCCEEDED', 'Authenticated worker task failed'
        checks.append({'name':'authenticated JVM to worker extraction completes','passed':True,'task_id':task['id']})
        revision=owner.get(path+'/documents').json()['revision']
        index=check('authenticated index build',owner.post(path+'/index',json={'strategy':'bm25','expected_revision':revision}),200).json()
        assert index['status']=='ready', 'Persistent local BM25 index must be enabled'
        check('viewer reads index state',viewer.get(path+'/index?strategy=bm25'),200)
        old_cookie=owner.cookies.get('LABLENS_SESSION')
        check('logout',owner.post('/api/auth/logout'),(200,204))
        check('server rejects replay of revoked token',anonymous.get(path+'/documents',headers={'Cookie':'LABLENS_SESSION='+old_cookie}),401)
        check('logged-out cookie denied',owner.get(path+'/documents'),401)
        login(owner,ownername,ownerpw)
        restored=check('history read after new login',owner.get(path+'/tasks/'+task['id']),200).json()
        assert restored['id']==task['id'] and restored['status']=='SUCCEEDED'
        check('owner revokes viewer',owner.delete(path+f'/members/{viewername}'),(200,204))
        check('revoked member denied on next request',viewer.get(path+'/documents'),404)
        with httpx.Client(base_url=worker,trust_env=False,timeout=5) as c:
            for route in ('/api/run','/api/plan/draft','/api/rag/answer','/api/rag/indexes/build','/api/rag/executions/'+str(uuid.uuid4())+'/cancel'):
                check('worker anonymous '+route.split('/')[2],c.post(route,json={}),401)
            check('worker rejects wrong key',c.get('/api/rag/status',headers={'X-LabLens-Service-Key':'wrong'}),401)
            check('worker service identity accepted',c.get('/api/rag/status',headers={'X-LabLens-Service-Key':service_key}),200)
        return {'passed':all(row['passed'] for row in checks),'checks':checks,'workspace_id':workspace,
                'task_id':task['id'],'external_model_calls':0,'scope':'isolated synthetic local identity integration; no model quality claim'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8086')
    parser.add_argument('--worker-url',default='http://127.0.0.1:8770')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive marker prevents replacing unfavorable runs with retries.
    with args.output.open('x') as handle:
        handle.write('{"status":"started"}\n')
    try:
        result=run(args.base_url,args.worker_url,os.environ.get('LABLENS_AUTH_BOOTSTRAP_USERNAME','admin'),os.environ['LABLENS_AUTH_BOOTSTRAP_PASSWORD'],os.environ['LABLENS_SERVICE_KEY'])
    except Exception as exc:
        args.output.write_text(json.dumps({'passed':False,'error_type':type(exc).__name__,'error_message':str(exc) if isinstance(exc,AssertionError) else 'See private runner log; no credentials retained'},indent=2)+'\n')
        raise SystemExit('Auth smoke failed; inspect its sanitized output') from None
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'passed':result['passed'],'checks':len(result['checks']),'output':str(args.output)}))

if __name__=='__main__':main()
