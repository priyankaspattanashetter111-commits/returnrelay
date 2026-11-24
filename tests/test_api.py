import os,tempfile,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory() as d:
 os.environ['RR_DB']=str(Path(d)/'api.db')
 from fastapi.testclient import TestClient
 from host import app
 with TestClient(app) as c:
  b=c.get('/api/bootstrap').json();assert len(b['orders'])==4
  assert c.post('/api/plan',json={'order_id':'RR-1042'}).status_code==403
  headers={'x-demo-token':b['token']}
  p=c.post('/api/plan',json={'order_id':'RR-1042'},headers=headers).json()['plan']
  a=c.post('/api/approve',json={'plan_id':p['id'],'approved':True,'fail_calendar':True},headers=headers)
  assert a.status_code==400
  assert c.get('/api/bootstrap').json()['outputs']==[]
  a=c.post('/api/approve',json={'plan_id':p['id'],'approved':True},headers=headers).json()['result']
  assert c.get('/api/download/'+a['id']+'/ics').status_code==200
  assert c.get('/api/download/'+a['id']+'/eml').status_code==200
  retry=c.post('/api/approve',json={'plan_id':p['id'],'approved':True},headers=headers).json()['result']
  assert retry['replayed']
  assert c.post('/api/plan',json={'order_id':'RR-1044'},headers=headers).json()['status']=='blocked'
  print('PASS API: CSRF protection, plan, failure rollback, approval, EML/ICS downloads, retry, policy stop')
