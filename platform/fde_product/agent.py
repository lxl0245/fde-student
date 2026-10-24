"""LangGraph product runtime; model choices, typed business tools, durable approval."""
from typing import TypedDict
from pathlib import Path
import json,sqlite3,threading,hashlib,uuid,re,time
from jsonschema import validate,ValidationError
from langgraph.graph import StateGraph,START,END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt,Command
from langsmith import tracing_context
from .business import Business,BusinessError,ROOT,VERSION,RUNTIME_REVISION,explicit_windows
from .actions import Ledger,ActionError,TagConnector
from .models import ModelError, call_summary
from .observability import setup
from .interaction import date_options,date_options_from_business,date_options_from_text,selected_option,ground_analysis

class State(TypedDict,total=False):
    messages:list
    steps:int
    repairs:int
    usage:dict
    confirmed_windows:list
    time_options:list
    time_options_token:str
    observations:list
    draft:dict|None
    result:dict
    action_mode:bool
    session:str

class Agent:
    def __init__(self,home,model,mock_url='http://127.0.0.1:8766',mock_token='training-demo-token',role='analyst',semantics=None,prompt=None,otlp_endpoint=None,otlp_headers=None):
        self.home=Path(home);self.home.mkdir(parents=True,exist_ok=True);self.model=model;self.role=role
        self.business=Business(self.home/'sample.sqlite',semantics);self.ledger=Ledger(self.home/'actions.sqlite',TagConnector(mock_url,mock_token))
        self.prompt=Path(prompt or ROOT/'product/system_prompt.txt').read_text();self.prompt_hash=hashlib.sha256(self.prompt.encode()).hexdigest()
        self.provider,self.tracer=setup(self.home/'trace.jsonl',otlp_endpoint,otlp_headers)
        self.connection=sqlite3.connect(self.home/'checkpoints.sqlite',check_same_thread=False);self.saver=SqliteSaver(self.connection);self.lock=threading.RLock()
        graph=StateGraph(State)
        for name,fn in [('model',self.model_node),('tools',self.tool_node),('approval',self.approval_node),('execute',self.execute_node)]:graph.add_node(name,fn)
        graph.add_edge(START,'model');graph.add_conditional_edges('model',self.after_model)
        graph.add_conditional_edges('tools',lambda s:'approval' if s.get('draft') else 'model')
        graph.add_conditional_edges('approval',lambda s:END if s.get('result',{}).get('status')=='rejected' else 'execute')
        graph.add_edge('execute','model');self.graph=graph.compile(checkpointer=self.saver)
    def close(self):self.provider.shutdown();self.connection.close()
    def answer_business_model(self,workspace,config_path,question,**options):
        from .natural_knowledge import answer_business_model
        with self.lock,tracing_context(enabled=False),self.tracer.start_as_current_span('agent.business_model'):
            return answer_business_model(self,workspace,config_path,question,**options)

    def answer_knowledge(self,workspace,config_path,case_id,*,include_knowledge=True):
        # Customer definitions use a fresh, tool-free context. W1 graph state and
        # durable approval are neither consumed nor changed by this entry point.
        from .customer_knowledge import answer_knowledge
        with self.lock,tracing_context(enabled=False),self.tracer.start_as_current_span('agent.knowledge'):
            return answer_knowledge(self,workspace,config_path,case_id,include_knowledge=include_knowledge)

    def schemas(self,action_mode):
        specs=[('describe_business','查询样例的对象、可执行口径、业务参考日期和数据覆盖',{},[]),('compare_spending','比较已明确的两个时段的老会员净消费，筛选下降人群，返回证据。结束日期不包含当天。',{'baseline_start':{'type':'string'},'baseline_end':{'type':'string'},'current_start':{'type':'string'},'current_end':{'type':'string'},'store_id':{'type':'string'},'min_decline_pct':{'type':'number','minimum':0,'maximum':100}},['baseline_start','baseline_end','current_start','current_end']),('member_history','读取一个会员的订单依据',{'member_id':{'type':'string'}},['member_id'])]
        if action_mode and self.role=='operator':
            specs += [('prepare_tag','创建样例标签草案，必须由界面明确确认后才能执行。只支持一个会员。',{'member_id':{'type':'string'},'tag':{'enum':['recall_review','follow_up']}},['member_id','tag']),('query_tag','通过 HTTP 核对本任务已创建的标签动作',{'action_id':{'type':'string'}},['action_id'])]
        return [{'type':'function','function':{'name':n,'description':d,'parameters':{'type':'object','properties':p,'required':r,'additionalProperties':False}}} for n,d,p,r in specs]
    def model_node(self,state):
        if state['steps']>=10:return {'result':{'status':'incomplete','summary':'达到调用上限，请查看工具记录。'}}
        comparisons=[o['result'] for o in state['observations'] if o['name']=='compare_spending' and o['result'].get('evidence_id')]
        actions=[o['result'] for o in state['observations'] if o['name'] in ('tag_execution','query_tag') and o['result'].get('id')]
        turn_context='\n本轮执行边界：历史消息只帮助理解需求，历史工具结果不能替代本轮证据。历史中的输出修复指令只针对当时的轮次。本轮已取得有效消费比较 '+str(len(comparisons))+' 次，动作结果 '+str(len(actions))+' 次。用户已确认时段：'+json.dumps(state.get('confirmed_windows',[]),ensure_ascii=False)+'。处理消费分析时，本轮缺少比较证据应按当前需求调用工具，没有确认时段时先澄清。已有本轮有效证据时，可以据此组织最终回答。标签任务按动作工具及受控确认流程办理，无需补充消费比较时段。'
        with self.tracer.start_as_current_span('model.call') as span:
            span.set_attribute('model.name',getattr(self.model,'model','scripted'));span.set_attribute('step',state['steps'])
            try:message,usage=self.model.complete([{'role':'system','content':self.prompt+turn_context+'\n当前主机模式：'+('样例动作模式。可提案和查询；批准、执行与撤销由受控入口处理。' if state['action_mode'] else '只读分析模式，没有标签动作权限。')}]+state['messages'],self.schemas(state['action_mode']))
            except ModelError as e:
                span.set_attribute('error.code',e.code);span.set_attribute('model.call',json.dumps(call_summary(self.model),ensure_ascii=False));return {'result':{'status':'incomplete','summary':e.code},'steps':state['steps']+1}
            span.set_attribute('model.call',json.dumps(call_summary(self.model),ensure_ascii=False))
            for k in ['prompt_tokens','completion_tokens','total_tokens']:
                if isinstance(usage.get(k),(int,float)):span.set_attribute(k,usage[k])
        update={'messages':state['messages']+[message],'steps':state['steps']+1,'usage':{k:state.get('usage',{}).get(k,0)+usage.get(k,0) for k in ['prompt_tokens','completion_tokens','total_tokens'] if isinstance(usage.get(k,0),(int,float))}}
        if not message.get('tool_calls'):
            try:
                result=json.loads(message.get('content') or '{}')
                if not isinstance(result,dict) or result.get('status') not in ['completed','clarification','refused','incomplete']:raise ValueError()
                if result['status']=='completed' and actions:
                    if actions[-1]['status'] not in ('applied','undone'):raise ValueError()
                    if not result.get('evidence_ids') or not set(result['evidence_ids'])<={a['id'] for a in actions}:raise ValueError()
                if result['status']=='completed' and not actions:

                    if not comparisons:raise ValueError()
                    ids={c['evidence_id'] for c in comparisons};members={m for c in comparisons for m in c['declining_members']}
                    if not set(result.get('evidence_ids',[])) or not set(result['evidence_ids'])<=ids:raise ValueError()
                    if {r.get('member_id') for r in result.get('recommendations',[])}!=members:raise ValueError()
                # An empty result caused by missing coverage is not a completed
                # comparison. Decide this from tool evidence, independent of the
                # model's wording; partial coverage with usable rows stays valid.
                cited=[c for c in comparisons if c['evidence_id'] in result.get('evidence_ids',[])]
                if result['status']=='completed' and not actions and cited and all(not c.get('rows') for c in cited) and any(c.get('missing_coverage_stores') for c in cited):
                    stores=sorted({s for c in cited for s in c.get('missing_coverage_stores',[])})
                    result={**result,'status':'incomplete','recommendations':[],
                            'summary':'、'.join(stores)+' 的所选比较时段缺少数据，暂时无法形成下降会员名单。缺失数据不能按零消费处理。'}
                    with self.tracer.start_as_current_span('output.normalize') as correction:
                        correction.set_attributes({'reason':'DATA_COVERAGE_INSUFFICIENT','model_status':'completed','status':'incomplete'})
                if result['status']=='clarification':
                    options=date_options(result.get('time_options',[]))
                    if not options:
                        options=date_options_from_text(result.get('summary',''))
                    if not options:
                        options=date_options_from_business(state.get('observations',[]))
                    result.pop('time_options',None)
                    update['time_options']=options
                    update['time_options_token']=uuid.uuid4().hex if options else ''
                    if options:
                        result['summary']='请确认比较时段。可点击下面的日期方案，或回复“按第一个方案”等；也可直接输入带年份的两个完整月份。'
                if result['status']=='completed' and not actions:
                    result=ground_analysis(result,cited)
                update['result']=result
                update['messages'][-1]={**message,'content':json.dumps(result,ensure_ascii=False)}
            except (ValueError,TypeError,AttributeError) as exc:
                code='MODEL_OUTPUT_INVALID_JSON' if isinstance(exc,json.JSONDecodeError) else 'MODEL_OUTPUT_UNGROUNDED'
                if state.get('repairs',0)<2 and state['steps']<9:
                    update['repairs']=state.get('repairs',0)+1
                    if not comparisons and not actions:
                        repair='本轮尚无可用于完成回答的工具证据，历史工具结果不能替代本轮查询。'
                        repair+=('如果当前需求是消费分析，请根据当前需求和已确认时段调用 compare_spending，再基于本轮结果生成最终 JSON。' if state.get('confirmed_windows') else '如果当前需求是消费分析，比较时段尚未确认，请返回 clarification，询问明确时段；不要猜测时段查询。')
                        repair+='动作只能通过本轮提供的工具和受控确认入口办理，不能复用历史执行结论。'
                    else:
                        repair='请仅修复最终 JSON 的格式、引用或建议名单；保持本轮已有工具事实，evidence_ids 必须逐字引用本轮返回的 evidence_id 或动作 id，不自行增添前缀。消费分析的建议名单必须对应本轮 declining_members。不重复查询，不改变数据。'
                    update['messages'] += [{'role':'user','content':'系统输出校验失败：'+code+'。'+repair+'不输出代码围栏。'}]
                else:update['result']={'status':'incomplete','summary':code}

        return update
    def after_model(self,state):
        return END if state.get('result') else ('tools' if state['messages'][-1].get('tool_calls') else 'model')
    def tool_node(self,state):
        messages=list(state['messages']);observations=list(state['observations']);draft=None
        tools={t['function']['name']:t['function']['parameters'] for t in self.schemas(state['action_mode'])}
        calls=messages[-1].get('tool_calls',[])
        for i,call in enumerate(calls):
            name=call.get('function',{}).get('name','');result={}
            with self.tracer.start_as_current_span('tool.'+name) as span:
                span.set_attribute('tool.name',name)
                try:
                    if i>=8:raise BusinessError('TOO_MANY_TOOL_CALLS')
                    if name not in tools:raise ActionError('TOOL_FORBIDDEN')
                    args=json.loads(call['function']['arguments']);validate(args,tools[name])
                    if name=='describe_business':result=self.business.describe()
                    elif name=='compare_spending':
                        requested=[[args['baseline_start'],args['baseline_end']],[args['current_start'],args['current_end']]]
                        if requested!=state.get('confirmed_windows',[]):raise BusinessError('TIME_RANGE_NOT_CONFIRMED')
                        result=self.business.compare(**args)
                    elif name=='member_history':result=self.business.history(**args)
                    elif name=='prepare_tag':
                        if draft:raise ActionError('ONE_DRAFT_PER_STEP')
                        draft=self.ledger.draft(state['session'],**args);result={'status':'awaiting_approval','draft':draft}
                    elif name=='query_tag':
                        d=self.ledger.get(args['action_id'])
                        if d['session']!=state['session']:raise ActionError('FORBIDDEN')
                        result=self.ledger.query(args['action_id'])
                except (KeyError,ValueError,ValidationError):result={'status':'error','code':'INVALID_ARGUMENTS'}
                except (BusinessError,ActionError) as e:result={'status':'error','code':str(e)}
                span.set_attribute('result.status',result.get('status','ok'))
                if result.get('evidence_id'):span.set_attribute('evidence.id',result['evidence_id'])
            observations.append({'name':name,'result':result,'call_id':call.get('id','invalid')})
            messages.append({'role':'tool','tool_call_id':call.get('id','invalid'),'content':json.dumps(result,ensure_ascii=False)})
        return {'messages':messages,'observations':observations,'draft':draft}
    def approval_node(self,state):
        if self.role!='operator':raise ActionError('FORBIDDEN')
        d=state['draft'];approval=interrupt({'kind':'tag_confirmation','draft_id':d['id'],'draft_hash':d['hash'],'payload':d['payload'],'message':'确认后会真实调用本地样例标签 API。'})
        if not isinstance(approval,dict) or not isinstance(approval.get('approved'),bool):raise ActionError('INVALID_CONFIRMATION')
        # Role comes from the host configuration, never from the model or request body.
        current=self.ledger.get(d['id'])
        if current['status']=='draft':current=self.ledger.approve(d['id'],state['session'],approval.get('draft_hash',''),approval['approved'],self.role)
        elif current['hash']!=approval.get('draft_hash') or current['status'] not in ('approved','applied','unknown','executing','rejected'):raise ActionError('APPROVAL_MISMATCH')
        if current['status']=='rejected':
            result={'status':'rejected','summary':'已拒绝这份标签草案，未执行本次添加操作。你可以继续提出新的要求。','evidence_ids':[current['id']]}
            return {'draft':None,'result':result,'observations':state['observations']+[{'name':'tag_rejection','result':current}],
                    'messages':state['messages']+[{'role':'assistant','content':json.dumps(result,ensure_ascii=False)}]}
        return {'draft':current,'messages':state['messages']+[{'role':'user','content':'系统记录的确认结果：'+current['status']}]}
    def execute_node(self,state):
        with self.tracer.start_as_current_span('action.execute') as span:
            result=self.ledger.execute(state['draft']['id']);span.set_attribute('action.status',result['status']);span.set_attribute('action.id',result['id'])
        return {'draft':None,'observations':state['observations']+[{'name':'tag_execution','result':result}], 'messages':state['messages']+[{'role':'user','content':'系统核验的标签执行结果：'+json.dumps(result,ensure_ascii=False)}]}
    def run(self,text=None,session=None,action_mode=False,resume=None,time_choice=None):
        session=session or str(uuid.uuid4())
        if not isinstance(session,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',session):raise ActionError('INVALID_SESSION_ID')
        config={'configurable':{'thread_id':session},'recursion_limit':40}
        with self.lock,tracing_context(enabled=False),self.tracer.start_as_current_span('agent.run') as span:
            span.set_attribute('session.id',session);span.set_attribute('product.version',VERSION);span.set_attribute('prompt.sha256',self.prompt_hash);span.set_attribute('model.mode',self.model.mode)
            current=self.graph.get_state(config)
            if resume is not None:
                if not current.next:raise ActionError('NO_PENDING_DRAFT')
                input=Command(resume=resume)
            else:
                # A paused graph without a draft means the previous turn stopped midway, not that approval is pending.
                if current.next:raise ActionError('CONFIRM_OR_CANCEL_PENDING_DRAFT' if current.values.get('draft') else 'SESSION_INTERRUPTED')
                previous=current.values.get('messages',[])
                if len(previous)>100:raise ActionError('SESSION_CONTEXT_LIMIT')
                scope=explicit_windows(text or '')
                options=current.values.get('time_options',[])
                choice=None
                if time_choice is not None:
                    if not isinstance(time_choice,dict) or not options or time_choice.get('token')!=current.values.get('time_options_token'):raise ActionError('TIME_OPTION_EXPIRED')
                    choice=next((o for o in options if o['id']==time_choice.get('id')),None)
                    if not choice:raise ActionError('TIME_OPTION_EXPIRED')
                elif not scope:choice=selected_option(text or '',options)
                if choice:
                    scope=choice['windows']
                    text=(text or '')+'\n用户确认日期方案：'+choice['label']
                elif time_choice is not None:raise ActionError('TIME_OPTION_EXPIRED')
                if not scope and current.values.get('confirmed_windows'):
                    # Only carry a scope when the follow-up introduces no new date terms.
                    if not re.search(r'年|月|季度|近期|最近|\d{4}-\d{2}|week|month|recent',text or '',re.I):scope=current.values['confirmed_windows']
                input={'confirmed_windows':scope,'time_options':[],'time_options_token':'','messages':previous+[{'role':'user','content':text or ''}],'steps':0,'repairs':0,'usage':{},'observations':[],'draft':None,'result':{},'action_mode':bool(action_mode and self.role=='operator'),'session':session}
            if hasattr(self.model,"deadline"):self.model.deadline=time.monotonic()+600
            out=self.graph.invoke(input,config)
            pauses=out.get('__interrupt__',())
            safe={'product_version':VERSION,'runtime_revision':RUNTIME_REVISION,'session_id':session,'model_mode':self.model.mode,'model_name':getattr(self.model,'model','scripted'),'prompt_sha256':self.prompt_hash,'status':'awaiting_approval' if pauses else out.get('result',{}).get('status','incomplete'),'result':out.get('result',{}),'observations':out.get('observations',[]),'steps':out.get('steps',0),'output_repairs':out.get('repairs',0),'usage':out.get('usage',{}),'trace_id':format(span.get_span_context().trace_id,'032x')}
            if pauses:safe['confirmation']=pauses[0].value
            if out.get('time_options') and not pauses:
                safe['time_options']=out['time_options'];safe['time_options_token']=out['time_options_token']
            (self.home/'runs').mkdir(exist_ok=True);(self.home/'runs'/(session+'-'+uuid.uuid4().hex[:8]+'.json')).write_text(json.dumps(safe,ensure_ascii=False,indent=2))
            span.set_attribute('result.status',safe['status']);return safe
