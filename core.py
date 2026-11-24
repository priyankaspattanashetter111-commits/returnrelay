"""Deterministic domain rules shared by the host and isolated MCP services."""
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
FIXTURE = json.loads((ROOT / 'data/fixtures.json').read_text())
TZ = ZoneInfo('Asia/Kolkata')

class Connection(sqlite3.Connection):
    def __exit__(self,*args):
        try: return super().__exit__(*args)
        finally: self.close()

def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'))

def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()

def db():
    path = Path(os.environ.get('RR_DB', str(ROOT / 'data/demo.db')))
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=20, factory=Connection)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, body TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY, plan_id TEXT UNIQUE, body TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY, event TEXT, body TEXT, created_at TEXT);
    ''')
    return c

def audit(event, body):
    with db() as c:
        c.execute('INSERT INTO audit(event,body,created_at) VALUES(?,?,?)',
                  (event, canonical(body), datetime.now(TZ).isoformat()))

def order_by_id(order_id, user_id=None):
    if user_id:
        from storage import purchase
        return purchase(user_id,order_id)
    for o in FIXTURE['orders']:
        if o['id'] == order_id:
            return dict(o)
    raise ValueError('Unknown order')

def check_policy(order, policy, condition):
    now = datetime.fromisoformat(FIXTURE['as_of']) if order.get('demo', True) else datetime.now(TZ)
    def result(status, reason, deadline=None, missing=None):
        return {'eligible':status=='ELIGIBLE','status':status,'reason':reason,'explanation':reason,
                'deadline':deadline,'policy':policy,'policy_version':policy.get('version'),
                'supporting_evidence':[order.get('receipt_id'),policy.get('source_id')],
                'missing_information':missing or [],'next_step':'Review and prepare a plan' if status=='ELIGIBLE' else 'Obtain missing evidence or merchant confirmation; no refund action authorized'}
    if not order.get('confirmed',True) or not order.get('receipt_id'):
        return result('INSUFFICIENT_EVIDENCE','Confirmed receipt evidence is required',missing=['confirmed receipt'])
    if not order.get('delivered'):
        return result('INSUFFICIENT_EVIDENCE','Delivery date is required by this policy',missing=['delivery date'])
    if condition != 'unused_with_packaging':
        return result('HUMAN_REVIEW_REQUIRED','Condition needs merchant review. No return action created.')
    if policy['type'] != 'return' or policy['days'] is None:
        return result('REPLACEMENT_ONLY' if policy['type']=='replacement' else 'HUMAN_REVIEW_REQUIRED','Replacement only' if policy['type']=='replacement' else 'Policy unclear: ask merchant to confirm')
    if policy.get('exceptions') or policy.get('conflicting_terms'):
        return result('HUMAN_REVIEW_REQUIRED','Policy exceptions or conflicting terms require merchant review')
    if order.get('category') in policy.get('excluded_categories',[]):
        return result('NOT_ELIGIBLE','Product category is excluded by this policy')
    end = datetime.fromisoformat(order['delivered']).replace(tzinfo=TZ, hour=23, minute=59) + timedelta(days=policy['days'])
    return result('ELIGIBLE' if now <= end else 'EXPIRED','Eligible to prepare a request, subject to merchant approval' if now <= end else 'Return window expired',end.isoformat())

def choose_slot(busy, deadline, as_of=None):
    start = datetime.fromisoformat(as_of or FIXTURE['as_of']).replace(second=0,microsecond=0)
    if as_of is None: start=start.replace(hour=17,minute=0)
    for _ in range(48):
        end = start + timedelta(minutes=30)
        if end > datetime.fromisoformat(deadline):
            raise ValueError('No free slot before deadline. Manual scheduling required.')
        if all(end <= datetime.fromisoformat(b['start']) or start >= datetime.fromisoformat(b['end']) for b in busy):
            return {'start':start.isoformat(),'end':end.isoformat()}
        start += timedelta(minutes=30)
    raise ValueError('No free slot in search horizon')

def make_plan(order, policy, busy, condition, user_id=None, reason='Product unsuitable'):
    decision = check_policy(order,policy,condition)
    if not decision['eligible']:
        return {'status':'blocked','order':order,'decision':decision,'policy':policy}
    slot = choose_slot(busy,decision['deadline'],None if order.get('demo',True) else datetime.now(TZ).isoformat())
    p = {'order_id':order['id'],'item':order['item'],'amount_inr':order['amount_inr'],
         'policy':policy,'receipt_id':order['receipt_id'],'receipt':order['receipt'],
         'condition':condition,'decision':decision,'slot':slot,'support':order['support'],
         'effects':['Create a local return email draft','Create a local calendar reminder'],
         'subject':f"Return request for {order['id']}",
         'message':f"Hello, I would like to request a return for {order['item']}, order {order['id']}. The item is unused with original packaging and accessories. Please confirm eligibility, return instructions, and refund timing. Receipt amount: INR {order['amount_inr']}."}
    if user_id:
        p.update(user_id=user_id,return_reason=reason,currency=order.get('currency','INR'),purchase_hash=digest(order))
        p['preview_nonce']=os.urandom(12).hex()
        p['message']=f"Hello, I would like to request a return for {order['item']}, order {order['id']}. Reason: {reason}. I declare the item unused with original packaging and accessories. Please confirm eligibility, instructions and refund timing. Receipt amount: {order.get('currency','INR')} {order['amount_inr']}."
    p['id'] = digest(p)[:20]
    p['hash'] = digest(p)
    p['expires_at'] = int(time.time()) + 1800
    with db() as c:
        c.execute('INSERT OR REPLACE INTO plans VALUES(?,?)',(p['id'],canonical(p)))
    audit('plan_previewed', {'plan_id':p['id'],'hash':p['hash']})
    return {'status':'preview','plan':p}

def load_plan(plan_id):
    with db() as c:
        row = c.execute('SELECT body FROM plans WHERE id=?',(plan_id,)).fetchone()
    if not row: raise ValueError('Plan not found')
    return json.loads(row['body'])

def issue_approval(plan):
    """Host-only action. Never exposed as an MCP tool."""
    payload = canonical({'plan_id':plan['id'],'hash':digest(plan),'exp':min(int(time.time())+300,plan['expires_at'])})
    token = base64.urlsafe_b64encode(payload.encode()).decode()
    key = os.environ['RR_APPROVAL_KEY'].encode()
    return token + '.' + hmac.new(key,token.encode(),hashlib.sha256).hexdigest()

def verify_approval(plan,token):
    try:
        body,sig = token.split('.')
        expected = hmac.new(os.environ['RR_APPROVAL_KEY'].encode(),body.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig,expected): raise ValueError()
        p = json.loads(base64.urlsafe_b64decode(body))
        if p['plan_id']!=plan['id'] or p['hash']!=digest(plan) or p['exp']<=time.time(): raise ValueError()
    except Exception:
        raise ValueError('Missing, changed or expired approval. Preview and approve again.') from None

def execute(plan_id, approval_token, fail_calendar=False):
    p = load_plan(plan_id)
    verify_approval(p,approval_token)
    order=order_by_id(p['order_id'],p.get('user_id'))
    if p.get('purchase_hash') and p['purchase_hash']!=digest(order): raise ValueError('Purchase evidence changed. New preview required.')
    if p.get('user_id'):
        from storage import policy
        current=policy(p['user_id'],order['policy_id'])
    else: current = FIXTURE['policies'][order['policy_id']]
    if digest(current)!=digest(p['policy']): raise ValueError('Policy changed. New preview required.')
    if time.time()>p['expires_at']: raise ValueError('Plan expired')
    if not check_policy(order,current,p['condition'])['eligible']: raise ValueError('Eligibility changed')
    # Local outputs are staged together in one SQLite transaction. No partial effects.
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if p.get('user_id'):
            request=c.execute('SELECT status FROM return_requests WHERE user_id=? AND plan_id=?',(p['user_id'],plan_id)).fetchone()
            if not request or request['status']=='cancelled': raise ValueError('Return plan is cancelled or unavailable')
        row=c.execute('SELECT body FROM actions WHERE plan_id=?',(plan_id,)).fetchone()
        if row: return {**json.loads(row['body']),'replayed':True}
        if fail_calendar: raise ValueError('Simulated calendar failure. No outputs committed. Retry safely.')
        from adapters import local_outputs
        result={'id':'ACT-'+plan_id,'plan_id':plan_id,'status':'prepared','replayed':False,
                **local_outputs.prepare(p),
                'refund_status':'awaiting_merchant_confirmation',
                'note':'Local draft and reminder only. Nothing sent, booked, charged or refunded.'}
        c.execute('INSERT INTO actions VALUES(?,?,?)',(result['id'],plan_id,canonical(result)))
        c.execute('INSERT INTO audit(event,body,created_at) VALUES(?,?,?)',('approved_outputs_prepared',canonical({'plan_id':plan_id}),datetime.now(TZ).isoformat()))
    return result

def outputs():
    with db() as c:
        return [json.loads(r['body']) for r in c.execute('SELECT body FROM actions')]

def ledger():
    with db() as c:
        return [dict(r) for r in c.execute('SELECT * FROM audit ORDER BY seq')]
