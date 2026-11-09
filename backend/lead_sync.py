"""Read-only mailbox synchronization for lead conversation status."""
import logging
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text
from backend import mailbox as mail_client

log=logging.getLogger(__name__)
BATCH_SIZE=100
MAX_PER_FOLDER=500


def addresses(*headers):
    return {address.strip().lower() for _,address in getaddresses([h or '' for h in headers]) if address and '@' in address}


def timestamp(value):
    try:
        result=datetime.fromisoformat(value or '')
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError,TypeError,OverflowError): return datetime.now(timezone.utc)


def outbound_match(db,app,sender,address,entry):
    if address.lower() not in addresses(entry.get('from')): return None
    recipients=addresses(entry.get('to'),entry.get('cc'))-{address.lower()}
    mid=entry.get('message_id','')
    if not mid or mid not in mail_client.message_ids(mid) or not recipients: return None
    existing=db.scalar(select(app.Message).where(app.Message.message_id==mid).execution_options(populate_existing=True))
    if existing:
        return (existing.lead_id,existing.recipient,existing.id) if existing.sender==sender and existing.direction=='outbound' and existing.status in ('sent','sending','uncertain') and existing.recipient.lower() in recipients else None
    pending=db.execute(select(app.Message,app.MailSubmission).join(app.LeadDelivery,app.LeadDelivery.message_id==app.Message.id).join(app.MailSubmission,app.MailSubmission.id==app.LeadDelivery.request_id).where(app.MailSubmission.message_id==mid,app.Message.sender==sender,app.Message.status.in_(['sending','uncertain'])).execution_options(populate_existing=True)).first()
    if pending:
        draft,submission=pending
        return (draft.lead_id,draft.recipient,draft.id) if draft.recipient.lower() in recipients else None
    leads=db.scalars(select(app.Lead).where(or_(func.lower(app.Lead.person_email).in_(recipients),func.lower(app.Lead.company_email).in_(recipients)))).all()
    if len(leads)!=1:return None
    lead=leads[0]
    match=next((email for email in (lead.person_email,lead.company_email) if email and email.lower() in recipients),None)
    return (lead.id,match,None) if match else None


def inbound_match(db,app,sender,entry):
    auto=(entry.get('auto_submitted') or '').strip().lower().split(';',1)[0]
    precedence=(entry.get('precedence') or '').strip().lower()
    if (auto and auto!='no') or precedence in ('bulk','junk','list','auto_reply'):return None
    origin=addresses(entry.get('from')); refs=mail_client.message_ids(entry.get('references'))
    mid=entry.get('message_id','')
    if not origin or not refs or not mid or mid not in mail_client.message_ids(mid):return None
    referenced=db.scalars(select(app.Message).where(app.Message.sender==sender,app.Message.message_id.in_(refs),app.Message.status.in_(['sent','sending','uncertain','received']))).all()
    candidates=set()
    for ref in referenced:
        original=db.scalar(select(app.Message.id).where(app.Message.lead_id==ref.lead_id,app.Message.sender==sender,app.Message.direction=='outbound',app.Message.status.in_(['sent','sending','uncertain']),func.lower(app.Message.recipient).in_(origin)).limit(1))
        if original:candidates.add(ref.lead_id)
    return next(iter(candidates)) if len(candidates)==1 else None


def record_outbound(app,sender,address,entry,body):
    with app.Session.begin() as db:
        match=outbound_match(db,app,sender,address,entry)
        if not match:return False
        lead_id,recipient,message_pk=match
        lead=db.scalar(select(app.Lead).where(app.Lead.id==lead_id).with_for_update())
        # Recheck after locking in case another process recorded the message.
        match=outbound_match(db,app,sender,address,entry)
        if not match or match[0]!=lead_id:return False
        _,recipient,message_pk=match
        message=db.get(app.Message,message_pk) if message_pk else None
        changed=False
        if message:
            if message.status in ('sending','uncertain'):
                message.status='sent';message.message_id=entry['message_id'];message.sent_at=timestamp(entry.get('date'));message.error=None;changed=True
                delivery=db.get(app.LeadDelivery,message.id)
                submission=db.get(app.MailSubmission,delivery.request_id) if delivery else None
                if submission and submission.message_id==entry['message_id']:
                    submission.status='sent';submission.error=None
        else:
            when=timestamp(entry.get('date'))
            created=datetime.now(timezone.utc)
            first=db.scalar(select(app.Message.created_at).where(app.Message.lead_id==lead.id,app.Message.direction=='outbound').order_by(app.Message.created_at,app.Message.id).limit(1))
            if first and created<=first:created=first+timedelta(microseconds=1)
            db.add(app.Message(lead_id=lead.id,direction='outbound',status='sent',sender=sender,recipient=recipient,subject=entry.get('subject') or '(No subject)',body=body,message_id=entry['message_id'],in_reply_to=entry.get('in_reply_to') or None,created_at=created,sent_at=when));changed=True
        if lead.status not in ('sent','replied','do_not_contact') and not db.get(app.Suppression,recipient.lower()):lead.status='sent';changed=True
        return changed


def record_inbound(app,sender,address,entry,body):
    with app.Session.begin() as db:
        if db.scalar(select(app.Message.id).where(app.Message.message_id==entry.get('message_id'))):return False
        lead_id=inbound_match(db,app,sender,entry)
        if lead_id is None:return False
        lead=db.scalar(select(app.Lead).where(app.Lead.id==lead_id).with_for_update())
        if inbound_match(db,app,sender,entry)!=lead_id:return False
        if db.scalar(select(app.Message.id).where(app.Message.message_id==entry['message_id'])):return False
        db.add(app.Message(lead_id=lead.id,direction='inbound',status='received',sender=sender,recipient=address,subject=entry.get('subject') or '(No subject)',body=body,message_id=entry['message_id'],in_reply_to=entry.get('in_reply_to') or None,created_at=timestamp(entry.get('date'))))
        origins=addresses(entry.get('from'))
        if lead.status!='do_not_contact' and not any(db.get(app.Suppression,origin) for origin in origins):lead.status='replied'
        return True


def record_submissions(app,sender,address):
    with app.Session() as db:
        people=select(func.lower(app.Lead.person_email)).where(app.Lead.person_email.is_not(None))
        companies=select(func.lower(app.Lead.company_email)).where(app.Lead.company_email.is_not(None))
        submissions=db.scalars(select(app.MailSubmission).where(app.MailSubmission.sender==sender,app.MailSubmission.status=='sent',or_(func.lower(app.MailSubmission.recipient).in_(people),func.lower(app.MailSubmission.recipient).in_(companies)),~select(app.Message.id).where(app.Message.message_id==app.MailSubmission.message_id).exists()).order_by(app.MailSubmission.created_at).limit(500)).all()
        entries=[{'from':address,'to':item.recipient,'subject':item.subject,'message_id':item.message_id,'date':item.created_at.isoformat(),'body':item.body} for item in submissions]
    return sum(bool(record_outbound(app,sender,address,entry,entry['body'])) for entry in entries)


def update_cursor(app,sender,folder,**changes):
    with app.Session.begin() as db:
        cursor=db.get(app.MailboxSyncCursor,(sender,folder))
        if not cursor:
            cursor=app.MailboxSyncCursor(sender=sender,folder=folder,uidvalidity='',last_uid=0,has_more=False);db.add(cursor)
        for key,value in changes.items():setattr(cursor,key,value)


def sync_folder(app,client,sender,address,folder):
    _,validity=mail_client.select_folder(client,folder)
    if not validity:raise mail_client.MailboxError('Mailbox did not provide UIDVALIDITY')
    with app.Session() as db:
        cursor=db.get(app.MailboxSyncCursor,(sender,folder)); last=cursor.last_uid if cursor and cursor.uidvalidity==validity else 0
    if not cursor or cursor.uidvalidity!=validity:update_cursor(app,sender,folder,uidvalidity=validity,last_uid=0,has_more=False,error=None)
    status,result=client.uid('search',None,'UID',str(last+1)+':*')
    if status!='OK':raise mail_client.MailboxError('Could not search mailbox')
    uids=sorted({int(x) for x in (result[0] or b'').split() if x.isdigit() and int(x)>last})
    pending=uids[:MAX_PER_FOLDER]; changed=0
    for offset in range(0,len(pending),BATCH_SIZE):
        batch=pending[offset:offset+BATCH_SIZE]; headers=mail_client.fetch_headers(client,[str(uid).encode() for uid in batch],validity)
        if {int(item['uid']) for item in headers} != set(batch):
            raise mail_client.MailboxError('Mailbox changed while fetching headers; retry')
        for entry in headers:
            with app.Session() as db:
                match=outbound_match(db,app,sender,address,entry) if folder=='sent' else inbound_match(db,app,sender,entry)
                existing=db.scalar(select(app.Message.id).where(app.Message.message_id==entry.get('message_id'))) if entry.get('message_id') else None
            if match is None:continue
            if folder=='inbox' and existing:continue
            # Existing outbound records need no body; only new matching messages are fetched.
            detail=entry if folder=='sent' and match[2] else mail_client.read_selected_message(client,entry['uid'],validity)
            body=detail.get('body','')
            if folder=='sent':changed+=bool(record_outbound(app,sender,address,entry,body))
            else:changed+=bool(record_inbound(app,sender,address,detail,body))
        last=batch[-1]
        update_cursor(app,sender,folder,uidvalidity=validity,last_uid=last,checked_at=datetime.now(timezone.utc),has_more=last<uids[-1],error=None)
    if not pending:update_cursor(app,sender,folder,uidvalidity=validity,last_uid=last,checked_at=datetime.now(timezone.utc),has_more=False,error=None)
    return changed


def sync_sender(sender):
    from backend import main as app
    if sender not in ('stefan','adrija'):raise HTTPException(400,'Invalid mailbox')
    try: address,password=app.mailbox(sender)
    except HTTPException as exc:
        if exc.status_code != 400:
            for folder in ('sent','inbox'):update_cursor(app,sender,folder,error='Mailbox credentials are unavailable. Check connection settings.')
        raise
    lock_id=87349001 if sender=='stefan' else 87349002
    with app.engine.connect() as lock:
        if not lock.scalar(text('SELECT pg_try_advisory_lock(:key)'),{'key':lock_id}):return {'busy':True,'matched':0}
        try:
            changed=record_submissions(app,sender,address)
            with mail_client.connect(address,password,app.os.getenv('IMAP_HOST','imap.purelymail.com'),int(app.os.getenv('IMAP_PORT','993'))) as client:
                try:
                    changed+=sync_folder(app,client,sender,address,'sent')
                except Exception as exc:
                    log.warning('Mailbox sync failed for %s/sent: %s',sender,type(exc).__name__)
                    update_cursor(app,sender,'sent',error='Could not sync this folder. Check mailbox connection.')
                    update_cursor(app,sender,'inbox',error='Waiting for the Sent folder to sync.')
                    return {'busy':False,'matched':changed}
                with app.Session() as db:
                    sent_cursor=db.get(app.MailboxSyncCursor,(sender,'sent'))
                    sent_backlog=sent_cursor.has_more
                if sent_backlog:
                    update_cursor(app,sender,'inbox',has_more=True,error=None)
                else:
                    try:changed+=sync_folder(app,client,sender,address,'inbox')
                    except Exception as exc:
                        log.warning('Mailbox sync failed for %s/inbox: %s',sender,type(exc).__name__)
                        update_cursor(app,sender,'inbox',error='Could not sync this folder. Check mailbox connection.')
            return {'busy':False,'matched':changed}
        except Exception as exc:
            log.warning('Mailbox sync failed for %s: %s',sender,type(exc).__name__)
            for folder in ('sent','inbox'):update_cursor(app,sender,folder,error='Could not connect to the mailbox.')
            raise HTTPException(502,'Could not sync the mailbox. Check its connection.')
        finally:lock.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':lock_id})
