"""Provider contracts and the active no-credentials local artifact provider.

OAuth providers may implement these interfaces, but must explicitly report their
connection state and retain host approval and provider idempotency checks.
"""
from typing import Protocol
from email.message import EmailMessage
from datetime import datetime, timezone

class MerchantAdapter(Protocol):
    def policy(self, merchant:str, category:str) -> dict: ...

class CalendarAdapter(Protocol):
    def busy(self, user_id:str) -> list[dict]: ...

class OutputAdapter(Protocol):
    def prepare(self, plan:dict) -> dict: ...
    def render(self, action:dict, kind:str) -> tuple[bytes,str,str]: ...

class LocalOutputAdapter:
    """Generates local files only. Does not connect to Gmail or Calendar."""
    def prepare(self, plan):
        return {'email_draft':{'to':plan['support'],'subject':plan['subject'],'body':plan['message']},
                'calendar':{**plan['slot'],'title':'Prepare return '+plan['order_id']}}

    def render(self, action, kind):
        if kind=='eml':
            d=action['email_draft'];m=EmailMessage();m['To']=d['to'];m['Subject']=d['subject'];m['X-Unsent']='1';m.set_content(d['body'])
            return m.as_bytes(),'message/rfc822','return-draft.eml'
        if kind=='ics':
            def utc(s):return datetime.fromisoformat(s).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            def escape(s):return s.replace('\\','\\\\').replace('\r','').replace('\n','\\n').replace(';','\\;').replace(',','\\,')
            c=action['calendar']
            text='\r\n'.join(['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//ReturnRelay//2.0//EN','CALSCALE:GREGORIAN','BEGIN:VEVENT','UID:'+action['id']+'@returnrelay.local','DTSTAMP:'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),'DTSTART:'+utc(c['start']),'DTEND:'+utc(c['end']),'SUMMARY:'+escape(c['title']),'DESCRIPTION:Preparation reminder only. No merchant pickup is booked.','BEGIN:VALARM','TRIGGER:-PT30M','ACTION:DISPLAY','DESCRIPTION:Prepare item and review merchant instructions','END:VALARM','END:VEVENT','END:VCALENDAR',''])
            return text.encode(),'text/calendar','return-reminder.ics'
        raise ValueError('Unknown output format')

local_outputs=LocalOutputAdapter()
