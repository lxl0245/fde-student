"""Independent persistent HTTP sample business system; localhost only."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import hmac,json,re,sqlite3
from jsonschema import validate,ValidationError

PAYLOAD={'type':'object','required':['member_id','tag'],'additionalProperties':False,'properties':{'member_id':{'enum':[f'M{i:03}' for i in range(1,10)]},'tag':{'enum':['recall_review','follow_up']}}}
class MockStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c:c.executescript('''CREATE TABLE IF NOT EXISTS labels(member_id TEXT,tag TEXT,active INTEGER,version INTEGER,PRIMARY KEY(member_id,tag));CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,payload TEXT,status TEXT,previous_active INTEGER,version INTEGER);''')
    def connect(self):
        c=sqlite3.connect(self.path,timeout=15);c.row_factory=sqlite3.Row;return c
    def query(self,id):
        with self.connect() as c:
            r=c.execute('SELECT * FROM actions WHERE id=?',(id,)).fetchone()
            if not r:return 404,{'code':'NOT_FOUND'}
            p=json.loads(r['payload']);l=c.execute('SELECT active,version FROM labels WHERE member_id=? AND tag=?',(p['member_id'],p['tag'])).fetchone()
            return 200,{'action_id':id,'status':r['status'],'payload':p,'label_active':bool(l['active']),'label_version':l['version']}
    def mutate(self,id,payload=None,undo=False):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');a=c.execute('SELECT * FROM actions WHERE id=?',(id,)).fetchone()
            if undo:
                if not a:return 404,{'code':'NOT_FOUND'}
                if a['status']=='undone':pass
                else:
                    p=json.loads(a['payload']);l=c.execute('SELECT * FROM labels WHERE member_id=? AND tag=?',(p['member_id'],p['tag'])).fetchone()
                    if l['version']!=a['version']:return 409,{'code':'LATER_CHANGE_EXISTS'}
                    c.execute('UPDATE labels SET active=?,version=version+1 WHERE member_id=? AND tag=?',(a['previous_active'],p['member_id'],p['tag']))
                    c.execute("UPDATE actions SET status='undone' WHERE id=?",(id,))
            elif a:
                if json.loads(a['payload'])!=payload:return 409,{'code':'IDEMPOTENCY_CONFLICT'}
            else:
                l=c.execute('SELECT * FROM labels WHERE member_id=? AND tag=?',(payload['member_id'],payload['tag'])).fetchone()
                prev=l['active'] if l else 0;v=(l['version'] if l else 0)+1
                c.execute('INSERT INTO labels VALUES (?,?,1,?) ON CONFLICT(member_id,tag) DO UPDATE SET active=1,version=?',(payload['member_id'],payload['tag'],v,v))
                c.execute("INSERT INTO actions VALUES (?,?,'applied',?,?)",(id,json.dumps(payload,sort_keys=True),prev,v))
        return self.query(id)

def make_server(path,port=8766,token='training-demo-token'):
    store=MockStore(path)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def respond(self,code,value):
            data=json.dumps(value,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_GET(self):self.handle_request()
        def do_PUT(self):self.handle_request()
        def do_POST(self):self.handle_request()
        def handle_request(self):
            auth=self.headers.get('Authorization','');writer=hmac.compare_digest(auth,'Bearer '+token);reader=hmac.compare_digest(auth,'Bearer '+token+'-reader')
            if not writer and not reader:return self.respond(401,{'code':'UNAUTHORIZED'})
            if self.command!='GET' and not writer:return self.respond(403,{'code':'FORBIDDEN'})
            if self.path=='/health' and self.command=='GET':return self.respond(200,{'status':'ok','service':'sample-tag-api','version':'1'})
            match=re.fullmatch(r'/v1/tag-actions/([a-zA-Z0-9-]{1,80})(/undo)?',self.path)
            if not match:return self.respond(404,{'code':'NOT_FOUND'})
            id,undo=match.groups()
            if self.command=='GET' and not undo:return self.respond(*store.query(id))
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<0 or size>4096:raise ValueError()
                body=json.loads(self.rfile.read(size))
                if undo:
                    if self.command!='POST' or body!={}:raise ValueError()
                else:
                    if self.command!='PUT':raise ValueError()
                    validate(body,PAYLOAD)
            except (ValueError,ValidationError):return self.respond(400,{'code':'INVALID_REQUEST'})
            return self.respond(*store.mutate(id,body,bool(undo)))
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler);server.store=store;return server
