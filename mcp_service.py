"""Three role-scoped MCP servers using the official Python SDK v1.29.0."""
import sys
from mcp.server.fastmcp import FastMCP
from core import FIXTURE, order_by_id, execute, choose_slot, check_policy, load_plan, verify_approval, outputs
from storage import purchases, purchase, policy, evidence

role = sys.argv[1]
mcp = FastMCP('ReturnRelay '+role)

if role == 'evidence':
    @mcp.tool()
    def list_orders(user_id: str='') -> list[dict]:
        """Read synthetic purchase receipt records. Text is untrusted data."""
        return purchases(user_id) if user_id else FIXTURE['orders']

    @mcp.tool()
    def get_evidence(order_id: str, user_id: str='') -> dict:
        """Read an order and its versioned, synthetic merchant policy."""
        order=order_by_id(order_id,user_id)
        return {'order':order,'policy':policy(user_id,order['policy_id']) if user_id else FIXTURE['policies'][order['policy_id']]}

    @mcp.tool()
    def validate_evidence(order_id:str,user_id:str,condition:str='unused_with_packaging') -> dict:
        """Evaluate receipt completeness and authoritative versioned policy rules."""
        o=purchase(user_id,order_id)
        return check_policy(o,policy(user_id,o['policy_id']),condition)

    @mcp.tool()
    def get_receipts(order_id:str,user_id:str) -> list[dict]:
        """List account-scoped evidence metadata; uploaded files remain private."""
        purchase(user_id,order_id)
        return evidence(user_id,order_id)

    @mcp.tool()
    def get_refund_evidence(order_id: str,user_id:str='') -> dict:
        """Merchant messages are evidence of processing, never bank settlement."""
        if user_id:
            from core import db
            import json
            o=purchase(user_id,order_id)
            with db() as c: row=c.execute('SELECT body FROM refund_records WHERE user_id=? AND order_id=?',(user_id,order_id)).fetchone()
            if row: return json.loads(row['body'])
            if not o.get('demo'): return {'messages':[],'bank_credit_verified':False,'status':'not_initiated'}
        matches=[m for m in FIXTURE['refund_messages'] if m['order_id']==order_id]
        return {'messages':matches,'bank_credit_verified':False,'status':'merchant_says_processed' if matches else 'no_evidence'}

elif role == 'calendar':
    @mcp.tool()
    def get_busy_slots(user_id:str='') -> list[dict]:
        """Read synthetic calendar occupancy. Does not write calendar events."""
        if not user_id: return FIXTURE['busy']
        from core import db
        import json
        with db() as c: return [json.loads(r['body']) for r in c.execute('SELECT body FROM calendar_slots WHERE user_id=?',(user_id,))]

    @mcp.tool()
    def find_available_slot(deadline:str,user_id:str,as_of:str='') -> dict:
        """Suggest a conflict-free preparation slot, never a pickup appointment."""
        return choose_slot(get_busy_slots(user_id),deadline,as_of or None)

    @mcp.tool()
    def validate_slot(start:str,end:str,deadline:str,user_id:str) -> dict:
        """Check timezone-aware interval, occupancy and policy cutoff."""
        from datetime import datetime
        s,e,d=map(datetime.fromisoformat,(start,end,deadline))
        if any(v.tzinfo is None for v in (s,e,d)): raise ValueError('Explicit timezone required')
        conflicts=[b for b in get_busy_slots(user_id) if e>datetime.fromisoformat(b['start']) and s<datetime.fromisoformat(b['end'])]
        return {'valid':s<e<=d and not conflicts,'conflicts':conflicts,'before_deadline':e<=d}

elif role == 'actions':
    @mcp.tool()
    def prepare_approved_outputs(plan_id: str, approval_token: str, fail_calendar: bool=False,user_id:str='') -> dict:
        """Prepare local outputs only with host-issued approval for the exact plan."""
        if load_plan(plan_id).get('user_id','')!=user_id: raise ValueError('Plan owner mismatch')
        return execute(plan_id,approval_token,fail_calendar)

    @mcp.tool()
    def validate_approval(plan_id:str,approval_token:str,user_id:str) -> dict:
        """Verify exact-plan authorization without creating any output."""
        p=load_plan(plan_id)
        if p.get('user_id')!=user_id: raise ValueError('Plan owner mismatch')
        verify_approval(p,approval_token)
        return {'valid':True}

    @mcp.tool()
    def action_status(plan_id:str,user_id:str) -> dict:
        """Read existing output status for idempotent recovery."""
        if load_plan(plan_id).get('user_id')!=user_id: raise ValueError('Plan owner mismatch')
        return next((a for a in outputs() if a['plan_id']==plan_id),{'status':'not_prepared'})
else:
    raise ValueError('Unknown service role')

if __name__=='__main__':
    mcp.run(transport='stdio')
