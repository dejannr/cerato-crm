import json
import unittest
import urllib.error
from unittest.mock import patch
from backend import relay

class Response:
    status=200
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self,size): return b'{"status":"sent"}'

class RelayTests(unittest.TestCase):
    def test_sent_payload_and_https(self):
        class Opener:
            def open(self,request,timeout):
                self.request=request
                return Response()
        opener=Opener()
        with patch('backend.relay.urllib.request.build_opener',return_value=opener):
            status,warning=relay.send('https://relay.example/send','secret-token','stefan@ceratosystems.com','password','recipient@example.com',b'hello')
        self.assertEqual((status,warning),('sent',None))
        self.assertEqual(json.loads(opener.request.data)['raw_email'],'aGVsbG8=')
        self.assertEqual(opener.request.get_header('Authorization'),'Bearer secret-token')
        self.assertEqual(opener.request.get_header('User-agent'),'CeratoCRM/1.0')
    def test_refuses_non_https_or_redirect_address(self):
        for url in ('http://relay.example/send','https://relay.example/send?secret=1','https://user:pass@relay.example/send'):
            self.assertEqual(relay.send(url,'token','user','pw','x@example.com',b'x')[0],'failed')
    def test_network_error_is_uncertain(self):
        class Opener:
            def open(self,*args,**kwargs):raise urllib.error.URLError('connection dropped')
        with patch('backend.relay.urllib.request.build_opener',return_value=Opener()):
            self.assertEqual(relay.send('https://relay.example/send','token','u','p','x@example.com',b'x')[0],'uncertain')
    def test_malformed_result_is_uncertain(self):
        class InvalidResponse(Response):
            def read(self,size): return b'broken'
        class Opener:
            def open(self,*args,**kwargs):return InvalidResponse()
        with patch('backend.relay.urllib.request.build_opener',return_value=Opener()):
            self.assertEqual(relay.send('https://relay.example/send','token','u','p','x@example.com',b'x')[0],'uncertain')
