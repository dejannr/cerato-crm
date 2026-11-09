import email
import unittest
import uuid
from unittest.mock import patch
from fastapi import HTTPException
from backend import main as app

class LeadReplyTests(unittest.TestCase):
    def setUp(self):
        self.address=uuid.uuid4().hex+'@example.com'
        self.original='<'+uuid.uuid4().hex+'@example.com>'
        self.incoming='<'+uuid.uuid4().hex+'@example.com>'
        with app.Session.begin() as db:
            lead=app.Lead(company='Reply test',person_email=self.address,status='replied');db.add(lead);db.flush();self.lead_id=lead.id
            db.add(app.Message(lead_id=lead.id,direction='outbound',status='sent',sender='stefan',recipient=self.address,subject='Hello',body='Hello',message_id=self.original))
            reply=app.Message(lead_id=lead.id,direction='inbound',status='received',sender='stefan',recipient='stefan@ceratosystems.com',subject='Re: Hello',body='Thanks',message_id=self.incoming,in_reply_to=self.original)
            db.add(reply);db.flush();self.reply_id=reply.id
        self.data=app.LeadReplyInput(request_id=uuid.uuid4(),body='Thank you',html_body='<p>Thank you</p>')

    def test_threaded_reply_uses_correct_account_and_is_recorded_once(self):
        with patch.object(app,'mailbox',return_value=('stefan@ceratosystems.com','fake')),patch.dict(app.os.environ,{'MAIL_TRANSPORT':'relay','RELAY_URL':'https://example.com/send','RELAY_TOKEN':'fake'}),patch.object(app.mail_relay,'send',return_value=('sent',None)) as relay,patch.object(app,'mail_connection',side_effect=RuntimeError('No IMAP')):
            result=app.send_lead_reply(self.lead_id,self.reply_id,self.data)
            self.assertEqual(app.send_lead_reply(self.lead_id,self.reply_id,self.data)['status'],'sent')
        self.assertEqual(result['status'],'sent');self.assertEqual(relay.call_count,1)
        mime=email.message_from_bytes(relay.call_args.args[-1],policy=email.policy.default)
        self.assertEqual(mime['In-Reply-To'],self.incoming);self.assertIn(self.original,mime['References']);self.assertEqual(mime['To'],self.address)
        self.assertIn('Cerato Systems',mime.get_body(preferencelist=('plain',)).get_content())
        lead=app.lead(self.lead_id);self.assertEqual(lead['status'],'replied');self.assertEqual(len(lead['messages']),3)

    def test_stopped_and_suppressed_leads_cannot_send(self):
        with app.Session.begin() as db:db.get(app.Lead,self.lead_id).status='do_not_contact'
        with patch.object(app,'send_manual_mail') as send:
            with self.assertRaises(HTTPException):app.send_lead_reply(self.lead_id,self.reply_id,self.data)
            send.assert_not_called()
        with app.Session.begin() as db:db.get(app.Lead,self.lead_id).status='replied';db.add(app.Suppression(email=self.address,reason='Stop'))
        with patch.object(app,'send_manual_mail') as send:
            with self.assertRaises(HTTPException):app.send_lead_reply(self.lead_id,self.reply_id,self.data)
            send.assert_not_called()

    def test_unmatched_recipient_requires_inbox_when_ambiguous(self):
        with app.Session.begin() as db:
            db.get(app.Message,self.reply_id).in_reply_to='<unknown@example.com>'
            db.add(app.Message(lead_id=self.lead_id,direction='outbound',status='sent',sender='stefan',recipient='another@example.com',subject='Hello',body='Hello',message_id='<other@example.com>'))
        with self.assertRaises(HTTPException):app.prepare_lead_reply(self.lead_id,self.reply_id)

    def test_uncertain_result_is_not_recorded_as_sent(self):
        with patch.object(app,'send_manual_mail',return_value={'status':'uncertain','warning':'Check mailbox'}):
            self.assertEqual(app.send_lead_reply(self.lead_id,self.reply_id,self.data)['status'],'uncertain')
        self.assertEqual(len(app.lead(self.lead_id)['messages']),2)
