"""Regressions from the learner's multi-turn path, including HTTP boundaries."""
import json
import threading
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import pytest
from fde_product.agent import Agent
from fde_product.actions import ActionError
from fde_product.business import Business,ROOT,import_sample
from fde_product.models import ScriptedModel
from fde_product.interaction import date_options,selected_option
from fde_product.web import make_server

WINDOWS=dict(baseline_start='2026-07-01',baseline_end='2026-08-01',current_start='2026-08-01',current_end='2026-09-01')
OPTION={'baseline':['2026-07-01','2026-08-01'],'current':['2026-08-01','2026-09-01']}
def call(name,args):return {'role':'assistant','content':None,'tool_calls':[{'id':'c1','type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
def final(status='clarification',**kw):return {'role':'assistant','content':json.dumps({'status':status,'summary':'fixture',**kw})}
class RecordingModel(ScriptedModel):
 def __init__(self,messages):super().__init__(messages);self.calls=0
 def complete(self,*args):self.calls+=1;return super().complete(*args)
@pytest.fixture
def home(tmp_path):import_sample(ROOT/'sample_data',tmp_path/'sample.sqlite');return tmp_path

def analysis_messages(home,**params):
 args=WINDOWS|params;v=Business(home/'sample.sqlite').compare(**args)
 return [call('compare_spending',args),final('completed',evidence_ids=[v['evidence_id']],recommendations=[{'member_id':m,'suggestion':'由运营核实原因后联系','rationale':'错误：两笔有效订单之外另有一笔退款单'} for m in v['declining_members']])]

def test_reject_is_terminal_after_restart_and_allows_changed_target(home):
 a=Agent(home,RecordingModel([call('prepare_tag',{'member_id':'M001','tag':'recall_review'})]),role='operator')
 first=a.run('给 M001 添加标签',action_mode=True);a.close()
 model=RecordingModel([call('prepare_tag',{'member_id':'M004','tag':'recall_review'})])
 a=Agent(home,model,role='operator')
 try:
  def unexpected(*args):raise AssertionError('Unexecuted draft must not reach the API')
  a.ledger.connector.query=unexpected;a.ledger.connector.apply=unexpected
  with pytest.raises(ActionError,match='CONFIRM_OR_CANCEL_PENDING_DRAFT'):a.run('改成 M004',session=first['session_id'],action_mode=True)
  rejected=a.run(session=first['session_id'],resume={'approved':False,'draft_hash':first['confirmation']['draft_hash']})
  assert rejected['status']=='rejected' and 'confirmation' not in rejected and model.calls==0
  assert a.ledger.query(first['confirmation']['draft_id'])['status']=='rejected'
  with pytest.raises(ActionError,match='NO_PENDING_DRAFT'):a.run(session=first['session_id'],resume={'approved':True,'draft_hash':first['confirmation']['draft_hash']})
  changed=a.run('给 M004 添加标签',session=first['session_id'],action_mode=True)
  assert changed['confirmation']['payload']['member_id']=='M004'
  with pytest.raises(ActionError,match='APPROVAL_MISMATCH'):a.run(session=first['session_id'],resume={'approved':True,'draft_hash':first['confirmation']['draft_hash']})
  assert a.ledger.get(changed['confirmation']['draft_id'])['status']=='draft'
 finally:a.close()

@pytest.mark.parametrize('reply',['就按你说的第一个方案，下降至少 20%','按第一个方案','选择第1个方案。'])
def test_natural_confirmation_continues_original_task(home,reply):
 a=Agent(home,RecordingModel([final(time_options=[OPTION])]+analysis_messages(home,min_decline_pct=20)))
 try:
  r=a.run('近期消费下降的老会员');assert r['time_options'][0]['id']=='T1'
  r=a.run(reply,session=r['session_id']);assert r['status']=='completed'
  assert r['observations'][0]['result']['windows']['current']==OPTION['current']
 finally:a.close()

@pytest.mark.parametrize('reply',['不要按第一个方案','第一个方案是什么？','请翻译“按第一个方案”','按第一个方案，改成最近两周','按第一个方案吗？'])
def test_mention_or_modified_period_does_not_confirm(reply):
 assert selected_option(reply,date_options([OPTION])) is None

def test_date_choice_is_session_bound_single_use_and_survives_restart(home):
 a=Agent(home,RecordingModel([final(time_options=[OPTION])]))
 r=a.run('近期下降会员');a.close();model=RecordingModel(analysis_messages(home));a=Agent(home,model)
 choice={'id':'T1','token':r['time_options_token']}
 try:
  with pytest.raises(ActionError,match='TIME_OPTION_EXPIRED'):a.run(session='other',time_choice=choice)
  with pytest.raises(ActionError,match='TIME_OPTION_EXPIRED'):a.run(session=r['session_id'],time_choice=choice|{'token':'stale'})
  assert model.calls==0
  assert a.run(session=r['session_id'],time_choice=choice)['status']=='completed'
  with pytest.raises(ActionError,match='TIME_OPTION_EXPIRED'):a.run(session=r['session_id'],time_choice=choice)
  assert model.calls==2
 finally:a.close()

def test_date_options_can_be_recovered_from_clarification_text(home):
 summary='请确认时段：方案A：baseline 2026-07-01~2026-08-01 vs current 2026-08-01~2026-09-01；方案B：baseline 2026-07-16~2026-08-01 vs current 2026-08-01~2026-08-16。'
 a=Agent(home,RecordingModel([final(summary=summary)]+analysis_messages(home)))
 try:
  first=a.run('近期消费下降的老会员')
  assert first['status']=='clarification'
  assert first['time_options'][0]['windows']==[OPTION['baseline'],OPTION['current']]
  second=a.run(session=first['session_id'],time_choice={'id':'T1','token':first['time_options_token']})
  assert second['status']=='completed'
 finally:a.close()

def test_missing_date_options_fall_back_to_business_reference(home):
 a=Agent(home,RecordingModel([call('describe_business',{}),final()]+analysis_messages(home)))
 try:
  first=a.run('近期消费下降的老会员')
  assert first['status']=='clarification'
  assert first['time_options'][0]['windows']==[OPTION['baseline'],OPTION['current']]
  second=a.run(session=first['session_id'],time_choice={'id':'T1','token':first['time_options_token']})
  assert second['status']=='completed'
 finally:a.close()

def test_new_clarification_invalidates_old_date_option(home):
 newer={'baseline':['2026-06-01','2026-07-01'],'current':['2026-07-01','2026-08-01']}
 a=Agent(home,RecordingModel([final(time_options=[OPTION]),final(time_options=[newer])]))
 try:
  old=a.run('近期下降会员');new=a.run('换一个时间段',session=old['session_id'])
  assert old['time_options_token']!=new['time_options_token']
  with pytest.raises(ActionError,match='TIME_OPTION_EXPIRED'):a.run(session=old['session_id'],time_choice={'id':'T1','token':old['time_options_token']})
 finally:a.close()

def test_bad_date_options_are_not_selectable():
 assert not date_options([{'baseline':['2026-02-30','2026-04-01'],'current':OPTION['current']},OPTION|{'current':OPTION['baseline']},None])

def test_explicit_dates_override_candidate(home):
 a=Agent(home,RecordingModel([final(time_options=[OPTION]),call('compare_spending',WINDOWS),final()]))
 try:
  r=a.run('近期');r=a.run('按第一个方案，比较 2026 年 6 月和 7 月',session=r['session_id'])
  assert r['observations'][0]['result']['code']=='TIME_RANGE_NOT_CONFIRMED'
 finally:a.close()

def test_missing_date_still_blocks_query_after_proposing_option(home):
 a=Agent(home,RecordingModel([final(time_options=[OPTION]),call('compare_spending',WINDOWS),final()]))
 try:
  r=a.run('近期');r=a.run('第一个方案是什么？',session=r['session_id'])
  assert r['observations'][0]['result']['code']=='TIME_RANGE_NOT_CONFIRMED'
 finally:a.close()

def test_factual_explanation_uses_refund_subset_and_preserves_model_suggestion(home):
 a=Agent(home,RecordingModel(analysis_messages(home)))
 try:
  r=a.run('比较 2026 年 7 月和 8 月')
  rec=next(x for x in r['result']['recommendations'] if x['member_id']=='M001')
  row=next(x for x in r['observations'][0]['result']['rows'] if x['member_id']=='M001')
  b=row['order_breakdown']['current']
  assert b=={'paid_count':2,'fully_refunded_count':1,'cancelled_count':1,'paid_cents':50000,'refunded_cents':10000,'net_cents':40000,'paid_order_ids':['O0011','OREF'],'cancelled_order_ids':['OCANCEL']}
  assert '其中 1 笔全额退款' in rec['rationale'] and '支付 500 元，退款 100 元，净消费 400 元' in rec['rationale']
  assert '错误' not in rec['rationale'] and rec['suggestion']=='由运营核实原因后联系'
  state=a.graph.get_state({'configurable':{'thread_id':r['session_id']}}).values
  assert json.loads(state['messages'][-1]['content'])==r['result']
 finally:a.close()

def test_http_date_button_contract_and_chinese_pending_draft_error(home):
 model=RecordingModel([final(time_options=[OPTION])]+analysis_messages(home)+[call('prepare_tag',{'member_id':'M001','tag':'recall_review'})])
 a=Agent(home,model,role='operator');server=make_server(a,actions=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 def post(path,value):
  request=Request(f'http://127.0.0.1:{server.server_port}'+path,data=json.dumps(value).encode(),headers={'Content-Type':'application/json','X-FDE-Client':'local-ui'})
  try:
   with urlopen(request) as response:return response.status,json.load(response)
  except HTTPError as e:return e.code,json.load(e)
 try:
  _,r=post('/api/run',{'text':'近期下降会员'})
  status,r=post('/api/select-time',{'session_id':r['session_id'],'option_id':'T1','options_token':r['time_options_token']})
  assert status==200 and r['status']=='completed'
  _,r=post('/api/run',{'text':'给 M001 打标签','action_mode':True})
  status,error=post('/api/run',{'text':'改成 M004','session_id':r['session_id'],'action_mode':True})
  assert status==409 and error['error']=='CONFIRM_OR_CANCEL_PENDING_DRAFT' and '先点击' in error['message']
  status,r=post('/api/resume',{'session_id':r['session_id'],'draft_hash':r['confirmation']['draft_hash'],'approved':False})
  assert status==200 and r['status']=='rejected' and model.calls==4
  status,error=post('/api/run',[]);assert status==400 and error['message']
 finally:server.shutdown();server.server_close();thread.join();a.close()

@pytest.mark.parametrize('scenario',['date_text','date_button','reject_change'])
def test_shared_multiturn_regression_checks(home,scenario):
 import sys
 sys.path.insert(0,str(ROOT/'tools'))
 from interaction_scenarios import run_scenario
 messages=([call('prepare_tag',{'member_id':'M001','tag':'recall_review'}),call('prepare_tag',{'member_id':'M004','tag':'recall_review'})]
           if scenario=='reject_change' else [final(time_options=[OPTION])]+analysis_messages(home,min_decline_pct=20))
 result=run_scenario(home,ScriptedModel(messages),scenario)
 assert result['scenario_passed'] and all(result['checks'].values())
 assert result['model_mode']=='scripted' and result['rounds']
