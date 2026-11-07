from pathlib import Path
from dotenv import dotenv_values
import os,json,re,sys
R=Path(__file__).resolve().parents[2];P=R/'.local/round12';os.chdir(R);variant=sys.argv[1];assert variant in ['baseline','candidate']
env={k:v for k,v in dotenv_values('/Users/nn/PycharmProjects/lablens-agent/.local/services.env').items() if v is not None};os.environ.update(env)
os.environ['LABLENS_DB_URL']=re.sub(r'^(jdbc:mysql://[^/]+/)[^?]+',r'\g<1>lablens_round12_'+variant+'_20261108',os.environ['LABLENS_DB_URL'])
os.environ.update(LABLENS_WORKSPACE_PORT='8086',LABLENS_WORKER_BASE_URL='http://127.0.0.1:8770',LABLENS_WORKER_TIMEOUT_SECONDS='30',LABLENS_TASK_LEASE_SECONDS='60',LABLENS_CACHE_NAMESPACE='round12-'+variant,LABLENS_REDIS_PORT='6383',LABLENS_MQ_ENABLED='false',LABLENS_MAX_PENDING_TASKS='8',LABLENS_MAX_PENDING_GLOBAL='12',LABLENS_RATE_LIMIT='10000',LABLENS_WORKERS='2')
java='/Users/nn/Library/Java/JavaVirtualMachines/openjdk-24.0.2/Contents/Home/bin/java';jar=R/'.local/round11/qa-server.jar' if variant=='baseline' else P/'candidate.jar'
os.execv(java,[java,'-Xms128m','-Xmx512m','-jar',str(jar)])
