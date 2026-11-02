import base64
import asyncio
import email
import io
import unittest
from unittest.mock import patch
from uuid import uuid4
from PIL import Image
from cryptography.fernet import Fernet
from starlette.requests import Request
from starlette.responses import Response
from backend import main as app
from test_mailbox import FakeIMAP

class SignatureSettingsTests(unittest.TestCase):
    def test_photo_settings_and_rendered_message(self):
        image=io.BytesIO(); Image.new('RGB',(100,150),'blue').save(image,'PNG')
        settings=app.update_signature_settings('stefan',app.SignatureSettingsInput(name='Stefan',title='CTO',email='stefan@ceratosystems.com',photo=base64.b64encode(image.getvalue()).decode()))
        self.assertEqual(settings['name'],'Stefan')
        self.assertTrue(settings['photo_id'])
        response=app.signature_photo(settings['photo_id'])
        self.assertEqual(response.media_type,'image/png')
        cropped=Image.open(io.BytesIO(response.body)); self.assertEqual(cropped.size,(170,170)); self.assertEqual(cropped.getpixel((0,0))[3],0)
        key=Fernet.generate_key().decode()
        with patch.dict(app.os.environ,{'CRM_ORIGIN':'https://crm.ceratosystems.com','MAILBOX_ENCRYPTION_KEY':key,'MAIL_TRANSPORT':'relay','RELAY_URL':'https://relay.example/send','RELAY_TOKEN':'test-token'}),patch.object(app.mail_client,'connect',return_value=FakeIMAP()),patch.object(app.mail_relay,'send',return_value=('sent',None)) as transport:
            app.connect_mailbox('stefan',app.MailboxSetupInput(address='stefan@ceratosystems.com',password='not-real'))
            data=app.ComposeMailInput(request_id=uuid4(),recipient='test@example.com',subject='Hello',body='Hello',html_body='<p>Hello</p>',include_signature=True)
            self.assertEqual(app.send_manual_mail('stefan',data)['status'],'sent')
            msg=email.message_from_bytes(transport.call_args.args[5]); html=next(p for p in msg.walk() if p.get_content_type()=='text/html').get_payload(decode=True).decode()
            self.assertIn('https://crm.ceratosystems.com/api/signature-images/'+settings['photo_id']+'.png',html)
            self.assertIn('border-left:1px solid #cbd5d1',html); self.assertIn('mailto:stefan@ceratosystems.com',html)
            plain=next(p for p in msg.walk() if p.get_content_type()=='text/plain').get_payload(decode=True).decode();self.assertIn('CTO | Cerato Systems',plain)
            data=data.model_copy(update={'request_id':uuid4(),'include_signature':False});app.send_manual_mail('stefan',data)
            msg=email.message_from_bytes(transport.call_args.args[5]); html=next(p for p in msg.walk() if p.get_content_type()=='text/html').get_payload(decode=True).decode();self.assertNotIn('Cerato Systems',html)
    def test_rejects_invalid_photo(self):
        with self.assertRaises(app.HTTPException): app.update_signature_settings('adrija',app.SignatureSettingsInput(name='Andrija',title='CBO',email='andrija@ceratosystems.com',photo=base64.b64encode(b'bad').decode()))

    def test_photo_is_public_but_settings_require_login(self):
        async def next_handler(request): return Response(status_code=200)
        def request(path): return Request({'type':'http','method':'GET','path':path,'headers':[],'query_string':b'','server':('localhost',8000),'scheme':'http'})
        with patch.object(app,'AUTH_HASH','configured'),patch.object(app,'SESSION_KEY','test-session-key'):
            self.assertEqual(asyncio.run(app.require_auth(request('/api/signature-images/stefan-default.png'),next_handler)).status_code,200)
            self.assertEqual(asyncio.run(app.require_auth(request('/api/settings'),next_handler)).status_code,401)

    def test_senders_have_independent_settings(self):
        saved=app.update_signature_settings('adrija',app.SignatureSettingsInput(name='Andrija',title='CBO',email='andrija@ceratosystems.com'))
        self.assertIn('Andrija',saved['html']); self.assertIn('CBO',saved['html'])
        with app.Session() as db:
            self.assertEqual(app.signature_settings(db,'stefan')['name'],'Stefan')
