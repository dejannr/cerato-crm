#!/usr/bin/env python3
"""Local, structured outreach tools for Codex. Run with --help for usage."""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = 'http://127.0.0.1:8000/api'

def request(path, method='GET', payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try: detail = json.load(exc).get('detail', str(exc))
        except Exception: detail = str(exc)
        raise SystemExit(f'API error ({exc.code}): {detail}')
    except urllib.error.URLError as exc:
        raise SystemExit(f'Cannot connect to local CRM. Start ./start.sh first: {exc.reason}')

def read(path):
    return Path(path).read_text(encoding='utf-8').strip()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    ls = commands.add_parser('leads', help='List eligible new leads')
    ls.add_argument('--all-services', action='store_true', help='Include leads without groupage category')
    ls.add_argument('--limit', type=int, default=20)
    show = commands.add_parser('show', help='Show a lead and its message history')
    show.add_argument('lead_id', type=int)
    research = commands.add_parser('research', help='Save research and source links from text files')
    research.add_argument('lead_id', type=int)
    research.add_argument('--findings', required=True, help='UTF-8 text file')
    research.add_argument('--sources', required=True, help='UTF-8 text file with source URLs')
    draft = commands.add_parser('draft', help='Create a draft for review, never send it')
    draft.add_argument('lead_id', type=int)
    draft.add_argument('--sender', required=True, choices=['stefan','adrija'])
    draft.add_argument('--to', required=True)
    draft.add_argument('--subject', required=True)
    draft.add_argument('--body-file', required=True, help='UTF-8 text file containing the email')
    commands.add_parser('review', help='List drafts awaiting review')
    template = commands.add_parser('template', help='Read the editable template for a sender')
    template.add_argument('--sender', choices=['stefan', 'adrija'], default='stefan')
    args=parser.parse_args()
    if args.command == 'leads':
        if args.limit < 1: parser.error('--limit must be positive')
        result=request('/leads?status=new'+('' if args.all_services else '&groupage=true'))[:args.limit]
    elif args.command == 'show': result=request(f'/leads/{args.lead_id}')
    elif args.command == 'research':
        findings, sources = read(args.findings), read(args.sources)
        if not findings or not sources: raise SystemExit('Findings and sources cannot be empty')
        result=request(f'/leads/{args.lead_id}', 'PATCH', {'research':findings,'research_sources':sources})
    elif args.command == 'draft':
        lead=request(f'/leads/{args.lead_id}')
        if lead['status'] != 'new' or lead['messages']: raise SystemExit('Lead is not new or has message history; inspect it before proceeding.')
        if not lead.get('research') or not lead.get('research_sources'): raise SystemExit('Save verified research and source URLs first.')
        result=request('/drafts','POST',{'lead_id':args.lead_id,'sender':args.sender,'recipient':args.to,'subject':args.subject,'body':read(args.body_file)})
    elif args.command == 'template': result=request('/template?sender='+args.sender)
    else: result=request('/messages?status=draft')
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
if __name__ == '__main__': main()
