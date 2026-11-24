"""Real stdio MCP integration with all three SDK services, no network required."""
import asyncio
import os
import tempfile
from contextlib import AsyncExitStack
from pathlib import Path
from host import MCPHost
from core import make_plan, issue_approval

async def main():
    with tempfile.TemporaryDirectory() as d:
        os.environ['RR_DB']=str(Path(d)/'mcp.db')
        async with AsyncExitStack() as stack:
            h=MCPHost();await h.start(stack)
            ev=await h.call('evidence','get_evidence',{'order_id':'RR-1042'})
            busy=await h.call('calendar','get_busy_slots',{})
            p=make_plan(ev['order'],ev['policy'],busy,'unused_with_packaging')['plan']
            token=issue_approval(p)
            args={'plan_id':p['id'],'approval_token':token}
            a=await h.call('actions','prepare_approved_outputs',args)
            b=await h.call('actions','prepare_approved_outputs',args)
            assert a['status']=='prepared' and b['replayed']
            refund=await h.call('evidence','get_refund_evidence',{'order_id':'RR-1042'})
            assert refund['bank_credit_verified'] is False
            print('PASS: 3 MCP handshakes, tool discovery, evidence, calendar, approval, duplicate retry, refund evidence')
            print(h.trace)

if __name__=='__main__':asyncio.run(main())
