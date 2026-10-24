"""Executable sample semantics. The model never supplies SQL or totals."""
from pathlib import Path
import csv, hashlib, json, sqlite3, re
from datetime import date

ROOT = Path(__file__).resolve().parents[2]
VERSION = '0.2.0-w1'
CONTRACT = 'w01-0.2.0'
RUNTIME_REVISION = 'w01-runtime-20260922'

class BusinessError(Exception):
    pass

def load_semantics(path=None):
    value=json.loads(Path(path or ROOT/'product/semantics.json').read_text())
    if value['spend']['aggregation']!='paid_minus_refunded_cents' or value['old_member']['basis']!='joined_before_baseline':
        raise BusinessError('UNSUPPORTED_METRIC_DEFINITION')
    return value

def import_sample(folder, database):
    folder=Path(folder); database=Path(database); database.parent.mkdir(parents=True,exist_ok=True)
    meta=json.loads((folder/'manifest.json').read_text())
    rows={name:list(csv.DictReader((folder/(name+'.csv')).open())) for name in ['stores','members','orders','coverage']}
    with sqlite3.connect(database) as c:
        c.execute('PRAGMA foreign_keys=ON')
        c.executescript('''CREATE TABLE IF NOT EXISTS stores(store_id TEXT PRIMARY KEY,name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS members(member_id TEXT PRIMARY KEY,name TEXT NOT NULL,joined_at TEXT NOT NULL,store_id TEXT REFERENCES stores);
        CREATE TABLE IF NOT EXISTS orders(order_id TEXT PRIMARY KEY,member_id TEXT REFERENCES members,paid_at TEXT,paid_cents INTEGER CHECK(paid_cents>=0),refunded_cents INTEGER CHECK(refunded_cents>=0 AND refunded_cents<=paid_cents),status TEXT CHECK(status IN ('paid','cancelled')));
        CREATE TABLE IF NOT EXISTS coverage(store_id TEXT REFERENCES stores,start TEXT,end TEXT,PRIMARY KEY(store_id,start,end));
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
        # One transaction: repeated imports replace the same dataset, never duplicate it.
        for name in ['orders','members','coverage','stores','metadata']:c.execute('DELETE FROM '+name)
        c.executemany('INSERT INTO stores VALUES (:store_id,:name)',rows['stores'])
        c.executemany('INSERT INTO members VALUES (:member_id,:name,:joined_at,:store_id)',rows['members'])
        c.executemany('INSERT INTO orders VALUES (:order_id,:member_id,:paid_at,:paid_cents,:refunded_cents,:status)',rows['orders'])
        c.executemany('INSERT INTO coverage VALUES (:store_id,:start,:end)',rows['coverage'])
        c.executemany('INSERT INTO metadata VALUES (?,?)',[(k,json.dumps(v,ensure_ascii=False)) for k,v in meta.items()])
    return {name:len(value) for name,value in rows.items()}

def explicit_windows(text):
    """Recognize user-supplied calendar months or four ISO boundaries, never 'recent'.

    This is a deterministic confirmation boundary, not an intent classifier.
    Unsupported natural-language periods must be clarified into explicit dates.
    """
    iso=re.findall(r'(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)',text or '')
    if len(iso)==4:
        try:
            for x in iso:date.fromisoformat(x)
        except ValueError:return []
        # ISO interval ends are explicitly exclusive in the UI/API contract.
        if re.search(r'不含|不包含|左闭右开|end.exclusive|exclusive|\)',text,re.I):
            return [[iso[0],iso[1]],[iso[2],iso[3]]]
        return []
    tokens=re.findall(r'(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月',text or '')
    if len(tokens)!=2:return []
    years=[int(y) for y,m in tokens if y]
    if not years:return []
    result=[];year=years[0]
    for y,m in tokens:
        year=int(y) if y else year;month=int(m)
        try:
            start=date(year,month,1);end=date(year+(month==12),1 if month==12 else month+1,1)
        except ValueError:return []
        result.append([start.isoformat(),end.isoformat()])
    return sorted(result) if len({tuple(x) for x in result})==2 else []

class Business:
    def __init__(self,database,semantics=None):
        self.path=Path(database).resolve(); self.semantics=load_semantics(semantics)
    def connect(self):
        c=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
        c.execute('PRAGMA query_only=ON'); return c
    def describe(self):
        with self.connect() as c:
            meta={r['key']:json.loads(r['value']) for r in c.execute('SELECT * FROM metadata')}
            cov=[dict(r) for r in c.execute('SELECT * FROM coverage')]
        return {'status':'ok','semantics':self.semantics,'dataset':meta,'coverage':cov,'required':'必须由用户明确或确认两个时段，不能擅自用默认日期替代澄清。'}
    def compare(self,baseline_start,baseline_end,current_start,current_end,store_id=None,min_decline_pct=None):
        dates=[baseline_start,baseline_end,current_start,current_end]
        try:
            for x in dates:
                if date.fromisoformat(x).isoformat()!=x:raise ValueError()
        except (ValueError,TypeError):raise BusinessError('INVALID_DATE') from None
        if not baseline_start<baseline_end<=current_start<current_end:raise BusinessError('INVALID_WINDOWS')
        cutoff=self.semantics['decline']['minimum_pct'] if min_decline_pct is None else min_decline_pct
        if not 0<=cutoff<=100:raise BusinessError('INVALID_THRESHOLD')
        with self.connect() as c:
            stores=[r[0] for r in c.execute('SELECT store_id FROM stores WHERE (? IS NULL OR store_id=?)',(store_id,store_id))]
            if not stores:raise BusinessError('UNKNOWN_STORE')
            missing=[]; valid=[]
            for sid in stores:
                covered=all(c.execute('SELECT 1 FROM coverage WHERE store_id=? AND start<=? AND end>=?',(sid,a,b)).fetchone() for a,b in [(baseline_start,baseline_end),(current_start,current_end)])
                (valid if covered else missing).append(sid)
            meta={r['key']:json.loads(r['value']) for r in c.execute('SELECT * FROM metadata')}
            results=[]
            for sid in valid:
                for m in c.execute('SELECT * FROM members WHERE store_id=? AND joined_at<? ORDER BY member_id',(sid,baseline_start)):
                    amounts=[];counts=[];evidence=[];breakdowns=[]
                    for a,b in [(baseline_start,baseline_end),(current_start,current_end)]:
                        orders=list(c.execute("SELECT order_id,paid_cents,refunded_cents,status FROM orders WHERE member_id=? AND paid_at>=? AND paid_at<? ORDER BY order_id",(m['member_id'],a,b)))
                        os=[o for o in orders if o['status']=='paid'];cancelled=[o for o in orders if o['status']=='cancelled']
                        paid=sum(o['paid_cents'] for o in os);refunded=sum(o['refunded_cents'] for o in os)
                        amounts.append(paid-refunded); counts.append(len(os));evidence.append([o['order_id'] for o in os])
                        breakdowns.append({'paid_count':len(os),'fully_refunded_count':sum(o['paid_cents']>0 and o['paid_cents']==o['refunded_cents'] for o in os),'cancelled_count':len(cancelled),'paid_cents':paid,'refunded_cents':refunded,'net_cents':paid-refunded,'paid_order_ids':[o['order_id'] for o in os],'cancelled_order_ids':[o['order_id'] for o in cancelled]})
                    before,after=amounts
                    pct=round((before-after)*100/before,2) if before>0 else None
                    results.append({'member_id':m['member_id'],'store_id':sid,'baseline_cents':before,'current_cents':after,'delta_cents':after-before,'decline_pct':pct,'baseline_orders':counts[0],'current_orders':counts[1],'declining':pct is not None and pct>=cutoff and after<before,'evidence_order_ids':evidence,'order_breakdown':dict(zip(['baseline','current'],breakdowns))})
        evidence_id='CMP-'+hashlib.sha256(json.dumps([meta,dates,store_id,cutoff,results],sort_keys=True).encode()).hexdigest()[:16]
        return {'status':'partial' if missing else 'ok','evidence_id':evidence_id,'dataset_version':meta['dataset_version'],'metric_id':self.semantics['spend']['id'],'windows':{'baseline':[baseline_start,baseline_end],'current':[current_start,current_end],'end_exclusive':True},'minimum_decline_pct':cutoff,'rows':results,'declining_members':[r['member_id'] for r in results if r['declining']],'missing_coverage_stores':missing,'limitations':['未覆盖门店的消费未知，不能计为零。'] if missing else [],'business_reference_date':meta['business_reference_date']}
    def history(self,member_id):
        with self.connect() as c:
            m=c.execute('SELECT member_id,store_id,joined_at FROM members WHERE member_id=?',(member_id,)).fetchone()
            if not m:raise BusinessError('UNKNOWN_MEMBER')
            return {'status':'ok','member':dict(m),'orders':[dict(r) for r in c.execute('SELECT order_id,paid_at,paid_cents,refunded_cents,status FROM orders WHERE member_id=? ORDER BY paid_at',(member_id,))]}
