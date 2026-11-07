"""Record owned verification commands/start times without touching production data."""
from pathlib import Path
import datetime,json,os,shlex,subprocess,sys,time
root=Path(__file__).resolve().parents[2]; out=root/'output/uniform-stage4'
name=sys.argv[1]; command=sys.argv[2:]
assert name.replace('-','').isalnum() and command
started=datetime.datetime.now(datetime.timezone.utc).isoformat()
record={'wrapper_pid':os.getpid(),'wrapper_command':shlex.join(sys.argv),'started_at_utc':started,'command':shlex.join(command)}
with (out/(name+'.log')).open('w') as log:
 process=subprocess.Popen(command,cwd=root,stdout=log,stderr=subprocess.STDOUT)
 record['pid']=process.pid
 (out/(name+'-process.json')).write_text(json.dumps(record,indent=2))
 with (root/'docs/UNIFORM_UPGRADE_CHECKPOINT.md').open('a') as checkpoint:
  checkpoint.write(f"\nOwned Stage 4 check: PID {process.pid}, start {started}, command `{shlex.join(command)}`; wrapper PID {os.getpid()}, command `{shlex.join(sys.argv)}`. Output `{out.name}/{name}.log`.\n")
 print(json.dumps(record),flush=True)
 try: code=process.wait(timeout=180)
 except subprocess.TimeoutExpired:
  process.terminate()
  try:process.wait(timeout=5)
  except subprocess.TimeoutExpired:process.kill();process.wait()
  code=124
 record.update(returncode=code,finished_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
 (out/(name+'-process.json')).write_text(json.dumps(record,indent=2))
 with (root/'docs/UNIFORM_UPGRADE_CHECKPOINT.md').open('a') as checkpoint:
  checkpoint.write(f"\nOwned check `{name}` above completed, exit {code}, at {record['finished_at_utc']}; no owned child remains.\n")
 print('Completed',name,'exit',code,flush=True)
 sys.exit(code)
