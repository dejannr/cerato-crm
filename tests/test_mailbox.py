import unittest
import email
from email.utils import parseaddr
from email.message import EmailMessage
from unittest.mock import patch
from uuid import uuid4
from cryptography.fernet import Fernet
from backend import mailbox as client
import backend.main as app

class FakeIMAP:
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def list(self): return 'OK',[b'(\\HasNoChildren \\Sent) "/" "Sent Items"']
    def select(self,*args,**kwargs): return 'OK',[b'1']
    def response(self,name): return 'UIDVALIDITY',[b'123']
    def uid(self,operation,*args):
        if operation=='search': return 'OK',[b'9']
        msg=EmailMessage(); msg['From']='Alex <alex@example.com>'; msg['To']='stefan@ceratosystems.com'; msg['Subject']='Hello'; msg['Message-ID']='<inbound@example.com>'; msg.set_content('Hello from Alex')
        return 'OK',[(b'9 (UID 9 FLAGS (\\Seen)',msg.as_bytes())]
    def append(self,*args): return 'OK',[b'1']

class FakeSMTP:
    sends=0
    def __init__(self,*args,**kwargs): pass
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def login(self,*args): pass
    def send_message(self,msg): FakeSMTP.sends+=1
    def quit(self): pass
    def close(self): pass

class MailboxTests(unittest.TestCase):
    def test_imap_folders_headers_and_uidvalidity(self):
        fake=FakeIMAP()
        self.assertEqual(client.folder_name(fake,'sent'),'Sent Items')
        rows=client.list_messages(fake,'inbox'); self.assertEqual(rows[0]['subject'],'Hello'); self.assertTrue(rows[0]['seen'])
        detail=client.read_message(fake,'inbox','9','123'); self.assertEqual(detail['reply_to'],'alex@example.com'); self.assertIn('Hello from Alex',detail['body'])
        with self.assertRaises(client.MailboxError): client.read_message(fake,'inbox','9','old')
    def test_html_email_is_read_as_text(self):
        msg=EmailMessage(); msg.set_content('<p>Hello</p><script>danger</script><p>there</p>',subtype='html')
        self.assertIn('Hello',client.body_text(msg)); self.assertNotIn('danger',client.body_text(msg))
    def test_credentials_encrypted_and_send_idempotent(self):
        key=Fernet.generate_key().decode(); FakeSMTP.sends=0
        with patch.dict(app.os.environ,{'MAILBOX_ENCRYPTION_KEY':key,'MAIL_TRANSPORT':'smtp'}),patch.object(client,'connect',return_value=FakeIMAP()),patch.object(app.smtplib,'SMTP_SSL',FakeSMTP):
            app.connect_mailbox('stefan',app.MailboxSetupInput(address='stefan@ceratosystems.com',password='not-a-real-password'))
            with app.Session() as db: self.assertNotIn('not-a-real-password',db.get(app.MailboxCredential,'stefan').encrypted_password)
            self.assertEqual(app.mailbox('stefan')[1],'not-a-real-password')
            data=app.ComposeMailInput(request_id=uuid4(),recipient='test@example.com',subject='Test',body='Hello')
            a=app.send_manual_mail('stefan',data); b=app.send_manual_mail('stefan',data)
            self.assertEqual(a['status'],'sent'); self.assertEqual(b['status'],'sent'); self.assertEqual(FakeSMTP.sends,1)
            changed=data.model_copy(update={'body':'Different'})
            with self.assertRaises(app.HTTPException):app.send_manual_mail('stefan',changed)

    def test_mailbox_defaults_and_editable_signature(self):
        defaults=app.settings()['mailboxes']
        self.assertEqual(defaults[0]['display_name'],'Stefan Radovanovic')
        self.assertEqual(defaults[1]['display_name'],'Andrija Ilic')
        app.update_mailbox_preferences('adrija',app.MailboxPreferencesInput(display_name='Andrija Ilic',signature='Regards,\nAndrija'))
        self.assertEqual(app.settings()['mailboxes'][1]['signature'],'Regards,\nAndrija')
        with self.assertRaises(app.HTTPException):app.update_mailbox_preferences('stefan',app.MailboxPreferencesInput(display_name='Stefan\nBcc: bad@example.com',signature=''))

    def test_sender_display_name_is_in_relay_message(self):
        key=Fernet.generate_key().decode()
        with patch.dict(app.os.environ,{'MAILBOX_ENCRYPTION_KEY':key,'MAIL_TRANSPORT':'relay','RELAY_URL':'https://relay.example/send','RELAY_TOKEN':'test-token'}),patch.object(client,'connect',return_value=FakeIMAP()),patch.object(app.mail_relay,'send',return_value=('sent',None)) as transport:
            app.connect_mailbox('stefan',app.MailboxSetupInput(address='stefan@ceratosystems.com',password='not-a-real-password'))
            data=app.ComposeMailInput(request_id=uuid4(),recipient='test@example.com',subject='Display',body='Hello\n\nBest,\nStefan')
            self.assertEqual(app.send_manual_mail('stefan',data)['status'],'sent')
            raw=transport.call_args.args[5]; msg=email.message_from_bytes(raw)
            self.assertEqual(parseaddr(msg['From']),('Stefan Radovanovic','stefan@ceratosystems.com'))
            self.assertIn('Best,',msg.get_payload(decode=True).decode())

    def test_smtp_connection_failure_is_not_sent(self):
        key=Fernet.generate_key().decode()
        with patch.dict(app.os.environ,{'MAILBOX_ENCRYPTION_KEY':key,'MAIL_TRANSPORT':'smtp'}),patch.object(client,'connect',return_value=FakeIMAP()),patch.object(app.smtplib,'SMTP_SSL',side_effect=TimeoutError('blocked')):
            app.connect_mailbox('stefan',app.MailboxSetupInput(address='stefan@ceratosystems.com',password='not-a-real-password'))
            data=app.ComposeMailInput(request_id=uuid4(),recipient='test@example.com',subject='Connection test',body='Hello')
            result=app.send_manual_mail('stefan',data)
            self.assertEqual(result['status'],'failed'); self.assertIn('Not sent',result['warning'])

    def test_submission_failure_remains_uncertain(self):
        class InterruptedSMTP(FakeSMTP):
            def send_message(self,msg): raise TimeoutError('connection lost during submission')
        key=Fernet.generate_key().decode()
        with patch.dict(app.os.environ,{'MAILBOX_ENCRYPTION_KEY':key,'MAIL_TRANSPORT':'smtp'}),patch.object(client,'connect',return_value=FakeIMAP()),patch.object(app.smtplib,'SMTP_SSL',InterruptedSMTP):
            app.connect_mailbox('stefan',app.MailboxSetupInput(address='stefan@ceratosystems.com',password='not-a-real-password'))
            data=app.ComposeMailInput(request_id=uuid4(),recipient='test@example.com',subject='Submission test',body='Hello')
            self.assertEqual(app.send_manual_mail('stefan',data)['status'],'uncertain')

    def test_relay_send_preserves_sent_copy_and_idempotency(self):
        key=Fernet.generate_key().decode(); FakeSMTP.sends=0
        with patch.dict(app.os.environ,{'MAILBOX_ENCRYPTION_KEY':key,'MAIL_TRANSPORT':'relay','RELAY_URL':'https://relay.example/send','RELAY_TOKEN':'test-token'}),patch.object(client,'connect',return_value=FakeIMAP()),patch.object(app.mail_relay,'send',return_value=('sent',None)) as send,patch.object(app.smtplib,'SMTP_SSL',side_effect=AssertionError('Direct SMTP used')):
            app.connect_mailbox('stefan',app.MailboxSetupInput(address='stefan@ceratosystems.com',password='not-a-real-password'))
            data=app.ComposeMailInput(request_id=uuid4(),recipient='test@example.com',subject='Relay',body='Hello')
            first=app.send_manual_mail('stefan',data); second=app.send_manual_mail('stefan',data)
            self.assertEqual(first['status'],'sent'); self.assertEqual(second['status'],'sent'); self.assertEqual(send.call_count,1)
            self.assertIn(b'\r\n',send.call_args.args[5])

    def test_relay_uncertain_is_not_retried(self):
        key=Fernet.generate_key().decode()
        with patch.dict(app.os.environ,{'MAILBOX_ENCRYPTION_KEY':key,'MAIL_TRANSPORT':'relay','RELAY_URL':'https://relay.example/send','RELAY_TOKEN':'test-token'}),patch.object(client,'connect',return_value=FakeIMAP()),patch.object(app.mail_relay,'send',return_value=('uncertain','Check inbox')) as send:
            app.connect_mailbox('stefan',app.MailboxSetupInput(address='stefan@ceratosystems.com',password='not-a-real-password'))
            data=app.ComposeMailInput(request_id=uuid4(),recipient='test@example.com',subject='Relay',body='Hello')
            self.assertEqual(app.send_manual_mail('stefan',data)['status'],'uncertain')
            self.assertEqual(app.send_manual_mail('stefan',data)['status'],'uncertain')
            self.assertEqual(send.call_count,1)

if __name__=='__main__':unittest.main()
