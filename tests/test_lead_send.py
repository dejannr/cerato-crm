import email
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from fastapi import HTTPException
from backend import main as app

class LeadSendTests(unittest.TestCase):
    def setUp(self):
        self.address=f'{uuid.uuid4().hex}@example.com'
        with app.Session.begin() as db:
            lead=app.Lead(company='Example', status='review', person_email=self.address)
            db.add(lead);db.flush(); self.lead_id=lead.id
            msg=app.Message(lead_id=lead.id,direction='outbound',status='draft',sender='stefan',recipient=self.address,subject='A question',body='Hello there')
            db.add(msg);db.flush();self.draft_id=msg.id
        self.data=app.SendLeadDraftInput(recipient=self.address,subject='A question',body='Hello there')

    def test_sent_message_has_signature_and_updates_lead(self):
        with patch.object(app,'mailbox',return_value=('stefan@ceratosystems.com','password')), patch.dict(app.os.environ,{'MAIL_TRANSPORT':'relay','RELAY_URL':'https://example.com/send','RELAY_TOKEN':'token'}), patch.object(app.mail_relay,'send',return_value=('sent',None)) as relay, patch.object(app,'mail_connection',side_effect=RuntimeError('No IMAP')):
            result=app.send_lead_draft(self.draft_id,self.data)
        self.assertEqual(result['status'],'sent')
        mime=email.message_from_bytes(relay.call_args.args[-1],policy=email.policy.default)
        self.assertIn('Stefan',mime.get_body(preferencelist=('plain',)).get_content())
        self.assertIn('stefan@ceratosystems.com',mime.get_body(preferencelist=('html',)).get_content())
        lead=app.lead(self.lead_id);self.assertEqual(lead['status'],'sent')
        self.assertEqual(lead['messages'][0]['status'],'sent');self.assertTrue(lead['messages'][0]['message_id'])
        with self.assertRaises(HTTPException): app.send_lead_draft(self.draft_id,self.data)

    def test_signature_can_be_omitted_and_draft_sender_is_preserved(self):
        with app.Session.begin() as db: db.get(app.Message,self.draft_id).sender='adrija'
        data=self.data.model_copy(update={'include_signature':False})
        with patch.object(app,'mailbox',return_value=('andrija@ceratosystems.com','password')), patch.dict(app.os.environ,{'MAIL_TRANSPORT':'relay','RELAY_URL':'https://example.com/send','RELAY_TOKEN':'token'}), patch.object(app.mail_relay,'send',return_value=('sent',None)) as relay, patch.object(app,'mail_connection',side_effect=RuntimeError('No IMAP')):
            self.assertEqual(app.send_lead_draft(self.draft_id,data)['status'],'sent')
        mime=email.message_from_bytes(relay.call_args.args[-1],policy=email.policy.default)
        self.assertIn('andrija@ceratosystems.com',mime['From'])
        self.assertNotIn('Cerato Systems',mime.get_body(preferencelist=('plain',)).get_content())

    def test_stopped_lead_cannot_be_sent(self):
        with app.Session.begin() as db: db.get(app.Lead,self.lead_id).status='do_not_contact'
        with patch.object(app,'send_manual_mail') as send:
            with self.assertRaises(HTTPException): app.send_lead_draft(self.draft_id,self.data)
            send.assert_not_called()

    def test_suppressed_and_changed_drafts_cannot_be_sent(self):
        with app.Session.begin() as db: db.add(app.Suppression(email=self.address,reason='Stop'))
        with patch.object(app,'send_manual_mail') as send:
            with self.assertRaises(HTTPException): app.send_lead_draft(self.draft_id,self.data)
            send.assert_not_called()
        with app.Session.begin() as db: db.delete(db.get(app.Suppression,self.address))
        changed=self.data.model_copy(update={'body':'Different draft'})
        with patch.object(app,'send_manual_mail') as send:
            with self.assertRaises(HTTPException): app.send_lead_draft(self.draft_id,changed)
            send.assert_not_called()

    def test_failed_send_can_be_retried_but_uncertain_cannot(self):
        with patch.object(app,'send_manual_mail',return_value={'status':'failed','warning':'Not sent'}):
            self.assertEqual(app.send_lead_draft(self.draft_id,self.data)['status'],'failed')
        self.assertEqual(app.lead(self.lead_id)['messages'][0]['status'],'draft')
        with patch.object(app,'send_manual_mail',return_value={'status':'uncertain','warning':'Check mailbox'}):
            self.assertEqual(app.send_lead_draft(self.draft_id,self.data)['status'],'uncertain')
        self.assertEqual(app.lead(self.lead_id)['messages'][0]['status'],'uncertain')
        with self.assertRaises(HTTPException): app.send_lead_draft(self.draft_id,self.data)

    def test_concurrent_send_does_not_submit_twice(self):
        entered=threading.Event(); release=threading.Event()
        def send(*args):
            entered.set();release.wait(5);return {'status':'sent','message_id':'<test@example.com>','warning':None}
        with patch.object(app,'send_manual_mail',side_effect=send) as mocked, ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(app.send_lead_draft,self.draft_id,self.data)
            self.assertTrue(entered.wait(5))
            try:
                with self.assertRaises(HTTPException): app.send_lead_draft(self.draft_id,self.data)
            finally: release.set()
            self.assertEqual(first.result()['status'],'sent')
            self.assertEqual(mocked.call_count,1)
