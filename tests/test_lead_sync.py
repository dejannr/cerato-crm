import unittest
import uuid
from email.message import EmailMessage
from datetime import datetime, timezone
from unittest.mock import patch
from backend import main as app
from backend import lead_sync


def mail(mid,from_addr,to_addr,subject,body,reply='',auto=''):
    msg=EmailMessage();msg['Message-ID']=mid;msg['From']=from_addr;msg['To']=to_addr;msg['Subject']=subject;msg['Date']='Mon, 09 Nov 2026 10:00:00 +0000'
    if reply:msg['In-Reply-To']=reply;msg['References']=reply
    if auto:msg['Auto-Submitted']=auto
    msg.set_content(body);return msg.as_bytes()

class SyncIMAP:
    def __init__(self,folders):self.folders=folders;self.folder='inbox';self.fetches=[];self.validity={'inbox':'100','sent':'200'}
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def list(self):return 'OK',[b'(\\HasNoChildren \\Sent) "/" "Sent Items"']
    def select(self,name,readonly=True):
        if not readonly:raise AssertionError('Mailbox must be read-only')
        self.folder='sent' if 'Sent Items' in name else 'inbox';return 'OK',[b'0']
    def response(self,name):return 'UIDVALIDITY',[self.validity[self.folder].encode()]
    def uid(self,operation,*args):
        data=self.folders.get(self.folder,{})
        if operation=='search':return 'OK',[(' '.join(str(x) for x in sorted(data))).encode()]
        uids=args[0].decode() if isinstance(args[0],bytes) else args[0];self.fetches.append((self.folder,uids,args[1]))
        if 'BODY.PEEK' not in args[1]:raise AssertionError('Fetch must not mark mail read')
        rows=[]
        for uid in uids.split(','):
            if int(uid) in data:rows.extend([(f'{uid} (UID {uid} FLAGS ()'.encode(),data[int(uid)]),b')'])
        return 'OK',rows

class LeadSyncTests(unittest.TestCase):
    def setUp(self):
        self.addr=uuid.uuid4().hex+'@example.com';self.mid='<'+uuid.uuid4().hex+'@example.com>'
        with app.Session.begin() as db:
            db.query(app.MailboxSyncCursor).delete()
            lead=app.Lead(company='Example '+uuid.uuid4().hex,city='Belgrade',person_email=self.addr,email_type='personal',status='new');db.add(lead);db.flush();self.lead_id=lead.id
    def lead(self):return app.lead(self.lead_id)
    def sync(self,client):
        with patch.object(app,'mailbox',return_value=('stefan@ceratosystems.com','fake')),patch.object(lead_sync.mail_client,'connect',return_value=client):return lead_sync.sync_sender('stefan')
    def test_sent_and_threaded_reply_update_and_deduplicate(self):
        sent=mail(self.mid,'Stefan <stefan@ceratosystems.com>',self.addr,'Hello','Original')
        reply=mail('<reply-'+uuid.uuid4().hex+'@example.com>',self.addr,'stefan@ceratosystems.com','Re: Hello','Thank you',self.mid)
        client=SyncIMAP({'sent':{1:sent},'inbox':{2:reply}})
        self.sync(client);lead=self.lead();self.assertEqual(lead['status'],'replied');self.assertEqual({m['direction'] for m in lead['messages']},{'outbound','inbound'})
        self.sync(client);self.assertEqual(len(self.lead()['messages']),2)
        client.validity={'sent':'201','inbox':'101'};self.sync(client);self.assertEqual(len(self.lead()['messages']),2)
    def test_ambiguous_address_is_not_assigned(self):
        with app.Session.begin() as db:db.add(app.Lead(company='Duplicate '+uuid.uuid4().hex,person_email=self.addr,status='new'))
        sent=mail(self.mid,'stefan@ceratosystems.com',self.addr,'Hello','Original');self.sync(SyncIMAP({'sent':{1:sent}}))
        self.assertEqual(self.lead()['status'],'new');self.assertEqual(self.lead()['messages'],[])
    def test_owner_and_do_not_contact_are_respected(self):
        with app.Session.begin() as db:
            db.get(app.Lead,self.lead_id).status='do_not_contact'
            db.add(app.Message(lead_id=self.lead_id,direction='outbound',status='draft',sender='adrija',recipient=self.addr,subject='Draft',body='Draft'))
        sent=mail(self.mid,'stefan@ceratosystems.com',self.addr,'Hello','Original');self.sync(SyncIMAP({'sent':{1:sent}}))
        self.assertEqual(self.lead()['status'],'do_not_contact');self.assertEqual(len(self.lead()['messages']),2);self.assertEqual(self.lead()['owner'],'adrija')
    def test_auto_reply_and_unrelated_incoming_are_ignored(self):
        sent=mail(self.mid,'stefan@ceratosystems.com',self.addr,'Hello','Original')
        auto=mail('<auto-'+uuid.uuid4().hex+'@example.com>',self.addr,'stefan@ceratosystems.com','Re: Hello','Out of office',self.mid,'auto-replied')
        unrelated=mail('<other-'+uuid.uuid4().hex+'@example.com>',self.addr,'stefan@ceratosystems.com','Hello','Unrelated')
        self.sync(SyncIMAP({'sent':{1:sent},'inbox':{2:auto,3:unrelated}}));lead=self.lead();self.assertEqual(lead['status'],'sent');self.assertEqual(len(lead['messages']),1)
    def test_suppression_does_not_change_status(self):
        with app.Session.begin() as db:db.add(app.Suppression(email=self.addr,reason='Do not contact'))
        sent=mail(self.mid,'stefan@ceratosystems.com',self.addr,'Hello','Original');self.sync(SyncIMAP({'sent':{1:sent}}))
        self.assertEqual(self.lead()['status'],'new')
    def test_sent_submission_without_sent_folder_is_recorded(self):
        with app.Session.begin() as db:db.add(app.MailSubmission(id=str(uuid.uuid4()),sender='stefan',recipient=self.addr,subject='Hello',body='Sent body',status='sent',message_id=self.mid))
        self.sync(SyncIMAP({}));self.assertEqual(self.lead()['status'],'sent');self.assertEqual(self.lead()['messages'][0]['body'],'Sent body')
    def test_do_not_contact_is_preserved_when_history_is_found(self):
        with app.Session.begin() as db:db.get(app.Lead,self.lead_id).status='do_not_contact'
        sent=mail(self.mid,'stefan@ceratosystems.com',self.addr,'Hello','Original');self.sync(SyncIMAP({'sent':{1:sent}}))
        lead=self.lead();self.assertEqual(lead['status'],'do_not_contact');self.assertEqual(lead['messages'][0]['status'],'sent')
    def test_different_owner_is_not_reassigned(self):
        with app.Session.begin() as db:db.add(app.Message(lead_id=self.lead_id,direction='outbound',status='draft',sender='adrija',recipient=self.addr,subject='Draft',body='Draft'))
        sent=mail(self.mid,'stefan@ceratosystems.com',self.addr,'Hello','Original');self.sync(SyncIMAP({'sent':{1:sent}}))
        self.assertEqual(self.lead()['status'],'sent');self.assertEqual(len(self.lead()['messages']),2);self.assertEqual(self.lead()['owner'],'adrija')
    def test_incremental_backfill_processes_all_batches(self):
        data={i:mail('<'+uuid.uuid4().hex+'@example.com>','stefan@ceratosystems.com',self.addr,'Hello','Original') for i in (1,2,3)}
        client=SyncIMAP({'sent':data})
        with patch.object(lead_sync,'MAX_PER_FOLDER',2),patch.object(lead_sync,'BATCH_SIZE',1):
            self.sync(client)
            with app.Session() as db:self.assertTrue(db.get(app.MailboxSyncCursor,('stefan','sent')).has_more)
            self.assertEqual(len(self.lead()['messages']),2)
            self.sync(client)
        self.assertEqual(len(self.lead()['messages']),3)
        with app.Session() as db:self.assertFalse(db.get(app.MailboxSyncCursor,('stefan','sent')).has_more)
    def test_inbox_waits_for_sent_history_before_matching_replies(self):
        unrelated={i:mail('<'+uuid.uuid4().hex+'@example.com>','stefan@ceratosystems.com','other@example.com','Hello','Original') for i in (1,2)}
        unrelated[3]=mail(self.mid,'stefan@ceratosystems.com',self.addr,'Hello','Original')
        reply=mail('<reply-'+uuid.uuid4().hex+'@example.com>',self.addr,'stefan@ceratosystems.com','Re: Hello','Thank you',self.mid)
        client=SyncIMAP({'sent':unrelated,'inbox':{1:reply}})
        with patch.object(lead_sync,'MAX_PER_FOLDER',2):
            self.sync(client)
            self.assertEqual(self.lead()['status'],'new')
            with app.Session() as db:self.assertEqual(db.get(app.MailboxSyncCursor,('stefan','inbox')).last_uid,0)
            self.sync(client)
        self.assertEqual(self.lead()['status'],'replied')
        self.assertEqual(len(self.lead()['messages']),2)
    def test_missing_headers_do_not_advance_cursor(self):
        class Missing(SyncIMAP):
            def uid(self,operation,*args):
                if operation=='fetch' and self.folder=='sent':return 'OK',[]
                return super().uid(operation,*args)
        sent=mail(self.mid,'stefan@ceratosystems.com',self.addr,'Hello','Original');self.sync(Missing({'sent':{1:sent}}))
        with app.Session() as db:
            cursor=db.get(app.MailboxSyncCursor,('stefan','sent'));self.assertEqual(cursor.last_uid,0);self.assertTrue(cursor.error)
        self.assertEqual(self.lead()['status'],'new')
    def test_sent_submission_reconciles_uncertain_draft(self):
        request_id=str(uuid.uuid4())
        with app.Session.begin() as db:
            msg=app.Message(lead_id=self.lead_id,direction='outbound',status='uncertain',sender='stefan',recipient=self.addr,subject='Hello',body='Original')
            db.add(msg);db.flush();db.add(app.LeadDelivery(message_id=msg.id,request_id=request_id,include_signature=True))
            db.add(app.MailSubmission(id=request_id,sender='stefan',recipient=self.addr,subject='Hello',body='Original',status='sent',message_id=self.mid))
        self.sync(SyncIMAP({}));lead=self.lead();self.assertEqual(lead['status'],'sent');self.assertEqual(len(lead['messages']),1);self.assertEqual(lead['messages'][0]['status'],'sent');self.assertEqual(lead['messages'][0]['message_id'],self.mid)
