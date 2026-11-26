"""Bounded MCP host. The browser never receives approval signing keys."""
import asyncio
import json
import os
import secrets
import sys
import time
from dotenv import load_dotenv
from datetime import datetime, timezone
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
load_dotenv(Path(__file__).resolve().parent/'.env')
from email.message import EmailMessage
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from typing import Literal
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from core import ROOT, FIXTURE, make_plan, load_plan, issue_approval, outputs, ledger, audit

def trace_response(value,key=''):
    """Retain response structure and protocol facts, redact personal content."""
    sensitive={'receipt','text','support','message','body','to','subject','amount','amount_inr','payment_reference','path','note','item','merchant','product','label'}
    if key in sensitive: return '[redacted personal content]'
    if isinstance(value,dict): return {k:trace_response(v,k) for k,v in value.items()}
    if isinstance(value,list): return [trace_response(v,key) for v in value[:30]]
    return value

def persist_trace(record):
    if record.get('user_id'):
        from core import db, canonical
        with db() as c: c.execute('INSERT INTO mcp_events(user_id,body,created_at) VALUES(?,?,?)',(record['user_id'],canonical(record),record['created_at']))

class MCPHost:
    def __init__(self): self.sessions={}; self.trace=[]; self.servers={}
    async def start(self,stack):
        self.sessions={}; self.trace=[]; self.servers={}
        if not os.environ.get('RR_APPROVAL_KEY'): os.environ['RR_APPROVAL_KEY']=secrets.token_hex(32)
        if len(os.environ['RR_APPROVAL_KEY'])<32: raise RuntimeError('RR_APPROVAL_KEY must contain at least 32 characters')
        for role in ['evidence','calendar','actions']:
            env={**os.environ}
            # Read-only services don't receive the signing key.
            if role!='actions': env.pop('RR_APPROVAL_KEY',None)
            io=await stack.enter_async_context(stdio_client(StdioServerParameters(command=sys.executable,args=[str(ROOT/'mcp_service.py'),role],env=env)))
            session=await stack.enter_async_context(ClientSession(*io))
            init=await session.initialize()
            found=await session.list_tools()
            self.sessions[role]=session
            self.servers[role]={'process_health':'running at handshake','connection':'initialized','protocol':init.protocolVersion,'tools':[{'name':t.name,'description':t.description,'input_schema':t.inputSchema} for t in found.tools]}
            self.trace.append({'server':role,'method':'initialize + tools/list','tools':[t.name for t in found.tools],'protocol':init.protocolVersion})
    async def call(self,role,name,args):
        started=time.perf_counter()
        uid=args.get('user_id')
        safe={k:('[redacted]' if k in ('approval_token',) else v) for k,v in args.items()}
        record={'server':role,'method':'tools/call','tool':name,'parameters':safe,'user_id':uid,'created_at':datetime.now(timezone.utc).isoformat(),'retry':'caller-controlled; no automatic action retry'}
        try:
            r=await asyncio.wait_for(self.sessions[role].call_tool(name,args),timeout=30)
        except Exception:
            record.update(ok=False,error='MCP service unavailable or timed out',duration_ms=round((time.perf_counter()-started)*1000,2))
            self.trace.append(record)
            persist_trace(record)
            self.servers[role]['connection']='call failed; reconnect by restarting host'
            raise ValueError('MCP service unavailable. No success assumed; retry after recovery.') from None
        record.update(ok=not r.isError,duration_ms=round((time.perf_counter()-started)*1000,2))
        if r.structuredContent:
            v=r.structuredContent; value=v.get('result',v)
        else:
            texts=[c.text for c in r.content if c.type=='text']
            try: value=json.loads(texts[0])
            except (ValueError,IndexError): value=' '.join(texts)
        # Payload previews are allowlisted to avoid publishing receipt text or contact details.
        record['response']=trace_response(value) if not r.isError else {'error':'Tool returned an error; personal content omitted'}
        record['business_success']=not r.isError and not (isinstance(value,dict) and (value.get('eligible') is False or value.get('valid') is False))
        self.trace.append(record); self.trace=self.trace[-200:]
        persist_trace(record)
        if r.isError: raise ValueError(' '.join(c.text for c in r.content if c.type=='text'))
        return value

host=MCPHost()
@asynccontextmanager
async def lifespan(app):
    from storage import migrate
    migrate()
    async with AsyncExitStack() as stack:
        await host.start(stack)
        yield

app=FastAPI(title='ReturnRelay 2.0',version='2.0.0',lifespan=lifespan)
allowed_hosts = ['127.0.0.1', 'localhost', 'testserver', 'returnrelay.onrender.com', '*.onrender.com']
render_host = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if render_host and render_host not in allowed_hosts:
    allowed_hosts.append(render_host)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
CSRF=secrets.token_urlsafe(32)
@app.middleware('http')
async def protect(request:Request,call_next):
    if request.method=='POST' and not request.url.path.startswith('/api/v2/') and request.headers.get('x-demo-token')!=CSRF:
        return Response('Missing browser session token',status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith('/api/'): response.headers['Cache-Control']='no-store'
    return response

def legacy_outputs():
    return [a for a in outputs() if not load_plan(a['plan_id']).get('user_id')]

def legacy_trace(): return [r for r in host.trace if not r.get('user_id')]

app.mount('/static',StaticFiles(directory=str(ROOT/'static')),name='static')
@app.get('/')
def home(): return FileResponse(ROOT/'static/index.html')

@app.get('/api/bootstrap')
async def bootstrap():
    return {'token':CSRF,'as_of':FIXTURE['as_of'],'orders':await host.call('evidence','list_orders',{}),'outputs':legacy_outputs(),'trace':legacy_trace()}

class PlanRequest(BaseModel):
    order_id:str=Field(max_length=40)
    condition:Literal['unused_with_packaging','opened_or_missing_parts']='unused_with_packaging'

@app.post('/api/plan')
async def plan(req:PlanRequest):
    try:
        ev=await host.call('evidence','get_evidence',{'order_id':req.order_id})
        busy=await host.call('calendar','get_busy_slots',{})
        result=make_plan(ev['order'],ev['policy'],busy,req.condition)
        return {**result,'trace':legacy_trace()}
    except ValueError as e: raise HTTPException(400,str(e))

class ApprovalRequest(BaseModel):
    plan_id:str=Field(max_length=30)
    approved:bool=False
    fail_calendar:bool=False

@app.post('/api/approve')
async def approve(req:ApprovalRequest):
    if not req.approved: raise HTTPException(400,'Explicit approval required')
    try:
        p=load_plan(req.plan_id)
        if p.get('user_id'): raise ValueError('Use authenticated approval')
        token=issue_approval(p)
        result=await host.call('actions','prepare_approved_outputs',{'plan_id':req.plan_id,'approval_token':token,'fail_calendar':req.fail_calendar})
        return {'result':result,'outputs':legacy_outputs(),'trace':legacy_trace()}
    except ValueError as e: raise HTTPException(400,str(e))

@app.get('/api/refund/{order_id}')
async def refund(order_id:str): return await host.call('evidence','get_refund_evidence',{'order_id':order_id})

@app.get('/api/audit')
def audit_log():
    entries=[]
    for row in ledger():
        pid=json.loads(row['body']).get('plan_id')
        if pid:
            try:
                if not load_plan(pid).get('user_id'): entries.append(row)
            except ValueError: pass
    return {'audit':entries,'trace':legacy_trace()}

@app.get('/api/download/{action_id}/{kind}')
def download(action_id:str,kind:str,request:Request):
    action=next((a for a in outputs() if a['id']==action_id),None)
    if not action: raise HTTPException(404,'Output not found')
    owner=load_plan(action['plan_id']).get('user_id')
    if owner:
        from security import session
        if session(request)['user_id']!=owner: raise HTTPException(404,'Output not found')
    from adapters import local_outputs
    try: content,mime,filename=local_outputs.render(action,kind)
    except ValueError: raise HTTPException(404,'Unknown format') from None
    return Response(content,media_type=mime,headers={'Content-Disposition':'attachment; filename='+filename})

from api_v2 import router, bind_host
bind_host(host)
app.include_router(router)

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8000)
