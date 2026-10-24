#!/usr/bin/env python3
"""Portable artifact checker; execution evidence is separately rerun by reviewers."""
import argparse,json,hashlib,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'platform'))
from jsonschema import Draft202012Validator
REQUIRED_CASES={'analysis','clarification','missing_coverage','readonly','http_action','idempotency','undo'}

def check(root,dry=False):
    root=Path(root).resolve();errors=[];values={}
    if dry:
        for p in (ROOT/'contracts/w01').glob('*.schema.json'):Draft202012Validator.check_schema(json.loads(p.read_text()))
        return {'status':'dry_run','note':'Only schemas were checked; learner execution not verified.','errors':[]}
    for name in ['environment_check','product_fit_gap','model_baseline','runtime_evidence']:
        p=root/'artifacts/w01'/(name+'.json')
        try:
            value=json.loads(p.read_text(encoding='utf-8'));schema=json.loads((ROOT/'contracts/w01'/(name+'.schema.json')).read_text())
            errors += [name+': '+e.message for e in Draft202012Validator(schema).iter_errors(value)];values[name]=value
        except (OSError,ValueError):errors.append(name+': MISSING_OR_INVALID')
    if errors:return {'status':'fail','errors':errors}
    versions={v['product_version'] for v in values.values()}
    if len(versions)!=1:errors.append('PRODUCT_VERSION_MISMATCH')
    env=values['environment_check']
    if any(c['status']!='pass' for c in env['checks'].values()):errors.append('ENVIRONMENT_NOT_READY')
    ev=values['runtime_evidence'];cases={c['case_id']:c for c in ev['cases']}
    if len(cases)!=len(ev['cases']) or not REQUIRED_CASES<=cases.keys() or not all(c['passed'] for c in cases.values()):errors.append('REQUIRED_BEHAVIOR_FAILED')
    known={x['id'] for x in json.loads((ROOT/'product/capabilities.json').read_text())['capabilities']}
    for item in values['product_fit_gap']['items']:
        if not set(item['capability_ids'])<=known:errors.append('UNKNOWN_CAPABILITY_ID')
        if item['evidence_status']=='verified' and not item['evidence']:errors.append('VERIFIED_WITHOUT_EVIDENCE')
    if not (root/'docs/w01/project_assessment.md').is_file():errors.append('PROJECT_ASSESSMENT_MISSING')
    notes=[]
    for rel,expected in ev['artifact_hashes'].items():
        # Earlier Windows runs recorded backslash paths and hashed CRLF bytes; Git or zip tools may since have changed line endings.
        p=(root/rel.replace('\\','/')).resolve()
        if not p.is_relative_to(root) or not p.is_file():errors.append('INVALID_EVIDENCE_PATH');continue
        raw=p.read_bytes();lf=raw.replace(b'\r\n',b'\n')
        if hashlib.sha256(raw).hexdigest()==expected:continue
        if expected in (hashlib.sha256(lf).hexdigest(),hashlib.sha256(lf.replace(b'\n',b'\r\n')).hexdigest()):notes.append('LINE_ENDINGS_NORMALIZED: '+rel)
        else:errors.append('EVIDENCE_HASH_MISMATCH: '+rel)
    for c in cases.values():
        if c['evidence'] not in ev['artifact_hashes']:errors.append('CASE_EVIDENCE_NOT_HASHED: '+c['case_id'])
    for r in values['model_baseline']['runs']:
        if r['run_id'] not in ev['artifact_hashes']:errors.append('BASELINE_RUN_NOT_HASHED')
        if r['actual_status']!=r['expected_status'] or not r['passed']:errors.append('MODEL_BASELINE_FAILED: '+r['case_id'])
    if not any(x['case_id']=='analysis' for x in values['model_baseline']['runs']):errors.append('NO_LIVE_ANALYSIS')
    return {'status':'fail' if errors else 'pass','errors':errors,'notes':notes,'limits':['Artifact checks do not prove authorship or replace live behavior replay and rubric review.']}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',default=str(ROOT));p.add_argument('--dry-run',action='store_true');a=p.parse_args();r=check(a.root,a.dry_run);print(json.dumps(r,ensure_ascii=False,indent=2));raise SystemExit(1 if r['status']=='fail' else 0)
