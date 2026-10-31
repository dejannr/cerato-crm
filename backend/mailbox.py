"""Small read-only IMAP client for the CRM mailbox view."""
import email
import imaplib
import re
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

class MailboxError(Exception): pass

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(); self.chunks=[]; self.hidden=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','head'): self.hidden+=1
        if not self.hidden and tag in ('br','p','div','tr','li','h1','h2','h3'): self.chunks.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script','style','head') and self.hidden: self.hidden-=1
        if not self.hidden and tag in ('p','div','tr','li'): self.chunks.append('\n')
    def handle_data(self, data):
        if not self.hidden: self.chunks.append(data)

def text_header(value):
    try: return str(make_header(decode_header(value or '')))
    except Exception: return str(value or '')

def safe_text(part):
    data=part.get_payload(decode=True)
    if data is None: return ''
    try: return data.decode(part.get_content_charset() or 'utf-8',errors='replace')
    except LookupError: return data.decode('utf-8',errors='replace')

def body_text(message):
    parts=list(message.walk()) if message.is_multipart() else [message]
    content=[p for p in parts if not p.is_multipart() and p.get_content_disposition() != 'attachment' and not p.get_filename()]
    for part in content:
        if part.get_content_type()=='text/plain': return safe_text(part)[:100000]
    for part in content:
        if part.get_content_type()=='text/html':
            parser=TextExtractor(); parser.feed(safe_text(part)[:500000]); return re.sub(r'\n{3,}','\n\n',''.join(parser.chunks)).strip()[:100000]
    return ''

def parsed(message):
    date=None
    try: date=parsedate_to_datetime(message.get('Date')).isoformat()
    except (ValueError,TypeError,OverflowError): pass
    return {'from':text_header(message.get('From')),'to':text_header(message.get('To')),'subject':text_header(message.get('Subject')) or '(No subject)','date':date,'message_id':str(message.get('Message-ID') or ''),'references':str(message.get('References') or ''),'reply_to':getaddresses(message.get_all('Reply-To',[]) or message.get_all('From',[]))[0][1] if (message.get_all('Reply-To',[]) or message.get_all('From',[])) else ''}

def connect(address,password,host,port):
    client=imaplib.IMAP4_SSL(host,port,timeout=20)
    try: client.login(address,password)
    except Exception:
        try: client.logout()
        except Exception: pass
        raise
    return client

def folder_name(client,folder):
    if folder=='inbox': return 'INBOX'
    if folder!='sent': raise MailboxError('Unknown mailbox folder')
    status,items=client.list()
    if status!='OK': raise MailboxError('Could not list mailbox folders')
    candidates=[]
    for item in items or []:
        if not item: continue
        line=item.decode('utf-8','replace') if isinstance(item,bytes) else str(item)
        match=re.match(r'^\(([^)]*)\)\s+(?:"[^"]*"|NIL)\s+(.+)$',line)
        if not match: continue
        flags,name=match.groups(); name=name.strip().strip('"')
        if '\\sent' in flags.lower().split(): return name
        if name.lower() in ('sent','sent messages','sent items','inbox.sent'): candidates.append(name)
    if candidates: return candidates[0]
    raise MailboxError('Could not find the Sent folder on this mailbox')

def select_folder(client,folder,readonly=True):
    name=folder_name(client,folder)
    status,_=client.select('"'+name.replace('\\','\\\\').replace('"','\\"')+'"',readonly=readonly)
    if status!='OK': raise MailboxError('Could not open mailbox folder')
    _,validity=client.response('UIDVALIDITY')
    return name,validity[0].decode() if validity and validity[0] else ''

def list_messages(client,folder,limit=50):
    _,validity=select_folder(client,folder)
    status,results=client.uid('search',None,'ALL')
    if status!='OK': raise MailboxError('Could not search mailbox')
    ids=(results[0] or b'').split()[-limit:][::-1]
    messages=[]
    for uid in ids:
        status,rows=client.uid('fetch',uid,'(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])')
        if status!='OK': continue
        row=next((x for x in rows if isinstance(x,tuple)),None)
        if not row: continue
        entry=parsed(email.message_from_bytes(row[1])); entry.update(uid=uid.decode(),uidvalidity=validity,seen=b'\\Seen' in row[0]); messages.append(entry)
    return messages

def read_message(client,folder,uid,uidvalidity):
    _,actual=select_folder(client,folder)
    if not uidvalidity or uidvalidity != actual: raise MailboxError('Mailbox changed; refresh the message list')
    if not uid.isdigit(): raise MailboxError('Invalid message identifier')
    status,rows=client.uid('fetch',uid,'(BODY.PEEK[])')
    row=next((x for x in rows if isinstance(x,tuple)),None) if status=='OK' else None
    if not row: raise MailboxError('Message is no longer available')
    message=email.message_from_bytes(row[1]); entry=parsed(message); entry.update(body=body_text(message),uid=uid,uidvalidity=actual)
    entry['attachments']=[text_header(p.get_filename()) for p in message.walk() if p.get_filename()][:30]
    return entry
