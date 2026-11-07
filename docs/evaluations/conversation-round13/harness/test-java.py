from pathlib import Path
from dotenv import dotenv_values
import os,json,re,subprocess,sys
R=Path.cwd();P=R/'.local/round13';env=os.environ.copy();values=dotenv_values('/Users/nn/PycharmProjects/lablens-agent/.local/services.env');env.update({k:v for k,v in values.items() if v is not None});env.update(json.loads((P/'mq-env.json').read_text()));env['LABLENS_DB_URL']=re.sub(r'^(jdbc:mysql://[^/]+/)[^?]+',r'\g<1>lablens_round13_tests_20261108',env['LABLENS_DB_URL']);env.update(LABLENS_REDIS_PORT='6383',LABLENS_INTEGRATION_TESTS='true',LABLENS_MQ_INTEGRATION_TESTS='true',LABLENS_MQ_ENABLED='false',LABLENS_MQ_EVIDENCE_DIR=str(P/'java-mq-evidence'))
env['JAVA_HOME']='/Users/nn/Library/Java/JavaVirtualMachines/openjdk-24.0.2/Contents/Home';env['PATH']=env['JAVA_HOME']+'/bin:'+env['PATH']
name=sys.argv[1];args=sys.argv[2:];log=P/(name+'.log');assert not log.exists()
with log.open('w') as out:code=subprocess.call([str(R/'.local/tooling/apache-maven-3.9.16/bin/mvn'),'-B','-f','services/workspace/pom.xml',*args],env=env,stdout=out,stderr=subprocess.STDOUT)
print(json.dumps({'log':str(log),'exit_code':code}));raise SystemExit(code)
