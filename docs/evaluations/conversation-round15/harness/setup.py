from pathlib import Path
from dotenv import dotenv_values
import os,json,re,secrets,subprocess,socket
R=Path.cwd();P=R/'.local/round15';B=P/'rabbitmq';B.mkdir(exist_ok=True)
for port in [5674,25674,6383,8086,8770]:
 with socket.socket() as s:s.bind(('127.0.0.1',port))
values=dotenv_values('/Users/nn/PycharmProjects/lablens-agent/.local/services.env');user=values['LABLENS_DB_USER'];assert re.fullmatch(r'[A-Za-z0-9_]+',user)
mysql=['/opt/homebrew/opt/mysql/bin/mysql','--no-defaults','--socket=/Users/nn/PycharmProjects/lablens-agent/.local/mysql.sock','-uroot','--batch','--skip-column-names']
def sql(s):
 r=subprocess.run(mysql,input=s,text=True,capture_output=True);assert r.returncode==0,'DB setup failed; private stderr withheld';return r.stdout.strip()
created={}
for kind in ['tests','runtime']:
 database='lablens_round15_'+kind+'_20261108';assert int(sql("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='"+database+"';"))==0,'Refuse nonempty DB'
 sql('CREATE DATABASE IF NOT EXISTS '+database+' CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;');sql('GRANT ALL PRIVILEGES ON '+database+".* TO '"+user+"'@'127.0.0.1';");created[kind]=database
(P/'databases.json').write_text(json.dumps(created,indent=2)+'\n')
password=secrets.token_hex(24);cookie=secrets.token_hex(32)
(B/'enabled_plugins').write_text('[].\n');(B/'isolated-env.conf').write_text('# isolated per-round broker\n')
(B/'rabbitmq.conf').write_text('listeners.tcp.1 = 127.0.0.1:5674\ndistribution.listener.interface = 127.0.0.1\ndistribution.listener.port_range.min = 25674\ndistribution.listener.port_range.max = 25674\nvm_memory_high_watermark.absolute = 512MiB\ndisk_free_limit.absolute = 512MiB\ndefault_user = lablens_round15\ndefault_pass = '+password+'\ndefault_vhost = round15\n');(B/'rabbitmq.conf').chmod(0o600)
env={'RABBITMQ_NODENAME':'lablens_round15@localhost','RABBITMQ_NODE_IP_ADDRESS':'127.0.0.1','RABBITMQ_NODE_PORT':'5674','RABBITMQ_DIST_PORT':'25674','RABBITMQ_MNESIA_BASE':str(B/'data'),'RABBITMQ_LOG_BASE':str(B/'logs'),'RABBITMQ_PID_FILE':str(B/'rabbitmq.pid'),'RABBITMQ_CONFIG_FILE':str(B/'rabbitmq.conf'),'RABBITMQ_CONF_ENV_FILE':str(B/'isolated-env.conf'),'RABBITMQ_ENABLED_PLUGINS_FILE':str(B/'enabled_plugins'),'RABBITMQ_ERLANG_COOKIE':cookie,'ERL_EPMD_ADDRESS':'127.0.0.1','RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS':'-kernel inet_dist_use_interface {127,0,0,1}'}
(P/'broker-env.json').write_text(json.dumps(env));(P/'broker-env.json').chmod(0o600)
mq={'LABLENS_MQ_HOST':'127.0.0.1','LABLENS_MQ_PORT':'5674','LABLENS_MQ_USERNAME':'lablens_round15','LABLENS_MQ_PASSWORD':password,'LABLENS_MQ_VHOST':'round15'}
(P/'mq-env.json').write_text(json.dumps(mq));(P/'mq-env.json').chmod(0o600)
log=(B/'launcher.log').open('w');proc=subprocess.Popen(['/opt/homebrew/opt/rabbitmq/sbin/rabbitmq-server'],env=os.environ|env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True);log.close();(B/'launcher.pid').write_text(str(proc.pid))
redisdir=P/'redis';redisdir.mkdir()
(redisdir/'redis.conf').write_text('pidfile '+str(redisdir/'pid')+'\nbind 127.0.0.1\nport 6383\nprotected-mode yes\nsave ""\nappendonly no\nmaxmemory 128mb\nmaxmemory-policy noeviction\ndir '+str(redisdir)+'\n')
with (redisdir/'server.log').open('w') as log:
 rp=subprocess.Popen([str(R/'.local/tooling/redis-8.2.2/src/redis-server'),str(redisdir/'redis.conf')],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
(redisdir/'pid').write_text(str(rp.pid));print(json.dumps({'databases':created,'redis_pid':rp.pid,'redis_port':6383,'broker_port':5674}))
