"""Durable action ledger and real HTTP connector. No analytical-table writes."""
import hashlib,json,sqlite3,uuid
from urllib import request,error
from urllib.parse import urlsplit
from pathlib import Path
from .models import NoRedirect

class ActionError(Exception):
    def __init__(self,code):self.code=code;super().__init__(code)

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class TagConnector:
    def __init__(self,url,token,timeout=5):
        p=urlsplit(url)
        if p.scheme!='http' or p.hostname not in ('127.0.0.1','localhost') or p.query or p.fragment or p.username:raise ActionError('MOCK_URL_INVALID')
        self.url=url.rstrip('/');self.token=token;self.timeout=timeout
    def call(self,method,path,body=None):
        req=request.Request(self.url+path,data=None if body is None else json.dumps(body).encode(),method=method,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        try:
            with request.build_opener(NoRedirect).open(req,timeout=self.timeout) as r:return json.loads(r.read(200001))
        except error.HTTPError as e:
            status=e.code;e.close();raise ActionError('API_HTTP_'+str(status)) from None
        except (error.URLError,TimeoutError,ValueError):raise ActionError('API_RESULT_UNKNOWN') from None
    def query(self,action_id):return self.call('GET','/v1/tag-actions/'+action_id)
    def apply(self,action_id,payload):return self.call('PUT','/v1/tag-actions/'+action_id,payload)
    def undo(self,action_id):return self.call('POST','/v1/tag-actions/'+action_id+'/undo',{})

class Ledger:
    def __init__(self,path,connector):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.connector=connector
        with self.connect() as c:c.execute('CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,session TEXT,payload TEXT,hash TEXT,status TEXT,result TEXT)')
    def connect(self):
        c=sqlite3.connect(self.path,timeout=15);c.row_factory=sqlite3.Row;return c
    def get(self,id):
        with self.connect() as c:r=c.execute('SELECT * FROM drafts WHERE id=?',(id,)).fetchone()
        if not r:raise ActionError('DRAFT_NOT_FOUND')
        d=dict(r);d['payload']=json.loads(d['payload']);d['result']=json.loads(d['result']) if d['result'] else None;return d
    def draft(self,session,member_id,tag):
        if tag not in ['recall_review','follow_up'] or member_id not in [f'M{i:03}' for i in range(1,10)]:raise ActionError('NOT_ALLOWLISTED')
        payload={'member_id':member_id,'tag':tag};id=str(uuid.uuid4());h=digest({'id':id,'payload':payload})
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE drafts SET status='superseded' WHERE session=? AND status IN ('draft','approved')",(session,))
            c.execute('INSERT INTO drafts VALUES (?,?,?,?,?,NULL)',(id,session,json.dumps(payload),h,'draft'))
        return self.get(id)
    def approve(self,id,session,expected_hash,approved,role):
        if role!='operator':raise ActionError('FORBIDDEN')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM drafts WHERE id=?',(id,)).fetchone()
            if not r or r['session']!=session or r['hash']!=expected_hash:raise ActionError('APPROVAL_MISMATCH')
            if r['status']!='draft':raise ActionError('DRAFT_NOT_PENDING')
            c.execute('UPDATE drafts SET status=? WHERE id=?',('approved' if approved else 'rejected',id))
        return self.get(id)
    def save_result(self,id,status,result):
        with self.connect() as c:c.execute('UPDATE drafts SET status=?,result=? WHERE id=?',(status,json.dumps(result),id))
    def execute(self,id):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM drafts WHERE id=?',(id,)).fetchone()
            if not r:raise ActionError('DRAFT_NOT_FOUND')
            if r['status'] in ('applied','undone'):return self.get(id)
            if r['status'] in ('executing','unknown'):recover=True
            elif r['status']=='approved':recover=False;c.execute("UPDATE drafts SET status='executing' WHERE id=?",(id,))
            else:raise ActionError('NOT_APPROVED')
        try:result=self.connector.query(id) if recover else self.connector.apply(id,json.loads(r['payload']))
        except ActionError as e:
            # A 404 on recovery is not proof a timed-out in-flight mutation cannot commit.
            self.save_result(id,'unknown',{'code':e.code});return self.get(id)
        self.save_result(id,result['status'],result);return self.get(id)
    def query(self,id):
        d=self.get(id)
        if d['status'] in ('draft','approved','rejected','superseded'):return d
        result=self.connector.query(id);self.save_result(id,result['status'],result);return self.get(id)
    def undo(self,id,session,role):
        d=self.get(id)
        if role!='operator' or d['session']!=session:raise ActionError('FORBIDDEN')
        if d['status'] not in ('applied','undone'):raise ActionError('NOT_REVERSIBLE')
        result=self.connector.undo(id);self.save_result(id,result['status'],result);return self.get(id)
