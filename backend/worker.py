"""Periodically synchronize configured mailboxes. Run separately from the web server."""
import os
import time
from main import sync

while True:
    for sender in ('stefan', 'adrija'):
        if os.getenv(sender.upper() + '_EMAIL') and os.getenv(sender.upper() + '_PASSWORD'):
            try:
                result = sync(sender)
                if result['matched']: print(f"{sender}: {result['matched']} new replies", flush=True)
            except Exception as exc:
                print(f'{sender}: sync failed: {exc}', flush=True)
    time.sleep(300)
