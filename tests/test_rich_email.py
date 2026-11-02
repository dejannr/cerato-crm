import base64
import email
import io
import unittest
from unittest.mock import patch
from uuid import uuid4
from PIL import Image
from cryptography.fernet import Fernet
from backend import rich_email
from backend import main as app
from test_mailbox import FakeIMAP

def picture():
    buf=io.BytesIO(); Image.new('RGB',(3,3),'blue').save(buf,'PNG')
    return 'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()

class RichEmailTests(unittest.TestCase):
    def test_sanitizes_html_and_removes_external_or_script(self):
        cleaned=rich_email.clean_fragment('<p onclick="bad()">Hello <strong>bold</strong><script>bad()</script><a href="javascript:bad()">link</a><img src="https://evil.test/a"></p>')
        self.assertIn('<strong>bold</strong>',cleaned); self.assertNotIn('bad()',cleaned); self.assertNotIn('evil.test',cleaned)
    def test_signature_can_contain_safe_image_and_survives_save(self):
        html='<p>Best,<br>Stefan</p><img src="'+picture()+'" alt="Logo">'
        app.update_mailbox_preferences('stefan',app.MailboxPreferencesInput(display_name='Stefan Radovanovic',signature='Best,\nStefan',signature_html=html))
        account=app.settings()['mailboxes'][0]
        self.assertIn('data:image/png',account['signature_html']); self.assertIn('Stefan',account['signature'])
    def test_rich_email_sends_multipart_with_embedded_image_and_idempotency(self):
        key=Fernet.generate_key().decode(); html='<p>Hello <strong>Alex</strong></p><img src="'+picture()+'" alt="Logo">'
        with patch.dict(app.os.environ,{'MAILBOX_ENCRYPTION_KEY':key,'MAIL_TRANSPORT':'relay','RELAY_URL':'https://relay.example/send','RELAY_TOKEN':'test-token'}),patch.object(app.mail_client,'connect',return_value=FakeIMAP()),patch.object(app.mail_relay,'send',return_value=('sent',None)) as transport:
            app.connect_mailbox('stefan',app.MailboxSetupInput(address='stefan@ceratosystems.com',password='not-real'))
            data=app.ComposeMailInput(request_id=uuid4(),recipient='test@example.com',subject='Rich',body='Hello Alex',html_body=html)
            self.assertEqual(app.send_manual_mail('stefan',data)['status'],'sent')
            msg=email.message_from_bytes(transport.call_args.args[5]); self.assertEqual(msg.get_content_type(),'multipart/alternative')
            types=[part.get_content_type() for part in msg.walk()]; self.assertIn('text/plain',types);self.assertIn('text/html',types);self.assertIn('image/png',types)
            image_part=next(p for p in msg.walk() if p.get_content_type()=='image/png'); self.assertTrue(image_part['Content-ID'])
            html_part=next(p for p in msg.walk() if p.get_content_type()=='text/html').get_payload(decode=True).decode();self.assertIn('cid:',html_part);self.assertNotIn('data:image',html_part)
            app.send_manual_mail('stefan',data);self.assertEqual(transport.call_count,1)
            with self.assertRaises(app.HTTPException):app.send_manual_mail('stefan',data.model_copy(update={'html_body':'<p>Changed</p>'}))
    def test_rejects_invalid_or_oversized_images(self):
        with self.assertRaises(ValueError):rich_email.clean_fragment('<img src="data:image/png;base64,bm90YW5pbWFnZQ==">')
        with self.assertRaises(ValueError):rich_email.clean_fragment('x'*180001)
