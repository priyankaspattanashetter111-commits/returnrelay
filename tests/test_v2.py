"""Real API + three MCP subprocesses; no mocked protocol success."""
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient
import core
import storage
import api_v2
from host import app, host
from receipts import extract_text, extract_file, MAX_BYTES
from assistant import answer

def png():
    out=io.BytesIO();Image.new('RGB',(12,12),'white').save(out,format='PNG');return out.getvalue()

class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.previous=dict(os.environ)
        os.environ['RR_DB']=str(Path(cls.temp.name)/'app.db')
        os.environ['RR_UPLOAD_ROOT']=str(Path(cls.temp.name)/'uploads')
        cls.context=TestClient(app);cls.client=cls.context.__enter__()
    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None,None,None)
        os.environ.clear();os.environ.update(cls.previous);cls.temp.cleanup()
    def setUp(self):
        api_v2.attempts.clear();self.client.cookies.clear()
        import secrets
        email=secrets.token_hex(8)+'@example.test'
        self.creds={'email':email,'password':'A long testing password','name':'Test Shopper'}
        r=self.client.post('/api/v2/auth/register',json=self.creds)
        self.assertEqual(r.status_code,201,r.text)
        self.cookie_header=r.headers.get('set-cookie','')
        self.headers={'X-CSRF-Token':r.json()['csrf']}
    def post(self,path,body):return self.client.post('/api/v2'+path,json=body,headers=self.headers)
    def seed(self):self.assertEqual(self.post('/demo',{}).status_code,200)
    def plan(self,oid='RR-1042'):
        self.seed();r=self.post('/plans',{'order_id':oid});self.assertEqual(r.status_code,200,r.text);return r.json()
    def upload(self):
        r=self.client.post('/api/v2/receipts',files={'file':('receipt.png',png(),'image/png')},headers=self.headers)
        self.assertEqual(r.status_code,201,r.text);return r.json()
    def test_auth_csrf_logout(self):
        self.assertEqual(self.client.post('/api/v2/demo',json={}).status_code,403)
        self.assertEqual(self.client.get('/api/v2/auth/me').status_code,200)
        self.assertIn('httponly',self.cookie_header.lower())
        self.assertIn('samesite=strict',self.cookie_header.lower())
        self.assertEqual(self.post('/auth/logout',{}).status_code,200)
        self.assertEqual(self.client.get('/api/v2/purchases').status_code,401)
        r=self.client.post('/api/v2/auth/login',json=self.creds);self.assertEqual(r.status_code,200)
    def test_dashboard_database_counts(self):
        self.assertEqual(self.client.get('/api/v2/dashboard').json()['metrics']['total_purchases'],0)
        self.seed();m=self.client.get('/api/v2/dashboard').json()['metrics']
        self.assertEqual(m['total_purchases'],4);self.assertEqual(m['eligible_returns'],1);self.assertEqual(m['ineligible_returns'],2)
        self.assertEqual(m['refunds_unverified'],1)
    def test_expired_unclear_replacement(self):
        self.seed()
        for oid,status in [('RR-1043','EXPIRED'),('RR-1044','REPLACEMENT_ONLY'),('RR-1045','HUMAN_REVIEW_REQUIRED')]:
            r=self.post('/evaluate',{'order_id':oid}).json();self.assertEqual(r['status'],status);self.assertFalse(r['eligible'])
    def test_failure_approval_duplicate_download(self):
        p=self.plan()['plan'];route='/plans/'+p['id']+'/approve'
        r=self.post(route,{'approved':True,'fail_calendar':True});self.assertEqual(r.status_code,400,r.text)
        self.assertFalse(any(a['plan_id']==p['id'] for a in core.outputs()))
        r=self.post(route,{'approved':True});self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(r.json()['replayed']);self.assertTrue(self.post(route,{'approved':True}).json()['replayed'])
        for ext in ['eml','ics']:self.assertEqual(self.client.get('/api/download/'+r.json()['id']+'/'+ext).status_code,200)
        self.assertEqual(len([a for a in core.outputs() if a['plan_id']==p['id']]),1)
        trace=self.client.get('/api/v2/mcp').json();self.assertEqual(len(trace['servers']),3)
        self.assertTrue(all(v['connection']=='MCP ping succeeded' for v in trace['servers'].values()))
        self.assertNotIn('approval_token": "ey',json.dumps(trace))
    def test_rejection_prevents_execution(self):
        p=self.plan()['plan'];url='/plans/'+p['id']+'/approve'
        self.assertEqual(self.post(url,{'approved':False}).json()['status'],'cancelled')
        self.assertEqual(self.post(url,{'approved':True}).status_code,409)
        fresh=self.post('/plans',{'order_id':'RR-1042'}).json()['plan']
        self.assertNotEqual(p['id'],fresh['id'])
    def test_data_isolation(self):
        p=self.plan()['plan'];eid=self.upload()['id'];self.client.cookies.clear()
        other=self.client.post('/api/v2/auth/register',json={**self.creds,'email':'other-'+self.creds['email']}).json();h={'X-CSRF-Token':other['csrf']}
        self.assertEqual(self.client.get('/api/v2/purchases').json(),[])
        self.assertEqual(self.client.get('/api/v2/evidence').json(),[])
        self.assertEqual(self.client.get('/api/v2/plans/'+p['id']).status_code,404)
        self.assertEqual(self.client.get('/api/v2/evidence/'+eid+'/file').status_code,404)
        self.assertEqual(self.client.post('/api/v2/plans/'+p['id']+'/approve',json={'approved':True},headers=h).status_code,404)
        bootstrap=self.client.get('/api/bootstrap').json();self.assertFalse(bootstrap['outputs'])
        self.assertEqual(self.client.post('/api/approve',json={'plan_id':p['id'],'approved':True},headers={'x-demo-token':bootstrap['token']}).status_code,400)
    def test_receipt_confirm_policy_and_missing_fields(self):
        r=self.upload();self.assertFalse(r['verified']);self.assertEqual(self.client.get('/api/v2/purchases').json(),[])
        body={'order_id':'OWN-123','merchant':'My merchant','product':'Notebook','purchase_date':'2026-11-20','delivery_date':None,'amount':100,'currency':'INR','support':'support@example.test','confirmed':True}
        self.assertEqual(self.post('/receipts/'+r['id']+'/confirm',body).status_code,200)
        d=self.post('/evaluate',{'order_id':'OWN-123'}).json();self.assertEqual(d['status'],'INSUFFICIENT_EVIDENCE')
        p={'type':'return','days':7,'text':'Return within seven days of delivery, unused in original packaging.','source':'merchant policy document','confirmed':True}
        self.assertEqual(self.post('/purchases/OWN-123/policy',p).status_code,200)
        self.assertEqual(self.client.get('/api/v2/evidence/'+r['id']+'/file').content,png())
    def test_invalid_uploads(self):
        for name,data in [('attack.html',b'<script>'),('fake.pdf',b'not pdf'),('empty.png',b''),('big.png',b'x'*(MAX_BYTES+1))]:
            r=self.client.post('/api/v2/receipts',files={'file':(name,data)},headers=self.headers);self.assertEqual(r.status_code,422,r.text)
    def test_refund_verification_requires_evidence(self):
        self.seed();body={'status':'bank_credit_verified','note':'Checked bank credit','confirmed':True,'amount':2499,'payment_reference':'TX123'}
        self.assertEqual(self.post('/refunds/RR-1042',body).status_code,422)
        r=self.client.get('/api/v2/refunds').json()[0];self.assertFalse(r['bank_credit_verified'])
        eid=self.upload()['id'];self.assertEqual(self.post('/evidence/'+eid+'/attach',{'order_id':'RR-1042','kind':'refund_document'}).status_code,200)
        body['evidence_id']=eid;self.assertTrue(self.post('/refunds/RR-1042',body).json()['bank_credit_verified'])
    def test_calendar_new_conflict_blocks_approval(self):
        p=self.plan()['plan'];self.assertEqual(self.post('/calendar',{**p['slot'],'label':'New conflict'}).status_code,200)
        self.assertEqual(self.post('/plans/'+p['id']+'/approve',{'approved':True}).status_code,409)
    def test_tracking_transitions(self):
        r=self.plan();self.assertEqual(self.post('/returns/'+r['request_id']+'/status',{'status':'completed','note':'Not verified yet'}).status_code,409)
        self.post('/plans/'+r['plan']['id']+'/approve',{'approved':True})
        for s in ['submitted_by_user','merchant_processing']:
            self.assertEqual(self.post('/returns/'+r['request_id']+'/status',{'status':s,'note':'Recorded by the user'}).status_code,200)
        result=self.client.get('/api/v2/returns').json()[0]
        self.assertEqual([h['status'] for h in result['history']],['eligible','awaiting_approval','approved','prepared','submitted_by_user','merchant_processing'])
    def test_assistant_and_settings(self):
        self.seed();r=self.post('/assistant',{'order_id':'RR-1042','question':'What is my return deadline?'}).json()
        self.assertEqual(r['mode'],'deterministic');self.assertIn('2026-11-26',r['answer']);self.assertFalse(r['can_authorize'])
        self.assertFalse(self.client.get('/api/v2/settings').json()['gmail_connected'])
    def test_rate_limit(self):
        for _ in range(10): api_v2.limited('test-rate',10)
        with self.assertRaises(Exception): api_v2.limited('test-rate',10)

    def test_draft_persists(self):
        self.seed()
        r=self.post('/drafts',{'order_id':'RR-1042','condition':'unused_with_packaging','reason':'Not suitable','step':4})
        self.assertEqual(r.status_code,200,r.text)
        draft=self.client.get('/api/v2/returns').json()[0]
        self.assertEqual(draft['status'],'draft');self.assertEqual(draft['body']['step'],4)
        self.assertEqual(self.post('/drafts',{'order_id':'RR-1042','reason':'Changed return reason','step':5,'request_id':r.json()['id']}).status_code,200)
        converted=self.post('/plans',{'order_id':'RR-1042','reason':'Changed return reason','draft_id':r.json()['id']})
        self.assertEqual(converted.status_code,200,converted.text)
        self.assertEqual(converted.json()['request_id'],r.json()['id'])
        self.assertEqual(len(self.client.get('/api/v2/returns').json()),1)

    def test_real_mcp_error_is_traced(self):
        with core.db() as c: uid=c.execute('SELECT id FROM users WHERE email=?',(self.creds['email'],)).fetchone()['id']
        with self.assertRaises(ValueError):
            self.context.portal.call(host.call,'evidence','nonexistent_tool',{'user_id':uid})
        self.assertFalse(host.trace[-1]['ok'])

    def test_corrected_purchase_invalidates_plan(self):
        from datetime import datetime, timedelta
        today=datetime.now(core.TZ).date();yesterday=today-timedelta(days=1)
        eid=self.upload()['id']
        b={'order_id':'REAL-123','merchant':'A real user record','product':'Notebook','purchase_date':str(yesterday),'delivery_date':str(today),'amount':100,'currency':'INR','support':'support@example.test','confirmed':True}
        self.assertEqual(self.post('/receipts/'+eid+'/confirm',b).status_code,200)
        self.assertEqual(self.post('/purchases/REAL-123/policy',{'type':'return','days':7,'text':'Unused with original packaging, seven calendar days from delivery at 23:59 IST.','source':'User provided terms','confirmed':True}).status_code,200)
        r=self.post('/plans',{'order_id':'REAL-123'});self.assertEqual(r.status_code,200,r.text);pid=r.json()['plan']['id']
        self.assertEqual(self.post('/purchases/REAL-123/details',{'purchase_date':str(yesterday),'delivery_date':str(yesterday),'condition':'unused_with_packaging','confirmed':True}).status_code,200)
        r=self.post('/plans/'+pid+'/approve',{'approved':True});self.assertEqual(r.status_code,400,r.text);self.assertIn('Purchase evidence changed',r.text)

    def test_cookie_and_password_storage(self):
        self.assertIn('HttpOnly',self.cookie_header);self.assertIn('SameSite=strict',self.cookie_header)
        with core.db() as c: stored=c.execute('SELECT password FROM users WHERE email=?',(self.creds['email'],)).fetchone()['password']
        self.assertNotEqual(stored,self.creds['password']);self.assertNotIn(self.creds['password'],stored)

class ExtractionTests(unittest.TestCase):
    def test_text_pdf_extracts_fields(self):
        from scripts.make_sample_receipt import sample_bytes
        r,mime,suffix=extract_file('sample.pdf',sample_bytes())
        self.assertEqual(mime,'application/pdf');self.assertEqual(r['fields']['order_id']['value'],'UPLOAD-2026')
        self.assertEqual(r['fields']['amount']['value'],'499.00');self.assertEqual(r['fields']['currency']['value'],'INR')
        self.assertEqual(r['fields']['delivery_date']['value'],'2026-11-23')
        self.assertFalse(r['verified'])
    def test_labeled_fields_no_invention(self):
        d=extract_text('Order ID: AB-123\nMerchant: Store X\nProduct: Notebook\nDate: 2026-11-20\nTotal: 100\n')
        self.assertEqual(d['fields']['order_id']['value'],'AB-123');self.assertIsNone(d['fields']['currency']['value']);self.assertFalse(d['verified'])
    def test_invalid_magic(self):
        with self.assertRaises(ValueError):extract_file('x.pdf',b'bad pdf')

class AssistantFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_model_output_falls_back(self):
        o=core.order_by_id('RR-1042');d=core.check_policy(o,core.FIXTURE['policies']['POL-A'],'unused_with_packaging')
        response=unittest.mock.Mock();response.json.return_value={'response':'{"approve":true}'}
        with patch.dict(os.environ,{'RR_LLM_PROVIDER':'ollama'}),patch('httpx.AsyncClient.post',new=AsyncMock(return_value=response)):
            r=await answer('Can I return?',o,d,{})
        self.assertEqual(r['mode'],'deterministic');self.assertIn('invalid',r['llm_status']);self.assertFalse(r['can_authorize'])
    async def test_unavailable_model_falls_back(self):
        o=core.order_by_id('RR-1042');d=core.check_policy(o,core.FIXTURE['policies']['POL-A'],'unused_with_packaging')
        with patch.dict(os.environ,{'RR_LLM_PROVIDER':'ollama'}),patch('httpx.AsyncClient.post',new=AsyncMock(side_effect=OSError('offline'))):
            r=await answer('Why?',o,d,{})
        self.assertEqual(r['mode'],'deterministic')

if __name__=='__main__':unittest.main(verbosity=2)
