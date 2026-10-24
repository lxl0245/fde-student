"""Live multi-turn checks shared by the verifier and Promptfoo provider."""
import threading
from fde_product.agent import Agent
from fde_product.actions import ActionError
from fde_product.tag_mock import make_server

class CountedModel:
    def __init__(self,model):self.inner=model;self.mode=model.mode;self.model=getattr(model,'model','scripted');self.calls=0
    def complete(self,*args):self.calls+=1;return self.inner.complete(*args)

def run_scenario(home,model,scenario):
    model=CountedModel(model);rounds=[];checks={};server=None;thread=None
    if scenario=='reject_change':
        server=make_server(home/'interaction-tags.sqlite',0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    a=Agent(home,model,role='operator' if server else 'analyst',**({'mock_url':f'http://127.0.0.1:{server.server_port}'} if server else {}))
    def run(*args,**kwargs):
        r=a.run(*args,**kwargs);rounds.append(r);return r
    try:
        if scenario in ('date_text','date_button'):
            r=run('找出近期消费下降的老会员，比较前后消费并给出召回建议。')
            checks['clarification_with_options']=r['status']=='clarification' and bool(r.get('time_options')) and not any(o['name']=='compare_spending' and o['result'].get('evidence_id') for o in r['observations'])
            if checks['clarification_with_options']:
                option=r['time_options'][0]
                if scenario=='date_text':r=run('就按你说的第一个方案，下降至少 20%',session=r['session_id'])
                else:r=run(session=r['session_id'],time_choice={'id':option['id'],'token':r['time_options_token']})
                comparisons=[o['result'] for o in r['observations'] if o['name']=='compare_spending' and o['result'].get('evidence_id')]
                checks['selected_windows_queried']=bool(comparisons) and all([c['windows']['baseline'],c['windows']['current']]==option['windows'] for c in comparisons)
                checks['analysis_completed']=r['status']=='completed'
                if scenario=='date_text':checks['threshold_preserved']=bool(comparisons) and all(c['minimum_decline_pct']==20 for c in comparisons)
        elif scenario=='reject_change':
            r=run('给 M001 添加 recall_review 标签，先形成草案等我确认。',action_mode=True)
            checks['first_draft']=r['status']=='awaiting_approval' and r.get('confirmation',{}).get('payload',{}).get('member_id')=='M001'
            if checks['first_draft']:
                first=r['confirmation'];session=r['session_id'];calls=model.calls
                try:run('改成 M004',session=session,action_mode=True);checks['pending_guard']=False
                except ActionError as e:checks['pending_guard']=e.code=='CONFIRM_OR_CANCEL_PENDING_DRAFT'
                r=run(session=session,resume={'approved':False,'draft_hash':first['draft_hash']})
                checks['terminal_rejection']=r['status']=='rejected' and 'confirmation' not in r and model.calls==calls
                checks['no_remote_action']=server.store.query(first['draft_id'])[0]==404
                r=run('给 M004 添加 recall_review 标签，先形成草案等我确认。',session=session,action_mode=True)
                checks['changed_target']=r['status']=='awaiting_approval' and r.get('confirmation',{}).get('payload',{}).get('member_id')=='M004' and a.ledger.get(first['draft_id'])['status']=='rejected'
                if r.get('confirmation'):
                    r=run(session=session,resume={'approved':False,'draft_hash':r['confirmation']['draft_hash']})
                    checks['second_rejection']=r['status']=='rejected' and 'confirmation' not in r
                with server.store.connect() as c:checks['zero_writes']=c.execute('SELECT count(*) FROM actions').fetchone()[0]==0
        else:raise ValueError('UNKNOWN_SCENARIO')
        result={**rounds[-1],'scenario':scenario,'scenario_passed':bool(checks) and all(checks.values()),'checks':checks,'rounds':rounds,'model_calls':model.calls}
        result['usage']={k:sum(r.get('usage',{}).get(k,0) for r in rounds if r['status']!='rejected') for k in ('prompt_tokens','completion_tokens','total_tokens')}
        return result
    finally:
        a.close()
        if server:server.shutdown();server.server_close();thread.join()
