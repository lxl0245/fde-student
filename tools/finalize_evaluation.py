#!/usr/bin/env python3
"""Merge an actual Promptfoo report; cannot turn failed evals into passed state."""
from pathlib import Path
import argparse,json
p=argparse.ArgumentParser();p.add_argument('--root',default=str(Path(__file__).resolve().parents[1]));a=p.parse_args();out=Path(a.root)/'artifacts/w01'
r=json.loads((out/'promptfoo-results.json').read_text());stats=r['results']['stats'];cases=r['results']['results']
ok=stats['successes']>=3 and stats['failures']==0 and stats.get('errors',0)==0 and len(cases)>=3 and all(c['success'] and json.loads(c['response']['output']).get('model_mode')=='live' for c in cases)
# A report left over from another model must not count for the model in this run's baseline.
model=json.loads((out/'model_baseline.json').read_text()).get('model_name')
same=ok and all(json.loads(c['response']['output']).get('model_name')==model for c in cases);code='PROMPTFOO_REPORT_VERIFIED' if same else ('PROMPTFOO_MODEL_MISMATCH' if ok else 'PROMPTFOO_FAILED');ok=same
env=json.loads((out/'environment_check.json').read_text());env['checks']['evaluation']={'status':'pass' if ok else 'fail','code':code};(out/'environment_check.json').write_text(json.dumps(env,ensure_ascii=False,indent=2)+'\n');print(env['checks']['evaluation']);raise SystemExit(0 if ok else 1)
