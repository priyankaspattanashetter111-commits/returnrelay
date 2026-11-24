"""Additive SQLite migration and account-scoped repositories."""
import json
import secrets
from datetime import datetime, timezone
from core import db, canonical, FIXTURE

def now():
    return datetime.now(timezone.utc).isoformat()

def migrate():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT);
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, password TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), csrf TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS purchases(user_id TEXT NOT NULL REFERENCES users(id), id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(user_id,id));
        CREATE TABLE IF NOT EXISTS merchant_policies(user_id TEXT NOT NULL REFERENCES users(id), id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(user_id,id));
        CREATE TABLE IF NOT EXISTS evidence_records(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), order_id TEXT, kind TEXT NOT NULL, source TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0, body TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY REFERENCES evidence_records(id), path TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS return_requests(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), order_id TEXT NOT NULL, status TEXT NOT NULL, plan_id TEXT, body TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS return_history(id INTEGER PRIMARY KEY, request_id TEXT NOT NULL REFERENCES return_requests(id), status TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS refund_records(user_id TEXT NOT NULL REFERENCES users(id), order_id TEXT NOT NULL, body TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(user_id,order_id));
        CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), plan_id TEXT NOT NULL, approved INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS calendar_slots(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), event TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS mcp_events(id INTEGER PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), body TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS evidence_owner ON evidence_records(user_id,order_id);
        CREATE INDEX IF NOT EXISTS requests_owner ON return_requests(user_id,updated_at);
        CREATE INDEX IF NOT EXISTS audit_owner ON audit_logs(user_id,id);
        INSERT OR IGNORE INTO schema_migrations VALUES(1,CURRENT_TIMESTAMP);
        ''')

def log(uid, event, body):
    with db() as c:
        c.execute('INSERT INTO audit_logs(user_id,event,body,created_at) VALUES(?,?,?,?)',(uid,event,canonical(body),now()))

def purchases(uid):
    with db() as c:
        return [json.loads(r['body']) for r in c.execute('SELECT body FROM purchases WHERE user_id=?',(uid,))]

def purchase(uid, oid):
    with db() as c:
        r=c.execute('SELECT body FROM purchases WHERE user_id=? AND id=?',(uid,oid)).fetchone()
    if not r: raise ValueError('Purchase not found')
    return json.loads(r['body'])

def policy(uid, pid):
    with db() as c:
        r=c.execute('SELECT body FROM merchant_policies WHERE user_id=? AND id=?',(uid,pid)).fetchone()
    return json.loads(r['body']) if r else {'type':'unknown','days':None,'version':1,'source_id':'unconfirmed','text':'No confirmed merchant policy provided.'}

def evidence(uid, oid=None):
    with db() as c:
        rows=c.execute('SELECT * FROM evidence_records WHERE user_id=?'+(' AND order_id=?' if oid else '')+' ORDER BY created_at DESC', (uid,oid) if oid else (uid,)).fetchall()
    return [{**dict(r),'body':json.loads(r['body'])} for r in rows]

def add_evidence(uid, oid, kind, source, body, verified=False, eid=None):
    eid=eid or secrets.token_hex(12)
    with db() as c:
        c.execute('INSERT INTO evidence_records VALUES(?,?,?,?,?,?,?,?)',(eid,uid,oid,kind,source,int(verified),canonical(body),now()))
    return eid

def seed_demo(uid):
    with db() as c:
        for pid,p in FIXTURE['policies'].items():
            c.execute('INSERT OR IGNORE INTO merchant_policies VALUES(?,?,?)',(uid,pid,canonical(p)))
            c.execute('INSERT OR IGNORE INTO evidence_records VALUES(?,?,?,?,?,?,?,?)',(uid+'-'+pid,uid,None,'policy','Fictional demonstration',1,canonical(p),now()))
        for o in FIXTURE['orders']:
            body={**o,'purchase_date':None,'currency':'INR','condition':'unused_with_packaging','demo':True,'confirmed':True,'category':'unspecified'}
            c.execute('INSERT OR IGNORE INTO purchases VALUES(?,?,?)',(uid,o['id'],canonical(body)))
            eid=uid+'-'+o['receipt_id']
            c.execute('INSERT OR IGNORE INTO evidence_records VALUES(?,?,?,?,?,?,?,?)',(eid,uid,o['id'],'receipt','Fictional demonstration',1,canonical({'text':o['receipt']}),now()))
        for b in FIXTURE['busy']:
            c.execute('INSERT OR IGNORE INTO calendar_slots VALUES(?,?,?)',(uid+'-demo-busy',uid,canonical(b)))
        for m in FIXTURE['refund_messages']:
            record={'messages':[m],'status':'merchant_says_processed','bank_credit_verified':False,'updated_at':now(),'demo':True}
            c.execute('INSERT OR IGNORE INTO refund_records VALUES(?,?,?,?)',(uid,m['order_id'],canonical(record),now()))
    log(uid,'demo_loaded',{'fictional':True})

def requests(uid):
    with db() as c:
        rows=c.execute('SELECT * FROM return_requests WHERE user_id=? ORDER BY updated_at DESC',(uid,)).fetchall()
        result=[]
        for r in rows:
            v={**dict(r),'body':json.loads(r['body'])}
            v['history']=[dict(h) for h in c.execute('SELECT status,note,created_at FROM return_history WHERE request_id=? ORDER BY id',(r['id'],))]
            result.append(v)
    return result

def transition(uid, rid, status, note):
    with db() as c:
        if not c.execute('SELECT 1 FROM return_requests WHERE id=? AND user_id=?',(rid,uid)).fetchone(): raise ValueError('Return not found')
        c.execute('UPDATE return_requests SET status=?,updated_at=? WHERE id=?',(status,now(),rid))
        c.execute('INSERT INTO return_history(request_id,status,note,created_at) VALUES(?,?,?,?)',(rid,status,note,now()))
    log(uid,'return_'+status,{'request_id':rid,'note':note})
