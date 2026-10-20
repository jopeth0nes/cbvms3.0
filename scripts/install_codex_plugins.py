"""Install CBVMS plugins with the official CLI and preserve user config bytes.

Run with Python 3.11+ from this trusted checkout. Codex uses its normal shared
plugin cache; project config alone enables these plugins. No model call occurs.
"""
from pathlib import Path
import subprocess, tomllib, json
root=Path(__file__).resolve().parents[1]
config=Path.home()/'.codex/config.toml'
before=config.read_bytes(); baseline=tomllib.loads(before.decode())
ids=['cbvms-ecc@cbvms-local','codex-on-crack@cbvms-local']
try:
 for plugin in ids:
  result=subprocess.run(['codex','plugin','add',plugin,'--json'],cwd=root,text=True,capture_output=True)
  print(result.stdout)
  if result.returncode: print(result.stderr); raise SystemExit(result.returncode)
finally:
 after=config.read_bytes()
 if after!=before:
  changed=tomllib.loads(after.decode())
  for plugin in ids:
   if plugin not in baseline.get('plugins',{}): changed.get('plugins',{}).pop(plugin,None)
   else: changed['plugins'][plugin]=baseline['plugins'][plugin]
  if changed!=baseline:
   raise RuntimeError('Concurrent/unexpected global changes found; refusing to overwrite. Inspect config before continuing.')
  config.write_bytes(before)
 print('Global config preserved byte-for-byte:', config.read_bytes()==before)
