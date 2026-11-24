"""Authenticated application API; MCP remains the evidence/action transport."""
import asyncio
import json
import os
import re
import secrets
import time
from collections import Counter, defaultdict, deque
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ConfigDict
import storage as store
from core import ROOT, FIXTURE, db, canonical, check_policy, make_plan, load_plan, issue_approval, outputs
from security import session, password_hash, password_matches, create_session, COOKIE
from receipts import extract_file, MAX_BYTES
from assistant import answer

router=APIRouter(prefix='/api/v2')
attempts=defaultdict(deque)
def limited(key, limit=60):
    t=time.monotonic(); q=attempts[key]
    while q and q[0]<t-60: q.popleft()
    if len(q)>=limit: raise HTTPException(429,'Too many requests; try again in a minute')
    q.append(t)

def user(request:Request):
    s=session(request);limited(s['user_id']);return s

_mcp_host=None
def bind_host(value):
    global _mcp_host
    _mcp_host=value

def host():
    return _mcp_host

async def call(s,role,tool,args=None):
    try: return await host().call(role,tool,{**(args or {}),'user_id':s['user_id']})
    except ValueError as e: raise HTTPException(400,str(e)) from None

def own_plan(s,pid):
    try: p=load_plan(pid)
    except ValueError: raise HTTPException(404,'Plan not found') from None
    if p.get('user_id')!=s['user_id']: raise HTTPException(404,'Plan not found')
    return p

def own_purchase(s,oid):
    try: return store.purchase(s['user_id'],oid)
    except ValueError: raise HTTPException(404,'Purchase not found') from None

class Credentials(BaseModel):
    email:str=Field(min_length=5,max_length=254)
    password:str=Field(min_length=10,max_length=128)
    name:str=Field(default='ReturnRelay user',min_length=1,max_length=80)

def auth_guard(req):
    limited(('login',req.client.host),10)
    if req.headers.get('sec-fetch-site')=='cross-site': raise HTTPException(403,'Cross-site login blocked')

@router.post('/auth/register',status_code=201)
async def register(body:Credentials,request:Request,response:Response):
    auth_guard(request)
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',body.email): raise HTTPException(422,'Enter a valid email')
    uid=secrets.token_hex(12)
    hashed=await asyncio.to_thread(password_hash,body.password)
    import sqlite3
    try:
        with db() as c: c.execute('INSERT INTO users VALUES(?,?,?,?,?)',(uid,body.email.lower(),body.name,hashed,store.now()))
    except sqlite3.IntegrityError: raise HTTPException(409,'An account with this email already exists') from None
    token=create_session(uid,response);store.log(uid,'registered',{})
    return {'user':{'name':body.name,'email':body.email},'csrf':token}

@router.post('/auth/login')
async def login(body:Credentials,request:Request,response:Response):
    auth_guard(request)
    with db() as c: row=c.execute('SELECT * FROM users WHERE email=?',(body.email.lower(),)).fetchone()
    # Equal-cost hashing for unknown accounts limits user enumeration through timing.
    valid=await asyncio.to_thread(password_matches,body.password,row['password'] if row else '00'*16+':'+'00'*64)
    if not row or not valid: raise HTTPException(401,'Email or password is incorrect')
    return {'user':{'name':row['name'],'email':row['email']},'csrf':create_session(row['id'],response)}

@router.get('/auth/me')
def me(s=Depends(user)):
    return {'user':{'name':s['name'],'email':s['email']},'csrf':s['csrf']}

@router.post('/auth/logout')
def logout(response:Response,s=Depends(user)):
    with db() as c: c.execute('DELETE FROM sessions WHERE id=?',(s['id'],))
    response.delete_cookie(COOKIE)
    return {'ok':True}

@router.post('/demo')
def demo(s=Depends(user)):
    store.seed_demo(s['user_id']);return {'ok':True,'notice':'Fictional data loaded; scenario clock fixed at '+FIXTURE['as_of']}

@router.get('/purchases')
async def purchases(s=Depends(user)):
    orders=await call(s,'evidence','list_orders')
    for o in orders: o['decision']=check_policy(o,store.policy(s['user_id'],o['policy_id']),o.get('condition','unused_with_packaging'))
    return orders

@router.get('/purchases/{oid}')
async def details(oid:str,s=Depends(user)):
    own_purchase(s,oid)
    return await call(s,'evidence','get_evidence',{'order_id':oid})

class Evaluation(BaseModel):
    order_id:str=Field(min_length=1,max_length=40)
    condition:Literal['unused_with_packaging','opened_or_missing_parts']='unused_with_packaging'
    reason:str=Field(default='Product unsuitable',min_length=3,max_length=500)
    draft_id:str|None=Field(default=None,max_length=40)

class PurchaseCorrection(BaseModel):
    purchase_date:date|None=None
    delivery_date:date|None=None
    condition:Literal['unused_with_packaging','opened_or_missing_parts']='unused_with_packaging'
    category:str=Field(default='unspecified',max_length=80)
    confirmed:bool

@router.post('/purchases/{oid}/details')
def correct_purchase(oid:str,body:PurchaseCorrection,s=Depends(user)):
    o=own_purchase(s,oid)
    if o.get('demo'): raise HTTPException(409,'Fictional purchase evidence is read-only')
    if not body.confirmed: raise HTTPException(422,'Confirm the corrected evidence')
    if body.purchase_date and body.delivery_date and body.delivery_date<body.purchase_date: raise HTTPException(422,'Delivery cannot precede purchase')
    o.update(purchase_date=str(body.purchase_date) if body.purchase_date else None,delivered=str(body.delivery_date) if body.delivery_date else None,condition=body.condition,category=body.category)
    with db() as c: c.execute('UPDATE purchases SET body=? WHERE user_id=? AND id=?',(canonical(o),s['user_id'],oid))
    store.add_evidence(s['user_id'],oid,'verification_record','User correction',body.model_dump(mode='json'),True)
    store.log(s['user_id'],'purchase_corrected',{'order_id':oid})
    return o

class DraftInput(Evaluation):
    step:int=Field(default=0,ge=0,le=8)
    request_id:str|None=None

@router.post('/drafts')
def save_draft(body:DraftInput,s=Depends(user)):
    own_purchase(s,body.order_id)
    rid=body.request_id or secrets.token_hex(12);stamp=store.now()
    with db() as c:
        if body.request_id:
            r=c.execute('SELECT status FROM return_requests WHERE id=? AND user_id=?',(rid,s['user_id'])).fetchone()
            if not r: raise HTTPException(404,'Draft not found')
            if r['status']!='draft': raise HTTPException(409,'Return is no longer a draft')
            c.execute('UPDATE return_requests SET body=?,updated_at=? WHERE id=?',(body.model_dump_json(),stamp,rid))
        else:
            c.execute('INSERT INTO return_requests VALUES(?,?,?,?,?,?,?,?)',(rid,s['user_id'],body.order_id,'draft',None,body.model_dump_json(),stamp,stamp))
            c.execute('INSERT INTO return_history(request_id,status,note,created_at) VALUES(?,?,?,?)',(rid,'draft','Workflow saved by user',stamp))
    store.log(s['user_id'],'draft_saved',{'request_id':rid})
    return {'id':rid}

@router.post('/evaluate')
async def evaluate(body:Evaluation,s=Depends(user)):
    own_purchase(s,body.order_id)
    return await call(s,'evidence','validate_evidence',{'order_id':body.order_id,'condition':body.condition})

@router.get('/calendar')
async def calendar(s=Depends(user)):
    return await call(s,'calendar','get_busy_slots')

class Busy(BaseModel):
    start:datetime
    end:datetime
    label:str=Field(default='Busy',max_length=80)

@router.post('/calendar')
def add_busy(body:Busy,s=Depends(user)):
    if not body.start.tzinfo or not body.end.tzinfo or body.end<=body.start: raise HTTPException(422,'Provide ordered timezone-aware times')
    bid=secrets.token_hex(12)
    with db() as c: c.execute('INSERT INTO calendar_slots VALUES(?,?,?)',(bid,s['user_id'],body.model_dump_json()))
    return {'id':bid}

@router.post('/plans')
async def plan(body:Evaluation,s=Depends(user)):
    own_purchase(s,body.order_id)
    ev=await call(s,'evidence','get_evidence',{'order_id':body.order_id})
    busy=await call(s,'calendar','get_busy_slots')
    decision=await call(s,'evidence','validate_evidence',{'order_id':body.order_id,'condition':body.condition})
    if decision['eligible']:
        from core import TZ
        await call(s,'calendar','find_available_slot',{'deadline':decision['deadline'],'as_of':'' if ev['order'].get('demo') else datetime.now(TZ).isoformat()})
    try: result=make_plan(ev['order'],ev['policy'],busy,body.condition,s['user_id'],body.reason)
    except ValueError as e: raise HTTPException(409,str(e)) from None
    rid=body.draft_id or secrets.token_hex(12);stamp=store.now();pid=result.get('plan',{}).get('id')
    status='awaiting_approval' if pid else ('evidence_pending' if decision['status']=='INSUFFICIENT_EVIDENCE' else 'under_review' if decision['status']=='HUMAN_REVIEW_REQUIRED' else 'not_eligible')
    with db() as c:
        if pid:
            existing=c.execute('SELECT id FROM return_requests WHERE user_id=? AND plan_id=?',(s['user_id'],pid)).fetchone()
            if existing: return {**result,'request_id':existing['id']}
        if body.draft_id:
            draft=c.execute('SELECT * FROM return_requests WHERE id=? AND user_id=?',(rid,s['user_id'])).fetchone()
            if not draft or draft['status']!='draft' or draft['order_id']!=body.order_id: raise HTTPException(409,'Select a saved draft for this purchase')
            c.execute('UPDATE return_requests SET status=?,plan_id=?,body=?,updated_at=? WHERE id=?',(status,pid,canonical({'decision':decision,'reason':body.reason}),stamp,rid))
        else:
            c.execute('INSERT INTO return_requests VALUES(?,?,?,?,?,?,?,?)',(rid,s['user_id'],body.order_id,status,pid,canonical({'decision':decision,'reason':body.reason}),stamp,stamp))
        if pid: c.execute('INSERT INTO return_history(request_id,status,note,created_at) VALUES(?,?,?,?)',(rid,'eligible',decision['reason'],stamp))
        c.execute('INSERT INTO return_history(request_id,status,note,created_at) VALUES(?,?,?,?)',(rid,status,decision['reason'],stamp))
    store.log(s['user_id'],'return_evaluated',{'request_id':rid,'decision':decision['status']})
    if pid: store.add_evidence(s['user_id'],body.order_id,'return_plan','Deterministic MCP host',result['plan'],True)
    return {**result,'request_id':rid}

class Approval(BaseModel):
    approved:bool
    fail_calendar:bool=False

@router.post('/plans/{pid}/approve')
async def approve(pid:str,body:Approval,s=Depends(user)):
    p=own_plan(s,pid)
    with db() as c: row=c.execute('SELECT id,status FROM return_requests WHERE user_id=? AND plan_id=?',(s['user_id'],pid)).fetchone()
    if not row or row['status']=='cancelled': raise HTTPException(409,'This plan has been cancelled')
    if not body.approved:
        if row['status']!='awaiting_approval': raise HTTPException(409,'Only pending plans can be rejected')
        store.transition(s['user_id'],row['id'],'cancelled','Plan rejected by user; no action authorized')
        return {'status':'cancelled'}
    token=issue_approval(p)
    slot_check=await call(s,'calendar','validate_slot',{'start':p['slot']['start'],'end':p['slot']['end'],'deadline':p['decision']['deadline']})
    if not slot_check['valid']: raise HTTPException(409,'Preparation slot now conflicts with your calendar. Generate a fresh plan.')
    result=await call(s,'actions','prepare_approved_outputs',{'plan_id':pid,'approval_token':token,'fail_calendar':body.fail_calendar})
    with db() as c: c.execute('INSERT INTO approvals VALUES(?,?,?,?,?)',(secrets.token_hex(12),s['user_id'],pid,1,store.now()))
    if row['status']=='awaiting_approval':
        store.transition(s['user_id'],row['id'],'approved','Explicit approval verified for exact stored plan')
        store.transition(s['user_id'],row['id'],'prepared','Local EML and ICS prepared. User must submit separately.')
    store.log(s['user_id'],'outputs_prepared',{'plan_id':pid,'replayed':result['replayed']})
    return result

@router.get('/plans/{pid}')
def get_plan(pid:str,s=Depends(user)): return own_plan(s,pid)

@router.get('/returns')
def returns(s=Depends(user)): return store.requests(s['user_id'])

class UpdateReturn(BaseModel):
    status:Literal['submitted_by_user','merchant_processing','cancelled','completed']
    note:str=Field(min_length=5,max_length=2000)

@router.post('/returns/{rid}/status')
def update_return(rid:str,body:UpdateReturn,s=Depends(user)):
    record=next((r for r in store.requests(s['user_id']) if r['id']==rid),None)
    if not record: raise HTTPException(404,'Return not found')
    allowed={'prepared':{'submitted_by_user','cancelled'},'submitted_by_user':{'merchant_processing','cancelled'},'merchant_processing':{'cancelled'},'awaiting_approval':{'cancelled'},'under_review':{'cancelled'},'not_eligible':{'cancelled'},'evidence_pending':{'cancelled'},'bank_credit_verified':{'completed'}}
    if body.status not in allowed.get(record['status'],set()): raise HTTPException(409,'Invalid lifecycle transition')
    store.transition(s['user_id'],rid,body.status,body.note)
    if body.status=='merchant_processing': store.add_evidence(s['user_id'],record['order_id'],'merchant_response','User-recorded merchant response',{'text':body.note})
    return {'ok':True}

@router.get('/refunds')
async def refunds(s=Depends(user)):
    result=[]
    for o in store.purchases(s['user_id']):
        r=await call(s,'evidence','get_refund_evidence',{'order_id':o['id']})
        result.append({**r,'credited_amount':r.get('amount'),'order_id':o['id'],'product':o['item'],'merchant':o['merchant'],'amount':o['amount_inr'],'currency':o.get('currency','INR')})
    return result

class RefundUpdate(BaseModel):
    status:Literal['refund_requested','merchant_processing','merchant_confirms_processed','bank_credit_pending_verification','bank_credit_verified']
    note:str=Field(min_length=5,max_length=2000)
    evidence_id:str|None=None
    payment_reference:str|None=Field(default=None,max_length=100)
    amount:float|None=Field(default=None,gt=0)
    confirmed:bool=False

@router.post('/refunds/{oid}')
def refund_update(oid:str,body:RefundUpdate,s=Depends(user)):
    o=own_purchase(s,oid);ev=None
    if body.evidence_id:
        ev=next((e for e in store.evidence(s['user_id'],oid) if e['id']==body.evidence_id and e['kind']=='refund_document'),None)
        if not ev: raise HTTPException(422,'Select a refund document for this order')
    if body.status=='bank_credit_verified':
        if not ev or not body.confirmed or not body.payment_reference or body.amount is None: raise HTTPException(422,'Bank verification requires a refund document, transaction reference, credited amount and explicit user confirmation')
        if abs(body.amount-o['amount_inr'])>0.01: raise HTTPException(422,'Credited amount differs from purchase; partial refunds require review')
    saved={**body.model_dump(),'bank_credit_verified':body.status=='bank_credit_verified','verification_method':'user-confirmed document; no bank API','updated_at':store.now()}
    with db() as c:
        c.execute('INSERT OR REPLACE INTO refund_records VALUES(?,?,?,?)',(s['user_id'],oid,canonical(saved),store.now()))
        if saved['bank_credit_verified']: c.execute('UPDATE evidence_records SET verified=1 WHERE id=?',(ev['id'],))
    store.add_evidence(s['user_id'],oid,'verification_record' if saved['bank_credit_verified'] else 'merchant_response','User record',saved,saved['bank_credit_verified'])
    timeline_status={'merchant_confirms_processed':'refund_processed_by_merchant','bank_credit_pending_verification':'bank_credit_pending_verification','bank_credit_verified':'bank_credit_verified'}.get(body.status)
    if timeline_status:
        for r in store.requests(s['user_id']):
            if r['order_id']==oid and r['status'] in ('merchant_processing','refund_processed_by_merchant','bank_credit_pending_verification'):
                store.transition(s['user_id'],r['id'],timeline_status,'User-recorded refund evidence: '+body.note)
    store.log(s['user_id'],'refund_updated',{'order_id':oid,'status':body.status})
    return saved

@router.post('/receipts',status_code=201)
async def upload(file:UploadFile=File(...),s=Depends(user)):
    data=await file.read(MAX_BYTES+1)
    try: extracted,mime,suffix=await asyncio.to_thread(extract_file,file.filename,data)
    except ValueError as e: raise HTTPException(422,str(e)) from None
    eid=store.add_evidence(s['user_id'],None,'receipt','User upload',extracted)
    root=Path(os.getenv('RR_UPLOAD_ROOT',str(ROOT/'data/uploads')));root.mkdir(parents=True,exist_ok=True)
    path=root/(eid+suffix)
    await asyncio.to_thread(path.write_bytes,data)
    with db() as c: c.execute('INSERT INTO receipts VALUES(?,?,?,?)',(eid,str(path.resolve()),mime,len(data)))
    store.log(s['user_id'],'receipt_uploaded',{'evidence_id':eid,'bytes':len(data)})
    return {'id':eid,**extracted}

class ConfirmReceipt(BaseModel):
    order_id:str=Field(min_length=3,max_length=40,pattern=r'^[A-Za-z0-9_-]+$')
    merchant:str=Field(min_length=2,max_length=100)
    product:str=Field(min_length=2,max_length=160)
    purchase_date:date
    delivery_date:date|None=None
    amount:float=Field(gt=0,le=10000000)
    currency:str=Field(pattern=r'^[A-Z]{3}$')
    payment_reference:str|None=Field(default=None,max_length=100)
    support:str=Field(max_length=254)
    category:str=Field(default='unspecified',max_length=80)
    confirmed:bool

@router.post('/receipts/{eid}/confirm')
def confirm_receipt(eid:str,body:ConfirmReceipt,s=Depends(user)):
    ev=next((e for e in store.evidence(s['user_id']) if e['id']==eid and e['kind']=='receipt'),None)
    if not ev: raise HTTPException(404,'Receipt not found')
    if not body.confirmed: raise HTTPException(422,'Explicit field confirmation required')
    if not re.fullmatch(r'[^\s@\r\n]+@[^\s@\r\n]+\.[^\s@\r\n]+',body.support): raise HTTPException(422,'Enter the merchant support email from your evidence')
    if body.delivery_date and body.delivery_date<body.purchase_date: raise HTTPException(422,'Delivery cannot precede purchase')
    o={'id':body.order_id,'item':body.product,'merchant':body.merchant,'purchase_date':str(body.purchase_date),'delivered':str(body.delivery_date) if body.delivery_date else None,'amount_inr':body.amount,'currency':body.currency,'receipt_id':eid,'receipt':ev['body'].get('text','User-confirmed receipt fields'),'support':body.support,'policy_id':'USER-'+body.order_id,'confirmed':True,'demo':False,'condition':'unused_with_packaging','category':body.category}
    import sqlite3
    try:
        with db() as c:
            c.execute('INSERT INTO purchases VALUES(?,?,?)',(s['user_id'],body.order_id,canonical(o)))
            c.execute('UPDATE evidence_records SET verified=1,order_id=? WHERE id=?',(body.order_id,eid))
    except sqlite3.IntegrityError: raise HTTPException(409,'Order ID already exists; use a unique ID') from None
    store.log(s['user_id'],'receipt_confirmed',{'order_id':body.order_id})
    return o

class PolicyInput(BaseModel):
    type:Literal['return','replacement','unknown']
    days:int|None=Field(default=None,ge=0,le=365)
    text:str=Field(min_length=20,max_length=10000)
    source:str=Field(min_length=3,max_length=500)
    exceptions:list[str]=Field(default_factory=list,max_length=20)
    excluded_categories:list[str]=Field(default_factory=list,max_length=30)
    confirmed:bool

@router.post('/purchases/{oid}/policy')
def save_policy(oid:str,body:PolicyInput,s=Depends(user)):
    o=own_purchase(s,oid)
    if o.get('demo'): raise HTTPException(409,'Fictional policies are read-only')
    if not body.confirmed: raise HTTPException(422,'Confirm the quoted policy source')
    old=store.policy(s['user_id'],o['policy_id'])
    p={**body.model_dump(),'version':old.get('version',0)+1,'source_id':o['policy_id']+'-v'+str(old.get('version',0)+1),'source_kind':'user-provided policy; merchant confirmation may be required'}
    with db() as c: c.execute('INSERT OR REPLACE INTO merchant_policies VALUES(?,?,?)',(s['user_id'],o['policy_id'],canonical(p)))
    store.add_evidence(s['user_id'],oid,'policy',body.source,p,True)
    store.log(s['user_id'],'policy_updated',{'order_id':oid,'version':p['version']})
    return p

@router.get('/evidence')
def vault(s=Depends(user)):
    ev=store.evidence(s['user_id'])
    with db() as c: files={r['id'] for r in c.execute('SELECT id FROM receipts')}
    for e in ev: e['has_file']=e['id'] in files
    return ev

@router.get('/evidence/{eid}/file')
def evidence_file(eid:str,s=Depends(user)):
    with db() as c: r=c.execute('SELECT r.*,e.user_id FROM receipts r JOIN evidence_records e ON e.id=r.id WHERE r.id=? AND e.user_id=?',(eid,s['user_id'])).fetchone()
    if not r or not Path(r['path']).is_file(): raise HTTPException(404,'Document not found')
    return FileResponse(r['path'],media_type=r['mime'],filename='evidence'+Path(r['path']).suffix,content_disposition_type='attachment')

class AttachEvidence(BaseModel):
    order_id:str=Field(max_length=40)
    kind:Literal['refund_document','merchant_response']

@router.post('/evidence/{eid}/attach')
def attach(eid:str,body:AttachEvidence,s=Depends(user)):
    own_purchase(s,body.order_id)
    with db() as c:
        r=c.execute('SELECT * FROM evidence_records WHERE id=? AND user_id=?',(eid,s['user_id'])).fetchone()
        if not r: raise HTTPException(404,'Evidence not found')
        if r['verified'] or r['order_id']: raise HTTPException(409,'Use a new upload; confirmed purchase evidence cannot be repurposed')
        c.execute('UPDATE evidence_records SET order_id=?,kind=? WHERE id=?',(body.order_id,body.kind,eid))
    return {'ok':True}

class Question(BaseModel):
    order_id:str=Field(max_length=40)
    question:str=Field(min_length=3,max_length=1000)

@router.post('/assistant')
async def assistant(body:Question,s=Depends(user)):
    o=own_purchase(s,body.order_id)
    d=await call(s,'evidence','validate_evidence',{'order_id':body.order_id,'condition':o.get('condition','unused_with_packaging')})
    r=await call(s,'evidence','get_refund_evidence',{'order_id':body.order_id})
    return await answer(body.question,o,d,r)

@router.get('/dashboard')
async def dashboard(start:date|None=None,end:date|None=None,s=Depends(user)):
    orders=await purchases(s);returns=store.requests(s['user_id'])
    if start: returns=[r for r in returns if r['created_at'][:10]>=str(start)]
    if end: returns=[r for r in returns if r['created_at'][:10]<=str(end)]
    decisions=Counter(o['decision']['status'] for o in orders)
    with db() as c:
        activity=[{**dict(r),'body':json.loads(r['body'])} for r in c.execute('SELECT * FROM audit_logs WHERE user_id=? ORDER BY id DESC LIMIT 20',(s['user_id'],))]
        refund_rows=[json.loads(r['body']) for r in c.execute('SELECT body FROM refund_records WHERE user_id=?',(s['user_id'],))]
    return {'metrics':{'total_purchases':len(orders),'total_return_requests':len(returns),'eligible_returns':decisions['ELIGIBLE'],'ineligible_returns':decisions['EXPIRED']+decisions['NOT_ELIGIBLE']+decisions['REPLACEMENT_ONLY'],'pending_approvals':sum(r['status']=='awaiting_approval' for r in returns),'requires_review':decisions['HUMAN_REVIEW_REQUIRED']+decisions['INSUFFICIENT_EVIDENCE'],'prepared_plans':sum(r['status']=='prepared' for r in returns),'refunds_unverified':sum(not r.get('bank_credit_verified',False) for r in refund_rows)},'decision_distribution':dict(decisions),'status_distribution':dict(Counter(r['status'] for r in returns)),'merchant_distribution':dict(Counter(o['merchant'] for o in orders)),'activity':activity,'purchases':orders,'returns':returns,'scenario_clock':FIXTURE['as_of'],'filter_note':'Date range filters return requests; purchase eligibility uses the relevant current or fictional clock.'}

@router.get('/mcp')
async def mcp_activity(s=Depends(user)):
    health={}
    for role,connection in host().sessions.items():
        try:
            await asyncio.wait_for(connection.send_ping(),timeout=3)
            health[role]={'process_health':'responsive','connection':'MCP ping succeeded'}
        except Exception: health[role]={'process_health':'unresponsive or disconnected','connection':'MCP ping failed'}
    with db() as c: trace=[json.loads(r['body']) for r in c.execute('SELECT body FROM mcp_events WHERE user_id=? ORDER BY id DESC LIMIT 200',(s['user_id'],))]
    return {'servers':{role:{**meta,**health.get(role,{})} for role,meta in host().servers.items()},'trace':list(reversed(trace)),'privacy':'Actual response structures are retained with personal content redacted. Approval tokens are redacted. History persists in SQLite.'}

@router.get('/settings')
def settings(s=Depends(user)):
    import shutil
    return {'timezone':'Asia/Kolkata','email':'Local downloadable .eml','calendar':'Local availability and downloadable .ics','gmail_connected':False,'google_calendar_connected':False,'llm_provider':os.getenv('RR_LLM_PROVIDER','deterministic'),'ocr_available':bool(shutil.which('tesseract')),'session_hours':12,'demo_clock':FIXTURE['as_of']}
