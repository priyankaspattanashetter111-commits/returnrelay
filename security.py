"""Password hashing and persistent expiring browser sessions."""
import hashlib
import hmac
import secrets
import time
from fastapi import HTTPException, Request
from core import db

COOKIE='rr_session'
def password_hash(password, salt=None):
    salt=salt or secrets.token_hex(16)
    key=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
    return salt+':'+key

def password_matches(password, stored):
    return hmac.compare_digest(password_hash(password,stored.split(':')[0]),stored)

def session(request:Request):
    sid=request.cookies.get(COOKIE,'')
    key=hashlib.sha256(sid.encode()).hexdigest()
    with db() as c:
        r=c.execute('SELECT s.*,u.email,u.name FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=? AND s.expires>?',(key,time.time())).fetchone()
    if not r: raise HTTPException(401,'Please sign in')
    if request.method not in ('GET','HEAD','OPTIONS') and not hmac.compare_digest(request.headers.get('x-csrf-token',''),r['csrf']):
        raise HTTPException(403,'Invalid session request token')
    return dict(r)

def create_session(uid,response):
    raw=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
    with db() as c:
        c.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
        c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(raw.encode()).hexdigest(),uid,csrf,time.time()+43200))
    import os
    response.set_cookie(COOKIE,raw,httponly=True,samesite='strict',secure=os.getenv('RR_SECURE_COOKIE')=='1',max_age=43200)
    return csrf
