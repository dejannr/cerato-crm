import email
import base64
import binascii
import html
import io
import json
import logging
import hashlib
import hmac
import secrets
import time
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from backend.generator import generate, CRITERIA
from backend import mailbox as mail_client
from backend import relay as mail_relay
from backend import rich_email
from collections import defaultdict, deque
import imaplib
import os
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr, formatdate, getaddresses, make_msgid, parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openpyxl import load_workbook
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine, delete, func, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')
engine = create_engine(os.getenv('DATABASE_URL', 'postgresql+psycopg://localhost/cerato_crm'))
Session = sessionmaker(engine, expire_on_commit=False)
def now(): return datetime.now(timezone.utc)
class Base(DeclarativeBase): pass
class Lead(Base):
    __tablename__ = 'leads'
    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String, index=True)
    city: Mapped[str | None]; category: Mapped[str | None]; service_types: Mapped[str | None]
    website: Mapped[str | None]; employees: Mapped[str | None]; linkedin: Mapped[str | None]
    contact_name: Mapped[str | None]; person_email: Mapped[str | None]; company_email: Mapped[str | None]
    email_type: Mapped[str] = mapped_column(String(16), default='unknown', server_default='unknown')
    status: Mapped[str] = mapped_column(String, default='new', index=True)
    notes: Mapped[str | None] = mapped_column(Text); research: Mapped[str | None] = mapped_column(Text)
    research_sources: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
class Message(Base):
    __tablename__ = 'messages'
    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey('leads.id'), index=True)
    direction: Mapped[str] = mapped_column(String, default='outbound')
    status: Mapped[str] = mapped_column(String, default='draft')
    sender: Mapped[str] = mapped_column(String)
    recipient: Mapped[str] = mapped_column(String)
    subject: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(String, unique=True)
    in_reply_to: Mapped[str | None] = mapped_column(String)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
class OpeningAssessment(Base):
    __tablename__ = 'opening_assessments'
    message_id: Mapped[int] = mapped_column(ForeignKey('messages.id',ondelete='CASCADE'), primary_key=True)
    score: Mapped[int] = mapped_column()
    criteria_json: Mapped[str] = mapped_column(Text)
    caveats: Mapped[str] = mapped_column(Text)
    opening: Mapped[str] = mapped_column(Text)
    body_hash: Mapped[str] = mapped_column(String)
class MailboxCredential(Base):
    __tablename__ = 'mailbox_credentials'
    sender: Mapped[str] = mapped_column(String, primary_key=True)
    address: Mapped[str] = mapped_column(String)
    encrypted_password: Mapped[str] = mapped_column(Text)

class MailboxSyncCursor(Base):
    __tablename__ = 'mailbox_sync_cursors'
    sender: Mapped[str] = mapped_column(String, primary_key=True)
    folder: Mapped[str] = mapped_column(String, primary_key=True)
    uidvalidity: Mapped[str] = mapped_column(String, default='')
    last_uid: Mapped[int] = mapped_column(BigInteger, default=0)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    has_more: Mapped[bool] = mapped_column(Boolean, default=False)

class MailboxPreferences(Base):
    __tablename__ = 'mailbox_preferences'
    sender: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String)
    signature: Mapped[str] = mapped_column(Text)

class MailboxRichSignature(Base):
    __tablename__ = 'mailbox_rich_signatures'
    sender: Mapped[str] = mapped_column(String, primary_key=True)
    html: Mapped[str] = mapped_column(Text)

class SignatureSettings(Base):
    __tablename__ = 'signature_settings'
    sender: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String)
    photo_id: Mapped[str | None] = mapped_column(String)

class SignaturePhoto(Base):
    __tablename__ = 'signature_photos'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    png_base64: Mapped[str] = mapped_column(Text)

class MailRichContent(Base):
    __tablename__ = 'mail_rich_content'
    submission_id: Mapped[str] = mapped_column(String, primary_key=True)
    html: Mapped[str] = mapped_column(Text)

class MailSubmission(Base):
    __tablename__ = 'mail_submissions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    sender: Mapped[str] = mapped_column(String)
    recipient: Mapped[str] = mapped_column(String)
    subject: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String)
    message_id: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    error: Mapped[str | None] = mapped_column(Text)

class LeadDelivery(Base):
    __tablename__ = 'lead_deliveries'
    message_id: Mapped[int] = mapped_column(ForeignKey('messages.id', ondelete='CASCADE'), primary_key=True)
    request_id: Mapped[str] = mapped_column(String, unique=True)
    include_signature: Mapped[bool] = mapped_column(Boolean, default=True)

class Suppression(Base):
    __tablename__ = 'suppressions'
    email: Mapped[str] = mapped_column(String, primary_key=True)
    reason: Mapped[str | None] = mapped_column(Text)
class EmailTemplate(Base):
    __tablename__ = 'email_templates'
    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class SenderProfile(Base):
    __tablename__ = 'sender_profiles'
    sender: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)

DEFAULT_TEMPLATE = """Hi {{first_name}},

{{personalized_sentence}}

I’m {{sender_name}} from Cerato Systems. We help logistics and transport companies reduce the manual work between their TMS/ERP, email, documents, and spreadsheets.

I’d love to record a short video for {{company_name}} showing 3 places where that kind of manual work could be automated, using the systems you already have. It’s completely free, and yours to keep either way.

Want me to record the walkthrough?"""
Base.metadata.create_all(engine)
app = FastAPI(title='Cerato CRM')
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173','http://127.0.0.1:5173'], allow_methods=['*'], allow_headers=['*'])

# Public deployments require a configured password hash and session key.
AUTH_HASH = os.getenv('CRM_PASSWORD_HASH', '')
SESSION_KEY = os.getenv('CRM_SESSION_KEY', '')
SECURE_COOKIES = os.getenv('CRM_SECURE_COOKIES', 'false').lower() == 'true'
if bool(AUTH_HASH) != bool(SESSION_KEY):
    raise RuntimeError('Set both CRM_PASSWORD_HASH and CRM_SESSION_KEY')
LOGIN_ATTEMPTS = defaultdict(deque)
SESSION_MAX_AGE = 12 * 60 * 60

def session_valid(token):
    try:
        timestamp, signature = token.split('.', 1)
        age = time.time() - int(timestamp)
        expected = hmac.new(SESSION_KEY.encode(), timestamp.encode(), hashlib.sha256).hexdigest()
        return 0 <= age <= SESSION_MAX_AGE and hmac.compare_digest(signature, expected)
    except (ValueError, AttributeError):
        return False

def password_valid(password):
    try:
        algorithm, iterations, salt, expected = AUTH_HASH.split('$')
        if algorithm != 'pbkdf2_sha256': return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError): return False

@app.middleware('http')
async def require_auth(request: Request, call_next):
    if not AUTH_HASH: return await call_next(request)
    path = request.url.path
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        expected_origin = os.getenv('CRM_ORIGIN', '')
        if origin and origin != expected_origin:
            return JSONResponse({'detail':'Origin not allowed'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail':'Cross-site request rejected'}, status_code=403)
    public_photo = request.method in ('GET','HEAD') and re.fullmatch(r'/api/signature-images/(?:stefan-default|[0-9a-f]{64})\.png',path)
    if path not in ('/api/auth/status', '/api/auth/login') and not public_photo and not session_valid(request.cookies.get('cerato_session', '')):
        return JSONResponse({'detail':'Login required'}, status_code=401)
    return await call_next(request)

class LoginInput(BaseModel):
    password: str

@app.get('/api/auth/status')
def auth_status(request: Request):
    return {'required': bool(AUTH_HASH), 'authenticated': not AUTH_HASH or session_valid(request.cookies.get('cerato_session', ''))}

@app.post('/api/auth/login')
def login(data: LoginInput, request: Request, response: Response):
    if not AUTH_HASH: return {'ok': True}
    # Nginx should overwrite X-Real-IP rather than forwarding an untrusted header.
    ip = request.headers.get('x-real-ip', request.client.host if request.client else 'unknown')
    attempts = LOGIN_ATTEMPTS[ip]; current = time.monotonic()
    while attempts and attempts[0] < current - 900: attempts.popleft()
    if len(attempts) >= 10: raise HTTPException(429, 'Too many attempts. Try again later.')
    attempts.append(current)
    if not password_valid(data.password): raise HTTPException(401, 'Incorrect password')
    attempts.clear()
    timestamp = str(int(time.time()))
    signature = hmac.new(SESSION_KEY.encode(), timestamp.encode(), hashlib.sha256).hexdigest()
    response.set_cookie('cerato_session', timestamp+'.'+signature, max_age=SESSION_MAX_AGE, httponly=True, secure=SECURE_COOKIES, samesite='strict', path='/')
    return {'ok':True}

@app.post('/api/auth/logout')
def logout(response: Response):
    response.delete_cookie('cerato_session', path='/', secure=SECURE_COOKIES, httponly=True, samesite='strict')
    return {'ok':True}

def serialize(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
def mailbox(key):
    if key not in ('stefan', 'adrija'): raise HTTPException(400, 'Choose Stefan or Andrija')
    default = 'stefan@ceratosystems.com' if key == 'stefan' else 'andrija@ceratosystems.com'
    with Session() as db:
        saved = db.get(MailboxCredential, key)
        if saved:
            try:
                key_value = os.getenv('MAILBOX_ENCRYPTION_KEY','').encode()
                return saved.address, Fernet(key_value).decrypt(saved.encrypted_password.encode()).decode()
            except (ValueError, InvalidToken): raise HTTPException(503, 'Mailbox encryption key is missing or invalid.')
    address = os.getenv(key.upper()+'_EMAIL','').strip() or default
    password = os.getenv(key.upper()+'_PASSWORD','')
    if not password: raise HTTPException(400, f'{key.title()} mailbox is not connected.')
    return address, password
def valid_address(value): return bool(re.fullmatch(r'[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+', value or ''))
GENERIC_EMAIL_LOCALS = {'info','contact','office','sales','support','service','admin','administration','booking','planning','dispatch','operations','logistics','transport','export','import','reception','customer','customers','enquiries','enquiry','inquiries','inquiry','hello','mail','team','marketing','publicrelations','pr','accounting'}
def infer_email_type(lead):
    address = lead.get('person_email') or lead.get('company_email')
    if not address or '@' not in address: return 'unknown'
    local = re.sub(r'[^a-z0-9]', '', address.split('@', 1)[0].lower())
    if local in GENERIC_EMAIL_LOCALS: return 'general'
    if lead.get('person_email'):
        company = re.sub(r'[^a-z0-9]', '', (lead.get('company') or '').lower())
        if len(company) >= 5 and local.startswith(company[:7]): return 'unknown'
        return 'personal'
    return 'unknown'
def get_lead(db, id):
    obj = db.get(Lead,id)
    if not obj: raise HTTPException(404, 'Lead not found')
    return obj
class LeadCreate(BaseModel):
    company: str
    city: str = ''
    contact_name: str = ''
    person_email: str = ''
    company_email: str = ''
    email_type: str = 'unknown'

class LeadUpdate(BaseModel):
    status: str | None = None
    email_type: str | None = None
    notes: str | None = None
    research: str | None = None
    research_sources: str | None = None
class DraftInput(BaseModel):
    lead_id: int
    sender: str
    recipient: str
    subject: str
    body: str
class SuppressInput(BaseModel):
    email: str
    reason: str = 'Opted out'
class TemplateInput(BaseModel):
    subject: str = ''
    body: str

def template_id(sender: str):
    if sender not in ('stefan', 'adrija'): raise HTTPException(400, 'Choose Stefan or Andrija')
    return 1 if sender == 'stefan' else 2

def default_template_for(sender: str):
    return DEFAULT_TEMPLATE

def normalize_legacy_template(body, sender):
    # Upgrade the original introduction and remove only the known original footer.
    name = 'Stefan' if sender == 'stefan' else 'Andrija'
    body = body.replace('I’m ' + name + ' from Cerato Systems.', 'I’m {{sender_name}} from Cerato Systems.')
    stripped=body.rstrip()
    footers=('Best,\n{{sender_name}}\n{{sender_role}}, Cerato Systems',
             'Best,\nStefan\nCTO, Cerato Systems', 'Best,\nAndrija\nCBO, Cerato Systems',
             'Best,\nAndrija\nCerato Systems')
    for footer in footers:
        if stripped.endswith(footer) and (len(stripped)==len(footer) or stripped[-len(footer)-1]=='\n'):
            return stripped[:-len(footer)].rstrip()
    return body

@app.get('/api/template')
def get_template(sender: str = 'stefan'):
    key = template_id(sender)
    with Session.begin() as db:
        template = db.get(EmailTemplate, key)
        if not template:
            template = EmailTemplate(id=key, subject='', body=default_template_for(sender))
            db.add(template)
            db.flush()
        profile = signature_settings(db, sender)
        return {**serialize(template), 'body':normalize_legacy_template(template.body,sender), 'sender':sender, 'sender_name':profile['name'], 'sender_role':profile['title']}

@app.put('/api/template')
def update_template(data: TemplateInput, sender: str = 'stefan'):
    key = template_id(sender)
    if not data.body.strip(): raise HTTPException(400, 'Template body cannot be empty')
    if '\n' in data.subject or '\r' in data.subject: raise HTTPException(400, 'Subject must be a single line')
    with Session.begin() as db:
        template = db.get(EmailTemplate, key)
        if not template:
            template = EmailTemplate(id=key, subject=data.subject, body=normalize_legacy_template(data.body,sender))
            db.add(template)
        else:
            template.subject = data.subject
            template.body = normalize_legacy_template(data.body,sender)
        profile = signature_settings(db, sender)
        db.flush()
        return {**serialize(template), 'sender': sender, 'sender_name':profile['name'], 'sender_role':profile['title']}

@app.get('/api/health')
def health(): return {'ok': True}

def signature_settings(db, sender):
    template_id(sender)
    saved=db.get(SignatureSettings,sender)
    if saved: return {'name':saved.name,'title':saved.title,'email':saved.email,'photo_id':saved.photo_id}
    return {'name':'Stefan' if sender=='stefan' else 'Andrija','title':'CTO' if sender=='stefan' else 'CBO','email':('stefan' if sender=='stefan' else 'andrija')+'@ceratosystems.com','photo_id':'stefan-default' if sender=='stefan' else None}

def signature_html(profile):
    name=html.escape(profile['name']); title=html.escape(profile['title']); address=html.escape(profile['email'],quote=True)
    origin=os.getenv('CRM_ORIGIN','http://127.0.0.1:8000').rstrip('/')
    photo=profile.get('photo_id')
    photo_cell=(f'<td style="padding-right:18px;vertical-align:middle"><img src="{html.escape(origin,quote=True)}/api/signature-images/{photo}.png" alt="{name}" width="85" height="85" style="display:block;width:85px;height:85px;border-radius:50%"></td>' if photo else '')
    divider='padding-left:18px;border-left:1px solid #cbd5d1;' if photo else ''
    return (f'<table cellpadding="0" cellspacing="0" border="0" style="font-family:Arial,Helvetica,sans-serif;color:#1f2937"><tr><td colspan="2" style="padding-bottom:16px;font-size:14px">Best,</td></tr><tr>{photo_cell}<td style="{divider}vertical-align:middle"><div style="font-size:17px;font-weight:700;color:#111827;margin-bottom:5px">{name}</div><div style="font-size:13px;color:#4b5563;margin-bottom:7px">{title} <span style="color:#9ca3af">|</span> Cerato Systems</div><a href="mailto:{address}" style="font-size:13px;color:#374151;text-decoration:none">{address}</a><br><a href="https://www.ceratosystems.com" style="font-size:13px;color:#374151;text-decoration:none">www.ceratosystems.com</a></td></tr></table>')

class SignatureSettingsInput(BaseModel):
    name: str
    title: str
    email: str
    photo: str | None = None
    remove_photo: bool = False

@app.get('/api/signature-images/{photo_id}.png')
def signature_photo(photo_id: str):
    if photo_id=='stefan-default': data=(ROOT/'backend/assets/stefan.png').read_bytes()
    elif re.fullmatch(r'[0-9a-f]{64}',photo_id):
        with Session() as db:
            saved=db.get(SignaturePhoto,photo_id)
            if not saved: raise HTTPException(404,'Photo not found')
            data=base64.b64decode(saved.png_base64)
    else: raise HTTPException(404,'Photo not found')
    return Response(data,media_type='image/png',headers={'Cache-Control':'public, max-age=31536000, immutable','X-Content-Type-Options':'nosniff'})

@app.put('/api/mail/{sender}/signature-settings')
def update_signature_settings(sender: str, data: SignatureSettingsInput):
    template_id(sender)
    name=data.name.strip(); title=data.title.strip(); address=data.email.strip()
    if any(not v or len(v)>100 or any(ord(c)<32 for c in v) for v in (name,title)) or not valid_address(address):
        raise HTTPException(400,'Enter a valid name, title and email address.')
    photo_id=None; encoded=None
    if data.photo:
        try:
            if len(data.photo)>7000000: raise ValueError('Image is too large.')
            raw=base64.b64decode(data.photo,validate=True)
            image=Image.open(io.BytesIO(raw))
            if image.width*image.height>20000000 or image.format not in ('PNG','JPEG','WEBP','GIF'): raise ValueError('Unsupported image.')
            image=ImageOps.fit(ImageOps.exif_transpose(image).convert('RGB'),(340,340),method=Image.Resampling.LANCZOS,centering=(0.5,0.25)).convert('RGBA')
            mask=Image.new('L',(340,340)); ImageDraw.Draw(mask).ellipse((0,0,339,339),fill=255); image.putalpha(mask)
            output=io.BytesIO(); image.resize((170,170),Image.Resampling.LANCZOS).save(output,'PNG',optimize=True)
            blob=output.getvalue(); photo_id=hashlib.sha256(blob).hexdigest(); encoded=base64.b64encode(blob).decode()
        except (ValueError,binascii.Error,UnidentifiedImageError,OSError,Image.DecompressionBombError): raise HTTPException(400,'Choose a valid image under 5 MB.')
    with Session.begin() as db:
        current=signature_settings(db,sender); saved=db.get(SignatureSettings,sender)
        if not saved:
            saved=SignatureSettings(sender=sender,name=name,title=title,email=address,photo_id=current['photo_id']); db.add(saved)
        saved.name=name; saved.title=title; saved.email=address
        if data.remove_photo: saved.photo_id=None
        if photo_id:
            saved.photo_id=photo_id
            if not db.get(SignaturePhoto,photo_id): db.add(SignaturePhoto(id=photo_id,png_base64=encoded))
        db.flush(); result=signature_settings(db,sender)
    return {**result,'html':signature_html(result)}
def mailbox_preferences(db, sender):
    saved = db.get(MailboxPreferences, sender)
    if saved: result={'display_name':saved.display_name, 'signature':saved.signature}
    elif sender == 'stefan': result={'display_name':'Stefan Radovanovic','signature':'Best,\nStefan\nCTO, Cerato Systems'}
    elif sender == 'adrija': result={'display_name':'Andrija Ilic','signature':'Best,\nAndrija\nCBO, Cerato Systems'}
    else: raise HTTPException(400,'Choose Stefan or Andrija')
    rich=db.get(MailboxRichSignature,sender)
    result['signature_html']=rich.html if rich else rich_email.plain_to_html(result['signature'])
    return result

class MailboxPreferencesInput(BaseModel):
    display_name: str
    signature: str = ''
    signature_html: str | None = None

@app.put('/api/mail/{sender}/preferences')
def update_mailbox_preferences(sender: str, data: MailboxPreferencesInput):
    template_id(sender)
    name=data.display_name.strip()
    if not name or len(name)>120 or '\n' in name or '\r' in name or any(ord(c)<32 for c in name):
        raise HTTPException(400,'Enter a valid single-line sender name.')
    if len(data.signature)>10000: raise HTTPException(400,'Signature is too long.')
    try: signature_html=rich_email.clean_fragment(data.signature_html) if data.signature_html is not None else None
    except ValueError as exc: raise HTTPException(400,str(exc))
    signature=rich_email.plain_text(signature_html) if signature_html is not None else data.signature
    if len(signature)>10000: raise HTTPException(400,'Signature is too long.')
    with Session.begin() as db:
        saved=db.get(MailboxPreferences,sender)
        if not saved: db.add(MailboxPreferences(sender=sender,display_name=name,signature=signature))
        else: saved.display_name=name; saved.signature=signature
        rich=db.get(MailboxRichSignature,sender)
        if signature_html is not None:
            if rich: rich.html=signature_html
            else: db.add(MailboxRichSignature(sender=sender,html=signature_html))
        elif rich: db.delete(rich)
    return {'display_name':name,'signature':signature,'signature_html':signature_html if signature_html is not None else rich_email.plain_to_html(signature)}

@app.get('/api/settings')
def settings():
    with Session() as db:
        result=[]
        for sender in ('stefan','adrija'):
            saved=db.get(MailboxCredential,sender)
            default='stefan@ceratosystems.com' if sender=='stefan' else 'andrija@ceratosystems.com'
            profile=signature_settings(db,sender)
            result.append({'id':sender,'email':saved.address if saved else (os.getenv(sender.upper()+'_EMAIL','').strip() or default),'configured':bool(saved or os.getenv(sender.upper()+'_PASSWORD')),**mailbox_preferences(db,sender),'signature_settings':profile,'signature_preview':signature_html(profile)})
        return {'mailboxes':result,'mail_setup_available':bool(os.getenv('MAILBOX_ENCRYPTION_KEY'))}
@app.post('/api/import')
def import_leads():
    path = ROOT / 'road_groupage_companies.xlsx'
    if not path.exists(): raise HTTPException(404,'Spreadsheet not found')
    rows = load_workbook(path, read_only=True, data_only=True).active.iter_rows(min_row=2, values_only=True)
    added = skipped = 0
    with Session.begin() as db:
        existing = set(db.scalars(select(Lead.company)).all())
        for row in rows:
            if not row[0]: continue
            vals = [str(x).strip() if x is not None else None for x in row[:10]]
            if vals[0] in existing: skipped += 1; continue
            lead_data = dict(zip(['company','city','category','service_types','website','employees','linkedin','contact_name','person_email','company_email'],vals))
            db.add(Lead(**lead_data, email_type=infer_email_type(lead_data)))
            existing.add(vals[0]); added += 1
    return {'added':added,'skipped':skipped}
@app.post('/api/leads', status_code=201)
def create_lead(data: LeadCreate):
    company, city, contact = data.company.strip(), data.city.strip(), data.contact_name.strip()
    person_email, company_email = data.person_email.strip(), data.company_email.strip()
    if not company or any(len(value) > 250 for value in (company, city, contact)):
        raise HTTPException(400, 'Enter a company name and valid lead details.')
    if not (person_email or company_email) or any(value and not valid_address(value) for value in (person_email, company_email)):
        raise HTTPException(400, 'Enter at least one valid email address.')
    if data.email_type not in ('personal','general','unknown'):
        raise HTTPException(400, 'Invalid email type')
    addresses = [value.lower() for value in (person_email, company_email) if value]
    with Session.begin() as db:
        duplicate = db.scalar(select(Lead.id).where(func.lower(Lead.company) == company.lower(), or_(func.lower(Lead.person_email).in_(addresses), func.lower(Lead.company_email).in_(addresses))).limit(1))
        if duplicate: raise HTTPException(409, 'A lead with this company and email already exists.')
        lead = Lead(company=company, city=city or None, contact_name=contact or None, person_email=person_email or None, company_email=company_email or None, email_type=data.email_type, status='new')
        db.add(lead); db.flush()
        return {**serialize(lead), 'owner':None}

@app.get('/api/leads')
def leads(q: str = '', status: str = '', email_type: str = ''):
    with Session() as db:
        query = select(Lead)
        if q: query = query.where(or_(Lead.company.ilike('%'+q+'%'), Lead.city.ilike('%'+q+'%'), Lead.person_email.ilike('%'+q+'%'), Lead.company_email.ilike('%'+q+'%')))
        if status: query = query.where(Lead.status == status)
        if email_type:
            if email_type not in ('personal','general','unknown'): raise HTTPException(400, 'Invalid email type')
            query = query.where(Lead.email_type == email_type)
        owners = {}
        for lead_id, sender in db.execute(select(Message.lead_id, Message.sender).where(Message.direction == 'outbound').order_by(Message.created_at, Message.id)):
            owners.setdefault(lead_id, sender)
        return [{**serialize(x), 'owner': owners.get(x.id)} for x in db.scalars(query.order_by(Lead.company)).all()]
@app.get('/api/leads/{id}')
def lead(id: int):
    with Session() as db:
        obj=get_lead(db,id)
        history = db.scalars(select(Message).where(Message.lead_id==id).order_by(Message.created_at.desc(), Message.id.desc())).all()
        outbound = [x for x in reversed(history) if x.direction == 'outbound']
        assessments={a.message_id:a for a in db.scalars(select(OpeningAssessment).where(OpeningAssessment.message_id.in_([m.id for m in history]))).all()} if history else {}
        messages=[]
        for message in history:
            item=serialize(message); assessment=assessments.get(message.id)
            item['opening_assessment']={'score':assessment.score,'criteria':json.loads(assessment.criteria_json),'caveats':assessment.caveats,'opening':assessment.opening,'stale':assessment.body_hash!=hashlib.sha256(message.body.encode()).hexdigest() or assessment.opening not in message.body} if assessment else None
            messages.append(item)
        return {**serialize(obj), 'owner': outbound[0].sender if outbound else None, 'messages':messages}
@app.patch('/api/leads/{id}')
def update_lead(id: int, data: LeadUpdate):
    with Session.begin() as db:
        obj=get_lead(db,id)
        changes=data.model_dump(exclude_unset=True)
        if 'status' in changes and changes['status'] not in ('new','review','sent','replied','do_not_contact'): raise HTTPException(400,'Invalid status')
        if 'email_type' in changes and changes['email_type'] not in ('personal','general','unknown'): raise HTTPException(400,'Invalid email type')
        if obj.status == 'do_not_contact' and changes.get('status', 'do_not_contact') != 'do_not_contact': raise HTTPException(409, 'A stopped lead cannot be reactivated here')
        for k,v in changes.items(): setattr(obj,k,v)
        if obj.status == 'do_not_contact':
            for address in (obj.person_email,obj.company_email):
                if valid_address(address) and not db.get(Suppression,address.lower()): db.add(Suppression(email=address.lower(),reason='Lead marked do not contact'))
        return serialize(obj)
@app.get('/api/messages')
def messages(status: str = ''):
    with Session() as db:
        query=select(Message,Lead.company).join(Lead)
        if status: query=query.where(Message.status==status)
        return [{**serialize(m),'company':company} for m,company in db.execute(query.order_by(Message.created_at.desc())).all()]
@app.post('/api/drafts')
def draft(data: DraftInput):
    if data.sender not in ('stefan','adrija') or not valid_address(data.recipient) or not data.subject.strip() or not data.body.strip(): raise HTTPException(400,'Provide a sender, one valid recipient, subject and body')
    with Session.begin() as db:
        lead=db.scalar(select(Lead).where(Lead.id==data.lead_id).with_for_update())
        if not lead: raise HTTPException(404, 'Lead not found')
        if db.scalar(select(Message.id).where(Message.lead_id==lead.id, Message.status.in_(['draft','simulated','sent','sending','uncertain']), Message.direction=='outbound').limit(1)): raise HTTPException(409, 'This lead already has a draft or outgoing message')
        if lead.status in ('do_not_contact','replied'): raise HTTPException(409,'Lead is stopped or has replied')
        if db.get(Suppression,data.recipient.lower()): raise HTTPException(409,'Recipient is suppressed')
        obj=Message(**data.model_dump(),direction='outbound',status='draft'); db.add(obj); db.flush(); lead.status='review'
        return serialize(obj)
@app.put('/api/drafts/{id}')
def update_draft(id: int, data: DraftInput):
    if data.sender not in ('stefan','adrija') or not valid_address(data.recipient) or not data.subject.strip() or not data.body.strip(): raise HTTPException(400,'Provide a sender, one valid recipient, subject and body')
    with Session.begin() as db:
        obj=db.get(Message,id)
        if not obj or obj.lead_id != data.lead_id: raise HTTPException(404,'Draft not found')
        lead = db.scalar(select(Lead).where(Lead.id == obj.lead_id).with_for_update())
        obj = db.scalar(select(Message).where(Message.id == id).with_for_update().execution_options(populate_existing=True))
        if not obj or obj.status != 'draft': raise HTTPException(404,'Draft not found')
        if data.sender != obj.sender: raise HTTPException(409, 'This draft belongs to a different sender')
        if lead.status in ('do_not_contact','replied') or db.get(Suppression,data.recipient.lower()): raise HTTPException(409,'Lead or recipient is stopped')
        for k,v in data.model_dump().items(): setattr(obj,k,v)
        return serialize(obj)
@app.delete('/api/drafts/{id}')
def delete_draft(id:int):
    with Session.begin() as db:
        obj=db.get(Message,id)
        if not obj or obj.status != 'draft' or obj.direction != 'outbound': raise HTTPException(404,'Draft not found')
        lead=db.scalar(select(Lead).where(Lead.id==obj.lead_id).with_for_update())
        obj=db.scalar(select(Message).where(Message.id==id).with_for_update().execution_options(populate_existing=True))
        if not obj or obj.status != 'draft' or obj.direction != 'outbound': raise HTTPException(404,'Draft not found')
        db.delete(obj); db.flush()
        if lead.status=='review' and not db.scalar(select(Message.id).where(Message.lead_id==lead.id).limit(1)): lead.status='new'
        lead_id=lead.id; status=lead.status
    with JOB_LOCK:
        if JOBS.get(lead_id,{}).get('state') != 'running': JOBS.pop(lead_id,None)
    return {'ok':True,'lead_status':status}
class SendLeadDraftInput(BaseModel):
    include_signature: bool = True
    recipient: str
    subject: str
    body: str

@app.post('/api/drafts/{id}/send')
def send_lead_draft(id: int, data: SendLeadDraftInput):
    request_id = uuid.uuid4()
    with Session.begin() as db:
        draft = db.get(Message, id)
        if not draft: raise HTTPException(404, 'Draft not found')
        lead = db.scalar(select(Lead).where(Lead.id == draft.lead_id).with_for_update())
        draft = db.scalar(select(Message).where(Message.id == id).with_for_update().execution_options(populate_existing=True))
        if not draft or draft.direction != 'outbound' or draft.status != 'draft': raise HTTPException(409, 'This draft is no longer available to send. Check its current status.')
        if (draft.recipient, draft.subject, draft.body) != (data.recipient, data.subject, data.body): raise HTTPException(409, 'This draft changed. Review the latest version before sending.')
        if lead.status in ('sent', 'replied', 'do_not_contact'): raise HTTPException(409, 'This lead has already been contacted, replied, or is marked do not contact.')
        if not valid_address(draft.recipient) or not draft.subject.strip() or not draft.body.strip(): raise HTTPException(400, 'Provide a valid recipient, subject and message.')
        if re.search(r'{{\s*[^{}]+\s*}}', draft.subject + draft.body): raise HTTPException(400, 'Replace all template placeholders before sending.')
        if db.get(Suppression, draft.recipient.lower()): raise HTTPException(409, 'This recipient is marked do not contact.')
        if db.scalar(select(Message.id).where(Message.lead_id == lead.id, Message.id != id, Message.direction == 'outbound', Message.status.in_(['simulated','sent','sending','uncertain'])).limit(1)): raise HTTPException(409, 'Another outreach message is already recorded for this lead.')
        delivery = db.get(LeadDelivery, id)
        if delivery:
            delivery.request_id = str(request_id); delivery.include_signature = data.include_signature
        else:
            db.add(LeadDelivery(message_id=id, request_id=str(request_id), include_signature=data.include_signature))
        lead_id, sender, recipient, subject, body = lead.id, draft.sender, draft.recipient, draft.subject, draft.body
        draft.status = 'sending'; draft.error = None
    try:
        result = send_manual_mail(sender, ComposeMailInput(request_id=request_id, recipient=recipient, subject=subject, body=body, include_signature=data.include_signature))
    except HTTPException as exc:
        result = {'status':'failed', 'warning':str(exc.detail)}
    except Exception:
        result = {'status':'uncertain', 'warning':'The sending outcome is uncertain. Check your mailbox before trying again.'}
    status = result.get('status')
    if status not in ('sent','failed','uncertain'): status = 'uncertain'
    with Session.begin() as db:
        lead = db.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
        draft = db.scalar(select(Message).where(Message.id == id).with_for_update().execution_options(populate_existing=True))
        if draft.status == 'sent':
            if status != 'sent': result['warning']='The message was found in your Sent folder.'
            status = 'sent'  # Sent-folder synchronization confirmed the message during submission.
        else:
            draft.status = 'draft' if status == 'failed' else status
            draft.error = result.get('warning')
            if status == 'sent':
                draft.message_id = result.get('message_id'); draft.sent_at = now()
                if lead.status not in ('replied','do_not_contact'): lead.status = 'sent'
    return {'status':status, 'warning':result.get('warning')}

# Jobs deliberately stay in memory: a service restart interrupts them instead of resending.
JOBS = {}
JOB_LOCK = threading.Lock()
JOB_POOL = ThreadPoolExecutor(max_workers=1)
class GenerateInput(BaseModel):
    sender: str

def run_generation(id: int, sender: str, lead_snapshot: dict, template: dict):
    try:
        result = generate(lead_snapshot, template, sender)
        sources = result.get('sources', [])
        if not isinstance(sources, list): raise ValueError('Invalid research sources')
        urls = [u.strip() for u in sources if isinstance(u, str) and re.match(r'^https?://[^\s]+$', u.strip())]
        research = str(result.get('research') or '').strip()[:12000] or 'No verified company research was found.'
        raw_criteria=result.get('criteria') if isinstance(result.get('criteria'),dict) else {}
        criteria={}
        for key in CRITERIA:
            item=raw_criteria.get(key) if isinstance(raw_criteria.get(key),dict) else {}
            value=item.get('score'); value=value if isinstance(value,int) and not isinstance(value,bool) else 0
            criteria[key]={'score':max(0,min(5,value)),'reason':str(item.get('reason') or 'Not assessed.')[:500]}
        caveats=str(result.get('caveats') or '').strip()[:2000]
        if not urls:
            criteria['evidence']={'score':0,'reason':'No supporting source was available.'}
            caveats=(caveats+'\n' if caveats else '')+'No supporting source was available; review the opening before use.'
        with Session.begin() as db:
            obj = db.scalar(select(Lead).where(Lead.id == id).with_for_update())
            if not obj or obj.status != 'new' or db.scalar(select(Message.id).where(Message.lead_id == id).limit(1)):
                raise ValueError('Lead changed while research was running; no draft was created')
            obj.research = research
            obj.research_sources = '\n'.join(urls[:20])
            recipient = next((str(x).strip() for x in (obj.person_email, obj.company_email) if x and valid_address(str(x).strip())), None)
            if not recipient: raise ValueError('This lead needs a single valid email address to create a draft')
            if db.get(Suppression, recipient.lower()): raise ValueError('This recipient is marked do not contact')
            first = str(result.get('first_name') or '').strip() or ((obj.contact_name or '').split() or ['there'])[0]
            sentence = str(result.get('personalized_sentence') or '').strip()
            if not sentence:
                sentence='How do you currently coordinate transport bookings and shipment updates?'
                for key in CRITERIA: criteria[key]={'score':0,'reason':'A generic fallback was used.'}
                caveats=(caveats+'\n' if caveats else '')+'No personalized opening was returned; a generic question was used.'
            subject = str(result.get('subject') or '').strip()
            if not subject or '\n' in subject or '\r' in subject or len(subject)>250: raise ValueError('The agent did not provide a valid subject. Please try again.')
            replacements={'{{first_name}}':first, '{{personalized_sentence}}':sentence, '{{company_name}}':obj.company, '{{sender_name}}':template['sender_name'], '{{sender_role}}':template['sender_role']}
            body=template['body']
            for key,value in replacements.items(): body=body.replace(key,value); subject=subject.replace(key,value)
            if re.search(r'{{\s*[^{}]+\s*}}',body+subject): raise ValueError('The template contains unresolved placeholders')
            message=Message(lead_id=id,direction='outbound',status='draft',sender=sender,recipient=recipient,subject=subject,body=body)
            db.add(message); db.flush(); obj.status='review'
            score=sum(v['score'] for v in criteria.values())*4
            if not urls: score=min(score,49)
            db.add(OpeningAssessment(message_id=message.id,score=score,criteria_json=json.dumps(criteria),caveats=caveats,opening=sentence,body_hash=hashlib.sha256(body.encode()).hexdigest()))
            outcome = {'state':'completed','message':'Draft created. Review the opening and its quality score.'}
        with JOB_LOCK: JOBS[id] = outcome
    except Exception as exc:
        with JOB_LOCK: JOBS[id] = {'state':'failed', 'message':str(exc)[:500]}

class ResetActivityInput(BaseModel):
    confirmation: str

@app.post('/api/reset-simulated-activity')
def reset_simulated_activity(data: ResetActivityInput):
    if data.confirmation != 'RESET SIMULATED ACTIVITY': raise HTTPException(400, 'Confirmation text does not match')
    with JOB_LOCK:
        if any(job.get('state') == 'running' for job in JOBS.values()): raise HTTPException(409, 'Wait for the current research job to finish')
        with Session.begin() as db:
            leads = db.scalars(select(Lead).with_for_update()).all()
            protected = set(db.scalars(select(Message.lead_id).where(or_(Message.direction != 'outbound', Message.status.not_in(['draft', 'simulated'])))).all())
            result = db.execute(delete(Message).where(Message.direction == 'outbound', Message.status.in_(['draft', 'simulated'])))
            reset_count = 0
            for lead in leads:
                if lead.id in protected or lead.status in ('do_not_contact', 'sent', 'replied'): continue
                lead.status = 'new'
                lead.research = None
                lead.research_sources = None
                lead.notes = None
                reset_count += 1
        JOBS.clear()
    return {'leads_reset':reset_count,'messages_removed':result.rowcount,'protected_leads':len(protected)}

@app.post('/api/leads/{id}/generate')
def generate_lead(id: int, data: GenerateInput):
    template_id(data.sender)
    with JOB_LOCK:
        if any(x.get('state') == 'running' for x in JOBS.values()): raise HTTPException(409,'Another research job is running; try again when it finishes')
        with Session() as db:
            obj=get_lead(db,id)
            if obj.status != 'new' or db.scalar(select(Message.id).where(Message.lead_id==id).limit(1)): raise HTTPException(409,'Only uncontacted new leads can be researched')
            snapshot={k:getattr(obj,k) for k in ('id','company','city','category','website','linkedin','contact_name','person_email','company_email','research','research_sources')}
        template=get_template(data.sender)
        JOBS[id]={'state':'running','message':'Researching this lead and preparing a draft…'}
        JOB_POOL.submit(run_generation,id,data.sender,snapshot,template)
    return JOBS[id]

@app.get('/api/leads/{id}/generation')
def generation_status(id: int):
    with Session() as db: get_lead(db,id)
    with JOB_LOCK: return JOBS.get(id, {'state':'idle','message':''})

@app.post('/api/suppressions')
def suppress(data:SuppressInput):
    address=data.email.strip().lower()
    if not valid_address(address): raise HTTPException(400,'Invalid email')
    with Session.begin() as db:
        if not db.get(Suppression,address): db.add(Suppression(email=address,reason=data.reason))
    return {'ok':True}
def decoded(value):
    try: return str(make_header(decode_header(value or '')))
    except Exception: return value or ''
def plain_body(msg):
    parts=msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type()=='text/plain' and part.get_content_disposition()!='attachment':
            payload=part.get_payload(decode=True)
            if payload: return payload.decode(part.get_content_charset() or 'utf-8',errors='replace')[:50000]
    return ''
class ComposeMailInput(BaseModel):
    request_id: uuid.UUID
    recipient: str
    subject: str
    body: str
    html_body: str | None = None
    include_signature: bool = False
    in_reply_to: str = ''
    references: str = ''

class LeadReplyInput(BaseModel):
    request_id: uuid.UUID
    body: str
    html_body: str | None = None
    include_signature: bool = True

def lead_reply_context(db, lead_id, message_id):
    lead=get_lead(db,lead_id)
    if lead.status=='do_not_contact': raise HTTPException(409,'This lead is marked do not contact.')
    messages=db.scalars(select(Message).where(Message.lead_id==lead_id)).all()
    target=next((m for m in messages if m.id==message_id and m.direction=='inbound' and m.status=='received'),None)
    if not target or not target.message_id or not mail_client.message_ids(target.message_id): raise HTTPException(404,'This reply is not available for a threaded response.')
    if target.sender not in ('stefan','adrija'): raise HTTPException(400,'This message has no connected sender account.')
    by_id={m.message_id:m for m in messages if m.message_id and m.sender==target.sender}
    ancestor=target; visited=set(); chain=[]; recipient=None
    while ancestor and ancestor.message_id not in visited and len(chain)<30:
        visited.add(ancestor.message_id);chain.append(ancestor.message_id)
        if ancestor.direction=='outbound' and ancestor.status=='sent':recipient=ancestor.recipient;break
        refs=mail_client.message_ids(ancestor.in_reply_to or '')
        ancestor=next((by_id[ref] for ref in refs if ref in by_id),None)
    if not recipient:
        candidates={m.recipient.lower() for m in messages if m.sender==target.sender and m.direction=='outbound' and m.status=='sent' and m.recipient}
        if len(candidates)!=1:raise HTTPException(409,'The original recipient could not be determined. Reply from Inbox instead.')
        recipient=next(iter(candidates))
    if not valid_address(recipient) or db.get(Suppression,recipient.lower()):raise HTTPException(409,'This recipient is marked do not contact or has an invalid address.')
    subject=target.subject or '(No subject)'
    if not re.match(r'^\s*re\s*:',subject,re.I):subject='Re: '+subject
    return {'sender':target.sender,'recipient':recipient,'subject':subject,'in_reply_to':target.message_id,'references':' '.join(reversed(chain[1:]))}

@app.get('/api/leads/{lead_id}/messages/{message_id}/reply')
def prepare_lead_reply(lead_id:int,message_id:int):
    with Session() as db:return lead_reply_context(db,lead_id,message_id)

@app.post('/api/leads/{lead_id}/messages/{message_id}/reply')
def send_lead_reply(lead_id:int,message_id:int,data:LeadReplyInput):
    with Session() as db:context=lead_reply_context(db,lead_id,message_id)
    try:
        result=send_manual_mail(context['sender'],ComposeMailInput(request_id=data.request_id,recipient=context['recipient'],subject=context['subject'],body=data.body,html_body=data.html_body,include_signature=data.include_signature,in_reply_to=context['in_reply_to'],references=context['references']))
    except HTTPException:raise
    except Exception:return {'status':'uncertain','warning':'The sending outcome is uncertain. Check your mailbox before trying again.'}
    if result.get('status')=='sent':
        try:
            with Session.begin() as db:
                lead=db.scalar(select(Lead).where(Lead.id==lead_id).with_for_update())
                if not db.scalar(select(Message.id).where(Message.message_id==result['message_id'])):
                    db.add(Message(lead_id=lead_id,direction='outbound',status='sent',sender=context['sender'],recipient=context['recipient'],subject=context['subject'],body=data.body,message_id=result['message_id'],in_reply_to=context['in_reply_to'],sent_at=now()))
                submission=db.get(MailSubmission,str(data.request_id))
                later_reply=submission and db.scalar(select(Message.id).where(Message.lead_id==lead_id,Message.direction=='inbound',Message.status=='received',Message.created_at>submission.created_at).limit(1))
                if lead.status=='replied' and not later_reply and not db.get(Suppression,context['recipient'].lower()):lead.status='sent'
        except Exception:
            logging.getLogger(__name__).exception('Could not immediately record a lead reply')
            result['warning']=result.get('warning') or 'Email sent. Conversation history will update when the mailbox syncs.'
    return result


def mail_connection(sender):
    address,password = mailbox(sender)
    return mail_client.connect(address,password,os.getenv('IMAP_HOST','imap.purelymail.com'),int(os.getenv('IMAP_PORT','993')))

class MailboxSetupInput(BaseModel):
    address: str
    password: str

@app.put('/api/mail/{sender}/connection')
def connect_mailbox(sender: str, data: MailboxSetupInput):
    template_id(sender)
    address=data.address.strip()
    if not valid_address(address) or not data.password or len(data.password)>1024: raise HTTPException(400,'Enter a valid email address and mailbox password.')
    try: cipher=Fernet(os.getenv('MAILBOX_ENCRYPTION_KEY','').encode())
    except ValueError: raise HTTPException(503,'Mailbox encryption is not configured on the server.')
    try:
        with mail_client.connect(address,data.password,os.getenv('IMAP_HOST','imap.purelymail.com'),int(os.getenv('IMAP_PORT','993'))) as client:
            mail_client.select_folder(client,'inbox')
    except Exception: raise HTTPException(400,'Could not connect to Purelymail. Check the email and password.')
    encrypted=cipher.encrypt(data.password.encode()).decode()
    with Session.begin() as db:
        saved=db.get(MailboxCredential,sender)
        if not saved: db.add(MailboxCredential(sender=sender,address=address,encrypted_password=encrypted))
        else: saved.address=address; saved.encrypted_password=encrypted
    return {'ok':True,'address':address}

@app.get('/api/mail/{sender}/messages')
def mailbox_messages(sender: str, folder: str = 'inbox'):
    try:
        with mail_connection(sender) as client: return mail_client.list_messages(client,folder)
    except HTTPException: raise
    except mail_client.MailboxError as exc: raise HTTPException(400,str(exc))
    except Exception: raise HTTPException(502,'Could not connect to the mailbox. Check the Purelymail settings and credentials.')

@app.get('/api/mail/{sender}/messages/{uid}')
def mailbox_message(sender: str, uid: str, folder: str = 'inbox', uidvalidity: str = ''):
    try:
        with mail_connection(sender) as client: return mail_client.read_message(client,folder,uid,uidvalidity)
    except HTTPException: raise
    except mail_client.MailboxError as exc: raise HTTPException(400,str(exc))
    except Exception: raise HTTPException(502,'Could not read this message from the mailbox.')

@app.get('/api/mail/{sender}/messages/{uid}/conversation')
def mailbox_conversation(sender: str, uid: str, folder: str = 'inbox', uidvalidity: str = ''):
    try:
        with mail_connection(sender) as client: return mail_client.read_conversation(client,folder,uid,uidvalidity)
    except HTTPException: raise
    except mail_client.MailboxError as exc: raise HTTPException(400,str(exc))
    except Exception: raise HTTPException(502,'Could not load this conversation from the mailbox.')

@app.post('/api/mail/{sender}/send')
def send_manual_mail(sender: str, data: ComposeMailInput):
    address,password = mailbox(sender)
    recipient = data.recipient.strip()
    if not valid_address(recipient) or not data.subject.strip() or len(data.subject)>300 or len(data.body)>100000:
        raise HTTPException(400,'Provide one valid recipient, a subject and a message.')
    in_reply_to=mail_client.unfold_header(data.in_reply_to)
    references=mail_client.unfold_header(data.references)
    if '\n' in data.subject or '\r' in data.subject or in_reply_to is None or references is None:
        raise HTTPException(400,'Invalid message header.')
    try: html_body=rich_email.clean_fragment(data.html_body) if data.html_body is not None else None
    except ValueError as exc: raise HTTPException(400,str(exc))
    if not data.body.strip() and (not html_body or '<img' not in html_body): raise HTTPException(400,'Provide a message.')
    with Session() as db:
        display_name=mailbox_preferences(db,sender)['display_name']
        profile=signature_settings(db,sender) if data.include_signature else None
    if profile:
        html_body=(html_body if html_body is not None else rich_email.plain_to_html(data.body))+'<br><br>'+signature_html(profile)
    candidate_id=make_msgid()
    msg=EmailMessage(); msg['From']=formataddr((display_name,address)); msg['To']=recipient; msg['Subject']=data.subject; msg['Message-ID']=candidate_id; msg['Date']=formatdate(localtime=False)
    if in_reply_to:
        msg['In-Reply-To']=in_reply_to
        msg['References']=(references+' '+in_reply_to).strip()
    plain=(rich_email.plain_text(rich_email.clean_fragment(data.html_body)) if data.html_body is not None else data.body)
    if profile: plain+='\n\nBest,\n'+profile['name']+'\n'+profile['title']+' | Cerato Systems\n'+profile['email']+'\nwww.ceratosystems.com'
    msg.set_content(plain or '[Image]')
    if html_body is not None:
        rendered,images=rich_email.mime_html(html_body)
        msg.add_alternative(rendered,subtype='html')
        for cid,kind,content in images: msg.get_payload()[-1].add_related(content,maintype='image',subtype=kind,cid=cid,disposition='inline')
    if os.getenv('MAIL_TRANSPORT','smtp').lower()=='relay' and len(base64.b64encode(msg.as_bytes(policy=SMTP)))>200000:
        raise HTTPException(400,'Email is too large for the sending relay. Use smaller images or shorten the message.')
    with Session.begin() as db:
        existing=db.get(MailSubmission,str(data.request_id))
        if existing:
            stored_html=db.get(MailRichContent,str(data.request_id))
            if existing.sender!=sender or existing.recipient!=recipient or existing.subject!=data.subject or existing.body!=data.body or (stored_html.html if stored_html else None)!=html_body: raise HTTPException(409,'This request ID was already used for a different message.')
            return {'status':existing.status,'message_id':existing.message_id,'warning':existing.error}
        if db.get(Suppression,recipient.lower()): raise HTTPException(409,'This address is marked do not contact.')
        item=MailSubmission(id=str(data.request_id),sender=sender,recipient=recipient,subject=data.subject,body=data.body,status='sending',message_id=candidate_id)
        db.add(item)
        if html_body is not None: db.add(MailRichContent(submission_id=str(data.request_id),html=html_body))
        db.flush(); message_id=item.message_id
    # A failure before submission is definitely not sent. Once submission begins,
    # network failures can be ambiguous and must never trigger an automatic retry.
    def record_failure(status, message):
        with Session.begin() as db:
            item=db.get(MailSubmission,str(data.request_id)); item.status=status; item.error=message
        return {'status':status,'message_id':message_id,'warning':message}
    transport = os.getenv('MAIL_TRANSPORT', 'smtp').lower()
    if transport == 'relay':
        outcome, error = mail_relay.send(os.getenv('RELAY_URL',''),os.getenv('RELAY_TOKEN',''),address,password,recipient,msg.as_bytes(policy=SMTP))
        if outcome != 'sent': return record_failure(outcome,error)
    elif transport == 'smtp':
        smtp = None
        try:
            smtp = smtplib.SMTP_SSL(os.getenv('SMTP_HOST','smtp.purelymail.com'),int(os.getenv('SMTP_PORT','465')),context=ssl.create_default_context(),timeout=15)
            smtp.login(address,password)
        except smtplib.SMTPAuthenticationError:
            if smtp:
                try: smtp.close()
                except Exception: pass
            return record_failure('failed','Not sent: Purelymail rejected the mailbox login. Check the account credentials.')
        except Exception:
            if smtp:
                try: smtp.close()
                except Exception: pass
            return record_failure('failed','Not sent: the CRM could not connect to the outgoing mail server. Check the server network and SMTP settings.')
        try:
            smtp.send_message(msg)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
            return record_failure('failed','Not sent: the outgoing mail server rejected the message.')
        except Exception:
            return record_failure('uncertain','The sending outcome is uncertain. Check your mailbox before trying again.')
        finally:
            try: smtp.quit()
            except Exception: smtp.close()
    else:
        return record_failure('failed','Not sent: the configured email transport is not supported.')
    warning=None
    try:
        with mail_connection(sender) as client:
            sent=mail_client.folder_name(client,'sent')
            status,_=client.append('\"'+sent.replace('\\','\\\\').replace('\"','\\\"')+'\"','(\\Seen)',imaplib.Time2Internaldate(time.time()),msg.as_bytes())
            if status!='OK': raise RuntimeError('Sent folder append failed')
    except Exception: warning='The server accepted the email, but the copy could not be saved to Sent.'
    with Session.begin() as db:
        item=db.get(MailSubmission,str(data.request_id)); item.status='sent'; item.error=warning
    return {'status':'sent','message_id':message_id,'warning':warning}

# Mailbox status synchronization runs independently of the web interface.
SYNC_INTERVAL_SECONDS=120
SYNC_POOL=ThreadPoolExecutor(max_workers=1)
SYNC_STATE_LOCK=threading.Lock()
SYNC_STOP=threading.Event()
SYNC_RUNNING=False

def queue_mail_sync():
    global SYNC_RUNNING
    with SYNC_STATE_LOCK:
        if SYNC_RUNNING:return False
        SYNC_RUNNING=True
    def run():
        global SYNC_RUNNING
        try:
            from backend.lead_sync import sync_sender
            for sender in ('stefan','adrija'):
                try:sync_sender(sender)
                except Exception as exc:logging.getLogger(__name__).warning('Mailbox synchronization failed for %s: %s',sender,type(exc).__name__)
        finally:
            with SYNC_STATE_LOCK:SYNC_RUNNING=False
    try:SYNC_POOL.submit(run)
    except Exception:
        with SYNC_STATE_LOCK:SYNC_RUNNING=False
        raise
    return True

@app.on_event('startup')
def start_mail_sync():
    if os.getenv('CRM_MAIL_SYNC_ENABLED','true').lower() in ('0','false','no'):return
    SYNC_STOP.clear()
    def schedule():
        if SYNC_STOP.wait(5):return
        while not SYNC_STOP.is_set():
            queue_mail_sync()
            if SYNC_STOP.wait(SYNC_INTERVAL_SECONDS):break
    threading.Thread(target=schedule,name='mailbox-lead-sync',daemon=True).start()

@app.on_event('shutdown')
def stop_mail_sync():
    SYNC_STOP.set()

@app.get('/api/mail-sync/status')
def mail_sync_status():
    accounts=settings()['mailboxes']
    with Session() as db:
        rows=db.scalars(select(MailboxSyncCursor)).all()
        states={(row.sender,row.folder):row for row in rows}
        result=[]
        for account in accounts:
            folders={}
            for folder in ('sent','inbox'):
                row=states.get((account['id'],folder))
                folders[folder]={'checked_at':row.checked_at if row else None,'error':row.error if row else None,'has_more':row.has_more if row else False}
            result.append({'id':account['id'],'configured':account['configured'],'folders':folders})
    with SYNC_STATE_LOCK:running=SYNC_RUNNING
    return {'running':running,'mailboxes':result}

@app.post('/api/mail-sync/refresh')
def refresh_mail_sync():
    return {'started':queue_mail_sync()}

@app.post('/api/sync/{sender}')
def sync(sender:str):
    from backend.lead_sync import sync_sender
    return sync_sender(sender)
