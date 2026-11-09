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

def unfold_header(value):
    """Unfold legal RFC header continuations and reject stray line breaks."""
    value=re.sub(r'\r?\n[ \t]+',' ',str(value or '')).strip()
    if '\r' in value or '\n' in value: return None
    return re.sub(r'[ \t]+',' ',value)

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
    references=' '.join(filter(None,(message.get('In-Reply-To',''),message.get('References',''))))
    return {'from':text_header(message.get('From')),'to':text_header(message.get('To')),'cc':text_header(', '.join(message.get_all('Cc',[]))),'subject':text_header(message.get('Subject')) or '(No subject)','date':date,'message_id':unfold_header(message.get('Message-ID')) or '','in_reply_to':unfold_header(message.get('In-Reply-To')) or '', 'references':unfold_header(references) or '', 'auto_submitted':unfold_header(message.get('Auto-Submitted')) or '', 'precedence':unfold_header(message.get('Precedence')) or '', 'reply_to':getaddresses(message.get_all('Reply-To',[]) or message.get_all('From',[]))[0][1] if (message.get_all('Reply-To',[]) or message.get_all('From',[])) else ''}

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
    return fetch_headers(client,ids,validity)

def fetch_headers(client,ids,validity):
    if not ids: return []
    fields='(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES AUTO-SUBMITTED PRECEDENCE)])'
    status,rows=client.uid('fetch',b','.join(ids),fields)
    if status!='OK': raise MailboxError('Could not load mailbox messages')
    entries={}
    rows=rows or []
    for index,row in enumerate(rows):
        if not isinstance(row,tuple) or len(row)<2 or not isinstance(row[0],bytes): continue
        metadata=row[0]
        for continuation in rows[index+1:]:
            if isinstance(continuation,tuple): break
            if isinstance(continuation,bytes): metadata+=b' '+continuation
        match=re.search(rb'\bUID\s+(\d+)\b',metadata,re.I)
        if not match or match.group(1) not in ids: continue
        uid=match.group(1)
        entry=parsed(email.message_from_bytes(row[1])); entry.update(uid=uid.decode(),uidvalidity=validity,seen=bool(re.search(rb'\\Seen\b',metadata,re.I))); entries[uid]=entry
    return [entries[uid] for uid in ids if uid in entries]

def read_message(client,folder,uid,uidvalidity):
    _,actual=select_folder(client,folder)
    if not uidvalidity or uidvalidity != actual: raise MailboxError('Mailbox changed; refresh the message list')
    return read_selected_message(client,uid,actual)

def read_selected_message(client,uid,actual):
    if not uid.isdigit(): raise MailboxError('Invalid message identifier')
    status,rows=client.uid('fetch',uid,'(BODY.PEEK[])')
    row=next((x for x in rows if isinstance(x,tuple)),None) if status=='OK' else None
    if not row: raise MailboxError('Message is no longer available')
    message=email.message_from_bytes(row[1]); entry=parsed(message); entry.update(body=body_text(message),uid=uid,uidvalidity=actual)
    entry['attachments']=[text_header(p.get_filename()) for p in message.walk() if p.get_filename()][:30]
    return entry

def message_ids(value):
    return set(re.findall(r'<[^<>\s]{1,512}>',value or ''))

def read_conversation(client,folder,uid,uidvalidity,limit=12):
    selected=read_message(client,folder,uid,uidvalidity)
    selected['folder']=folder
    root_ids=message_ids(selected.get('message_id'))
    if not root_ids:
        return {'messages':[selected], 'notice':'This message has no thread identifier, so related messages could not be located.'}
    known=root_ids | message_ids(selected.get('references'))
    candidates=[]; unavailable=[]
    for source in ('inbox','sent'):
        try:
            candidates.extend({**item,'folder':source} for item in list_messages(client,source))
        except Exception:
            unavailable.append(source.title())
    related=[]; remaining=candidates[:]; changed=True
    while changed:
        changed=False; unmatched=[]
        for item in remaining:
            own=message_ids(item.get('message_id')); refs=message_ids(item.get('references'))
            if own and (own & known or refs & known):
                related.append(item); known.update(own | refs); changed=True
            else: unmatched.append(item)
        remaining=unmatched
    def order(item):
        try: return parsedate_to_datetime(item.get('date')).timestamp()
        except (ValueError,TypeError,OverflowError):
            try:
                from datetime import datetime
                return datetime.fromisoformat(item.get('date') or '').timestamp()
            except (ValueError,TypeError,OverflowError): return 0
    unique={selected['message_id']:selected}
    for item in related:
        mid=item.get('message_id')
        if mid and mid not in unique: unique[mid]=item
    headers=sorted(unique.values(),key=order)
    truncated=len(headers)>limit
    if truncated:
        headers=headers[-limit:]
        if not any(item.get('message_id')==selected['message_id'] for item in headers): headers=[selected]+headers[1:]
    results={selected['message_id']:selected}; missing=False
    for source in ('inbox','sent'):
        to_read=[item for item in headers if item.get('folder')==source and item.get('message_id')!=selected['message_id']]
        if not to_read: continue
        try:
            _,actual=select_folder(client,source)
            for item in to_read:
                if not item.get('uidvalidity') or item['uidvalidity']!=actual: missing=True; continue
                try:
                    message=read_selected_message(client,item['uid'],actual); message['folder']=source
                    if message.get('message_id')==item.get('message_id'): results[item['message_id']]=message
                    else: missing=True
                except Exception: missing=True
        except Exception: unavailable.append(source.title())
    messages=[results[item['message_id']] for item in headers if item.get('message_id') in results]
    notice='Shows matching messages from the latest 50 in Inbox and Sent.'
    if truncated: notice+=' Only the most recent matches are shown.'
    if unavailable: notice+=' Could not read: '+', '.join(sorted(set(unavailable)))+'.'
    if missing: notice+=' Some messages could not be loaded.'
    return {'messages':messages,'notice':notice}
