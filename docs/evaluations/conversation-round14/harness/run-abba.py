from pathlib import Path
import subprocess,json,hashlib,time
R=Path.cwd();P=R/'.local/round14';py=str(R/'.venv/bin/python');candidate=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
windows=[]
for size in [1000,10000,100000]:
 for number,variant in enumerate(['baseline','candidate','candidate','baseline'],1):
  label=f'abba-{size}-{number}-{variant}';row=dict(size=size,window=number,variant=variant,label=label);windows.append(row)
  started=time.monotonic()
  try:
   subprocess.run([py,str(P/'qa.py'),'start',variant,str(size),label],check=True)
   command=[py,'scripts/evaluate_history_scale.py','measure','--size',str(size),'--output',str(P/label),'--variant',variant,'--view','full' if variant=='baseline' else 'summary','--jar',str(P/(variant+'.jar')),'--source-commit','0f732e4ac1dc7b34b41374a021c8100df41e246d' if variant=='baseline' else candidate]
   result=subprocess.run(command);row['measurement_exit_code']=result.returncode
  except Exception as exc:row['operator_error_type']=type(exc).__name__
  finally:
   stopped=subprocess.run([py,str(P/'qa.py'),'stop']);row['stop_exit_code']=stopped.returncode
   row['elapsed_s']=time.monotonic()-started
   (P/'abba-windows.json').write_text(json.dumps(windows,indent=2)+'\n')
  print(json.dumps(row),flush=True)
  if row.get('stop_exit_code') or row.get('operator_error_type'):raise SystemExit('Service lifecycle failure; unstarted windows not executed')
