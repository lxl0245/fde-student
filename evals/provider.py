"""Promptfoo Python provider; evaluates the actual product with a chosen live model."""
import json,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'platform'))
from fde_product.business import import_sample
from fde_product.agent import Agent
from fde_product.models import CompatibleModel
sys.path.insert(0,str(ROOT/'tools'))
from interaction_scenarios import run_scenario

def call_api(prompt,options,context):
    home=Path(tempfile.mkdtemp(prefix='fde-eval-'));import_sample(ROOT/'sample_data',home/'sample.sqlite')
    start=time.monotonic();scenario=context.get('vars',{}).get('scenario')
    if scenario:result=run_scenario(home,CompatibleModel(timeout=90),scenario)
    else:
        a=Agent(home,CompatibleModel(timeout=90))
        try:result=a.run(prompt)
        finally:a.close()
    return {'output':json.dumps(result,ensure_ascii=False),'tokenUsage':{'total':result['usage'].get('total_tokens',0),'prompt':result['usage'].get('prompt_tokens',0),'completion':result['usage'].get('completion_tokens',0)},'metadata':{'duration_seconds':time.monotonic()-start,'trace_file':str(home/'trace.jsonl'),'model_name':result['model_name']}}
