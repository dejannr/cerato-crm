"""Authenticated HTTPS transport to the private Cloudflare SMTP relay."""
import base64
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def send(url, token, username, password, recipient, raw_email):
    try: parsed=urlsplit(url or '')
    except ValueError: return 'failed', 'Not sent: the secure email relay is not configured correctly.'
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query or parsed.path != '/send' or not token:
        return 'failed', 'Not sent: the secure email relay is not configured correctly.'
    payload=json.dumps({'username':username,'password':password,'recipient':recipient,'raw_email':base64.b64encode(raw_email).decode('ascii')}).encode('utf-8')
    request=urllib.request.Request(url,data=payload,method='POST',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'})
    opener=urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request,timeout=35) as response:
            code=response.status
            content=response.read(4097)
    except urllib.error.HTTPError as exc:
        code=exc.code
        content=exc.read(4097)
    except Exception:
        return 'uncertain','The relay did not return a result. Check the mailbox before trying again.'
    try:
        result=json.loads(content) if len(content)<=4096 else {}
    except (ValueError, UnicodeDecodeError): result={}
    status=result.get('status') if isinstance(result,dict) else None
    if code==200 and status=='sent': return 'sent',None
    if code in (400,401,404,413) and status in ('invalid_request','unauthorized','not_found'):
        return 'failed','Not sent: the email relay rejected the request or its credentials.'
    if code==502 and status in ('failed','rejected'):
        return 'failed','Not sent: Purelymail did not accept the message. Check the mailbox credentials and server settings.'
    return 'uncertain','The relay could not confirm delivery. Check the mailbox before trying again.'
